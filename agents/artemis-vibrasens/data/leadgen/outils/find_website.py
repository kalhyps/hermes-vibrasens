"""Trouve et vérifie le site officiel de chaque entreprise d'un CSV (colonnes id, nom, ville, code_postal, site_web).

Pour chaque entreprise, dans l'ordre, jusqu'à trouver une preuve :
  0. le site donné par la collecte (Overture, liste), s'il y en a un ;
  1. les adresses plausibles tirées du nom (rhonemecanique.fr, lafay-tech.com…), sans moteur de recherche ;
  2. une recherche web par le nom et la ville, avec les services gratuits (Exa, Parallel, puis les moteurs de ddgs) ;
  3. une recherche par l'identifiant officiel seul, qui fait souvent remonter les mentions légales du site ;
  4. si rien n'est encore probant et que la clé Firecrawl est présente : les mêmes recherches avec Firecrawl
     (payant, environ 2 crédits par recherche), réservé ainsi aux cas difficiles ou aux moteurs gratuits saturés.
Devant un sous-domaine (deploy., eshop.…), le domaine principal est essayé d'abord, et retenu à score égal.
Un site est confirmé quand l'identifiant officiel de l'entreprise (SIREN, numéro d'entreprise belge, numéro
Companies House) apparaît sur l'accueil ou dans les mentions légales (confiance haute). Sinon, il est noté
selon le nom et la localisation trouvés sur la page (confiance moyenne ou basse).

Exemple :
  python find_website.py --entree 1-collecte.csv --sortie sites.csv
Chaque lancement s'arrête de lui-même au bout de 3 minutes environ (--duree-max) : relancer la même
commande reprend là où le traitement s'est arrêté, jusqu'à « restantes : 0 ».
"""
import argparse
import os
import re
import socket
import threading
import time
from urllib.parse import urljoin, urlparse

import lxml.html

import free_search
from common import (LEGAL_WORDS, CsvAppender, PageError, PoliteClient, contains_company_id, country_code,
                    domain_matches, domain_of, fiche_ids, firecrawl_client, firecrawl_report, firecrawl_search,
                    is_social_site, name_tokens, normalize, page_text, page_title, read_rows, rewrite_csv, root_domain,
                    run_with_budget, search_locale)

FIELDS = ["id", "nom", "ville", "site_web", "confiance", "score", "preuve", "candidats", "erreur"]

# Annuaires, réseaux sociaux, sites d'emploi, presse, avis : jamais le site officiel d'une entreprise.
NOT_OFFICIAL = (
    "societe.com", "pappers", "annuaire", "data.gouv", "gouv.fr", "infogreffe", "verif.com", "manageo",
    "pagesjaunes", "kompass", "europages", "linkedin", "facebook", "instagram", "twitter", "x.com",
    "youtube", "tiktok", "pinterest", "wikipedia", "wikiwand", "indeed", "welcometothejungle", "hellowork",
    "monster.", "apec.fr", "francetravail", "pole-emploi", "jobteaser", "glassdoor", "societeinfo",
    "corporama", "b2bhint", "lefigaro", "bodacc", "score3", "infonet", "dnb.com", "rubypayeur", "118000",
    "118712", "justacote", "cylex", "hoodspot", "firmania", "infobel", "hotfrog", "opencorporates",
    "zoominfo", "rocketreach", "crunchbase", "lusha", "apollo.io", "mappy", "mapquest", "google.", "bing.",
    "yahoo.", "tripadvisor", "yelp", "trustpilot", "scamadviser", "doctrine", "l-entreprise", "usinenouvelle",
    "usine-digitale", "leprogres", "ouest-france", "lesechos", "lyon-entreprises", "amazon.", "leboncoin",
    "trouver-un-artisan", "habitatpresto", "starofservice", "kelentreprise", "net1901", "zoneindustrie",
    "mecaweb", "shein", "temu", "aliexpress", "numlookup", "numtrace", "sync.me", "spokeo",
    "cataloxy", "lagazettefrance", "rne-entreprise", "fichesociete", "bilansgratuits", "entreprises.lefigaro",
    "societeinfo", "dirigeant", "infoentreprise", "pagesdor", "e-pro.fr", "polyvia.fr", "allianceplasturgie",
    # États-Unis, Royaume-Uni, Belgique, Allemagne, Suisse, Italie, Espagne : annuaires et sites d'emploi
    "manta.com", "bizapedia", "buzzfile", "corporationwiki", "yellowpages", "bbb.org", "thomasnet", "ziprecruiter",
    "simplyhired", "careerbuilder", "chamberofcommerce", "opengovus", "bloomberg", "zoominfo", "dandb",
    "endole", "company-information.service.gov.uk", "companycheck", "yell.com", "192.com", "thomsonlocal",
    "trends.be", "companyweb.be", "kbopub", "goudengids", "pagesdor.be", "northdata", "firmenwissen",
    "dasoertliche", "gelbeseiten", "11880", "wlw.de", "moneyhouse", "local.ch", "paginegialle",
    "paginasamarillas", "einforma", "axesor", "infoempresa", "allbiz", "truckingdatabase", "carriersource",
    "dot.gov", "sec.gov", "sam.gov", "usaspending", "govtribe", "highergov", "nextdoor", "angi.com", "homeadvisor",
    "thumbtack", "houzz", "superpages", "whitepages", "loc8nearme", "bizjournals", "industrynet", "macraesbluebook",
    "iqsdirectory", "globalspec", "signalhire", "datanyze", "cybo.com", "brownbook", "find-us-here", "showmelocal",
    "merchantcircle", "bizstanding", "manufacturerstoday", "mfgpages", "linkedin", "zaubacorp",
)
STRONG = 80  # nom dans l'adresse, et nom et localisation sur la page : inutile de chercher plus loin
# Un mot présent dans le nom d'au moins 3 entreprises de la campagne ne les distingue plus ; un domaine proposé par la
# recherche pour au moins 3 entreprises différentes est un annuaire.
SHARED_BY = 3
SHARED_PER = 1000  # dans un vivier de plus de 3 000 noms, le seuil monte d'un cran par tranche de 1 000
WITHOUT_ID_MAX = 95  # meilleur score possible sans le numéro officiel sur le site
# Pages légales, où figure l'identifiant officiel : France, Belgique, Royaume-Uni, Allemagne, Espagne, Italie, Pays-Bas.
LEGAL_LINK = re.compile(r"mentions|legal|l[eé]gales|impressum|cgv|conditions|terms|aviso|note-legali|colofon"
                        r"|disclaimer|company-information|wettelijke", re.I)


# Après plusieurs recherches vides d'affilée, les moteurs gratuits nous limitent : on cesse de les solliciter
# pour ce lancement, et les entreprises concernées sont marquées pour être reprises avec --reessayer-erreurs.
SEARCH_BREAKER = 4
SEARCH_UNAVAILABLE = "recherche web indisponible"


def retryable(row):
    """Sans site faute de moteur de recherche, ou parce qu'aucun candidat n'a répondu (réseau saturé, panne) :
    l'entreprise est reprise par --reessayer-erreurs."""
    errors = [e for e in (row.get("erreur") or "").split(" | ") if e]
    return not row.get("site_web") and bool(errors) and (
        SEARCH_UNAVAILABLE in errors or all("injoignable" in e for e in errors))
_search_state = {"empty_in_a_row": 0, "skipped": 0}
_search_lock = threading.Lock()


def search(query, country, paid=False):
    """Résultats de recherche : services gratuits (free_search : Exa, Parallel, ddgs), ou Firecrawl avec paid=True.
    None si le moteur demandé n'est pas disponible (moteurs gratuits saturés, pas de clé, budget atteint)."""
    if paid:
        return firecrawl_search(query, country)
    with _search_lock:
        if _search_state["empty_in_a_row"] >= SEARCH_BREAKER:
            _search_state["skipped"] += 1
            return None
    results = free_search.search(query, search_locale(country)[0], max_results=10)
    refused = results is None  # Exa, Parallel et les moteurs de ddgs saturés
    results = results or []
    with _search_lock:
        _search_state["empty_in_a_row"] = _search_state["empty_in_a_row"] + 1 if refused else 0
    return results


def resolves(domain):
    try:
        socket.getaddrinfo(domain, 443)
        return True
    except OSError:
        return False


def full_label(name):
    """Le nom entier accolé, mots courts compris, sans la forme juridique : « BF INJECTION » -> « bfinjection »,
    « AG PLAST » -> « agplast », « PLASTIQUES ZD SARL » -> « plastiqueszd »."""
    words = normalize(re.sub(r"\([^)]*\)", "", name)).split()
    while len(words) > 1 and words[-1] in LEGAL_WORDS:
        words.pop()
    while len(words) > 1 and words[0] in LEGAL_WORDS and len(words[0]) > 2:
        words.pop(0)
    return "".join(words)


def guessed_domains(name, country):
    """Adresses plausibles tirées du nom et du sigle entre parenthèses, avec les extensions du pays,
    limitées à celles qui existent. Un mot courant du secteur seul (« injection.fr ») n'est pas essayé."""
    whole = full_label(name)
    labels = [whole] if len(whole) >= 4 and whole not in common_words() else []
    for tokens in [name_tokens(re.sub(r"\([^)]*\)", "", name))] + [name_tokens(s) for s in re.findall(r"\(([^)]*)\)", name)]:
        if not tokens:
            continue
        for label in ("".join(tokens), "-".join(tokens), tokens[0] if len(tokens[0]) >= 5 else ""):
            if len(label) >= 4 and label not in labels and label not in common_words():
                labels.append(label)
    domains = [f"{label}.{tld}" for label in labels[:4] for tld in search_locale(country)[2]]
    return [(d, f"https://{d}/") for d in domains if resolves(d)]


# Sous-domaines de copies de travail d'un site : on donne plutôt l'adresse du site principal.
STAGING = {"deploy", "staging", "stage", "preprod", "pre-prod", "dev", "develop", "test", "tests", "recette", "demo",
           "beta", "old", "new", "v2", "wip", "tmp", "temp", "sandbox", "qa", "uat", "preview"}


# Plateformes d'hébergement de sites : « acme.wixsite.com » est le site d'Acme, mais « wixsite.com » n'est celui de personne.
HOSTING = ("wixsite.com", "wix.com", "wordpress.com", "jimdofree.com", "jimdo.com", "jimdosite.com", "webnode.fr",
           "webnode.com", "blogspot.com", "squarespace.com", "weebly.com", "site123.me", "business.site", "sites.google.com",
           "free.fr", "wanadoo.fr", "orange.fr", "e-monsite.com", "over-blog.com", "simplesite.com", "webflow.io",
           "godaddysites.com", "ueniweb.com", "strikingly.com", "1and1.fr", "ionos.fr", "netlify.app", "github.io")


def with_root(candidates):
    """Devant un sous-domaine (deploy., eshop., intramet.…), le domaine principal : à score égal, c'est lui
    qui est retenu, et le sous-domaine seulement s'il est plus probant. Pas pour une plateforme d'hébergement."""
    result = []
    for domain, url in candidates:
        root = root_domain(domain)
        if root != domain and root not in [d for d, _ in result] and not domain_matches(root, HOSTING):
            result.append((root, f"https://www.{root}/" if resolves(f"www.{root}") else f"https://{root}/"))
        result.append((domain, url))
    return result


# Annuaires propres à la campagne (association locale, salon, presse régionale) : un domaine que la recherche propose
# pour plusieurs entreprises différentes, sans être retenu pour aucune, n'est le site d'aucune d'elles.
_seen_for = {}  # domaine -> entreprises pour lesquelles la recherche l'a proposé
_chosen = set()  # domaines retenus comme site d'une entreprise
_directory_lock = threading.Lock()


def remember_candidates(company_id, domains, chosen=""):
    with _directory_lock:
        for domain in domains:
            _seen_for.setdefault(domain, set()).add(company_id)
        if chosen:
            _chosen.add(domain_of(chosen))
            _chosen.add(root_domain(domain_of(chosen)))


def looks_like_directory(domain):
    if domain_matches(domain, HOSTING):
        return False
    with _directory_lock:
        return domain not in _chosen and len(_seen_for.get(domain, ())) >= shared_by()


def candidates_from(queries, country, pause, exclude=(), unavailable=None, paid=False):
    """Domaines candidats, dans l'ordre des résultats, sans les annuaires, les doublons ni les domaines exclus.
    Si une recherche n'a pas pu être faite, ajoute un drapeau dans la liste `unavailable`."""
    found = []
    for query in queries:
        results = search(query, country, paid)
        if results is None:
            if unavailable is not None:
                unavailable.append(True)
            break
        for result in results:
            url = result.get("href") or ""
            domain = domain_of(url)
            if (not domain or domain in exclude or domain_matches(domain, NOT_OFFICIAL)
                    or looks_like_directory(domain) or looks_like_directory(root_domain(domain))):
                continue
            if domain not in [d for d, _ in found]:
                found.append((domain, f"{urlparse(url).scheme or 'https'}://{urlparse(url).netloc}/"))
        if not paid:
            time.sleep(pause)  # les moteurs gratuits saturent vite ; Firecrawl gère son propre débit
        if len(found) >= 3:
            break
    return with_root(found[:4])


def legal_page_url(html, base_url):
    try:
        tree = lxml.html.fromstring(html)
    except (lxml.etree.ParserError, ValueError):
        return None
    for anchor in tree.iter("a"):
        href = anchor.get("href") or ""
        label = " ".join(anchor.text_content().split())
        if LEGAL_LINK.search(label) or LEGAL_LINK.search(href):
            url = urljoin(base_url, href)
            if domain_of(url) == domain_of(base_url):
                return url
    return None


# Mots trop courants pour identifier une entreprise à eux seuls dans une adresse web. S'y ajoutent les mots que
# plusieurs entreprises du vivier ont dans leur nom (« injection », « plastiques » dans une campagne de plasturgie) :
# voir common_words().
COMMON_WORDS = {
    "production", "productions", "mecanique", "mecaniques", "industrie", "industries", "industriel",
    "industrielle", "service", "services", "technologie", "technologies", "precision", "solution", "solutions",
    "maintenance", "usinage", "atelier", "ateliers", "systeme", "systemes", "equipement", "equipements",
    "engineering", "ingenierie", "international", "europe", "hydraulique", "metal", "metaux", "fabrication",
    "construction", "concept", "innovation", "rhone", "lyon", "lyonnaise", "beaujolais", "beaujolaise", "alpes",
}


_common = {"words": None, "names": 0}


def shared_by():
    """À partir de combien d'entreprises un mot ou un domaine n'en désigne plus une seule : SHARED_BY dans un petit
    vivier, un peu plus dans un très grand (dans 9 000 ateliers de l'Ohio, plusieurs portent « Adams » ou « Acme »)."""
    return max(SHARED_BY, -(-_common["names"] // SHARED_PER))


def load_common_words(paths):
    """Mots que plusieurs entreprises de la campagne ont dans leur nom : ils ne désignent aucune d'elles."""
    counts = {}
    for path in paths:
        if not os.path.exists(path):
            continue
        names = {r.get("nom", "") for r in read_rows(path)}
        for name in names:
            for token in set(name_tokens(name)):
                counts[token] = counts.get(token, 0) + 1
        break  # le premier fichier présent suffit : le vivier s'il existe, sinon la liste envoyée
    _common["names"] = len(names) if counts else 0
    _common["words"] = COMMON_WORDS | {t for t, n in counts.items() if n >= shared_by()}


def common_words():
    if _common["words"] is None:
        load_common_words(["vivier.csv", "1-collecte.csv"])
    return _common["words"]


def name_parts(name):
    """(mots du nom hors parenthèses, sigles entre parenthèses)"""
    main = name_tokens(re.sub(r"\([^)]*\)", "", name))
    acronyms = [t for inner in re.findall(r"\(([^)]*)\)", name) for t in name_tokens(inner)]
    return main, acronyms


# Mots de liaison et formes juridiques qu'une adresse web peut contenir ou omettre : « unitedtoolandmachineinc »
FILLERS = LEGAL_WORDS | {"co", "and", "und", "en", "y", "e", "of", "group", "grp", "holding", "intl"}


def name_words(name):
    """Mots du nom dans l'ordre, lettres isolées regroupées (« D-A-M Machine » -> dam, machine), puis les sigles
    entre parenthèses."""
    words = []
    for part in (re.sub(r"\([^)]*\)", " ", name), " ".join(re.findall(r"\(([^)]*)\)", name))):
        letters = []
        for token in normalize(part).split():
            if len(token) == 1 and token.isalpha():
                letters.append(token)
                continue
            if letters:
                words.append("".join(letters))
                letters = []
            words.append(token)
        if letters:
            words.append("".join(letters))
    return words


def segment(flat, words):
    """Découpe l'adresse en morceaux pris dans le nom, dans l'ordre : mots entiers, initiales, mots de liaison.
    Renvoie (mots entiers, initiales) du découpage qui garde le plus de mots entiers, ou None."""
    if len(flat) > 60:
        return None
    memo = {}

    def best(i, j):
        if i == len(flat):
            return [], []
        if (i, j) in memo:
            return memo[(i, j)]
        options = []
        for filler in FILLERS:
            if flat.startswith(filler, i) and len(filler) > 1:
                rest = best(i + len(filler), j)
                if rest is not None:
                    options.append(rest)
        for k in range(j, len(words)):
            word = words[k]
            if flat.startswith(word, i):
                rest = best(i + len(word), k + 1)
                if rest is not None:
                    options.append(([word] + rest[0], rest[1]))
            if word[0] == flat[i]:
                rest = best(i + 1, k + 1)
                if rest is not None:
                    options.append((rest[0], [word[0]] + rest[1]))
        result = max(options, key=lambda o: (len(o[0]), -len(o[1]))) if options else None
        memo[(i, j)] = result
        return result

    return best(0, 0)


def name_in_domain(name, domain):
    """Le nom désigne-t-il cette adresse ? 2 : l'adresse se lit entièrement avec les mots du nom (« dammachine »,
    « unitedtoolandmachineinc », « plastiqueszd ») et contient ses mots distinctifs, ou c'est son sigle ;
    1 : une partie seulement (« georges.fr » pour « Établissements Georges David », « atlasfasteners » pour
    « Atlas Bolt & Screw ») ; 0 : non. Un mot courant du secteur seul (« injection.fr » pour « BF Injection ») ne
    compte pas."""
    label = root_domain(domain).split(".")[0]
    flat = label.replace("-", "")
    if not flat:
        return 0
    main, acronyms = name_parts(name)
    if flat == full_label(name) or any(a == flat or a in label.split("-") for a in acronyms):
        return 2
    distinctive = [t for t in main if t not in common_words()]
    pieces = segment(flat, name_words(name))
    if pieces is not None:
        full, initials = pieces
        if distinctive and set(distinctive) <= set(full):
            return 2
        words = name_words(name)
        if not distinctive and words and words[0] in full and len(full) >= 2:
            return 2  # nom fait de mots courants : son premier mot et un autre (pas « precisionmachining » pour « Acme… »)
        own = [w for w in full if w not in common_words() and w not in FILLERS]
        if own and initials and len(full) + len(initials) >= 3:
            return 2  # mot propre et initiales (« kbmtf » pour « KB Machine Tool Fab »)
        if own or len(initials) >= 2:
            return 1  # une partie du nom, ou des initiales seules (« apm.com ») : à confirmer par la page
    words = name_words(name)
    if any(len(t) >= 4 and t in flat for t in distinctive) or (words and len(words[0]) >= 4 and flat.startswith(words[0])):
        return 1
    return 0


def name_on_page(name, text):
    """Tous les mots du nom (ou un sigle) figurent comme mots entiers dans le texte, ou le nom entier écrit
    autrement (« D-A-M Machine & Tool » sur la page pour « DAM MACHINE TOOL »)."""
    padded = f" {text} "
    main, acronyms = name_parts(name)
    if bool(main) and all(f" {t} " in padded for t in main) or any(f" {a} " in padded for a in acronyms):
        return True
    whole = full_label(name)
    return len(whole) >= 8 and whole in text.replace(" ", "")


REGISTRY_LABELS = {"siren": "SIREN", "kbo": "numéro d'entreprise", "bce": "numéro d'entreprise",
                   "companies_house": "numéro Companies House"}
# Registres d'entreprises dont le numéro figure couramment sur les sites (mentions légales, pied de page) : seuls
# ceux-là servent à prouver un site ou à le chercher. Un numéro d'installation (EPA aux États-Unis), un DUNS ou un
# numéro fiscal rarement publié n'apparaît pas sur les sites : chercher ce numéro serait une recherche perdue.
WEB_VISIBLE_REGISTRIES = {
    "siren", "siret", "rcs", "kbo", "bce", "companies", "house", "crn", "kvk", "handelsregister", "hrb", "hra",
    "cif", "nif", "nipc", "piva", "iva", "rea", "uid", "che", "cvr", "orgnr", "ytunnus", "krs", "nip", "cro", "abn",
    "acn", "ico", "tva", "vat", "nzbn", "cnpj", "rut", "ruc", "zefix", "brreg", "bolagsverket", "ariregister",
}


def official_id(row):
    """(identifiant officiel, pays, libellé), ou (None, pays, None) quand la ligne n'en a pas (base de lieux, liste)
    ou quand son numéro n'est pas de ceux qu'on trouve sur un site.

    Format commun de la collecte : colonnes id_registre et registre. Les anciennes campagnes n'ont que l'id,
    qui est alors le numéro officiel, sauf pour les lieux d'Overture."""
    country = country_code(row.get("pays"))
    if "id_registre" in row:
        number = (row.get("id_registre") or "").strip()
        registry = (row.get("registre") or "").strip().lower()
        if not number or not set(re.split(r"[^a-z0-9]+", registry)) & WEB_VISIBLE_REGISTRIES:
            return None, country, None
        return number, country, REGISTRY_LABELS.get(registry, "SIREN" if country == "FR" else "numéro d'entreprise")
    if row.get("source", "").startswith("overture") or not re.fullmatch(r"\d{6,}", row.get("id", "")):
        return None, country, None
    return row["id"], country, "SIREN" if country == "FR" else "numéro d'entreprise"


# Domaine abandonné, parqué ou à vendre : la page ne dit rien de l'entreprise, même si l'adresse porte son nom.
PARKED = re.compile(r"domain (name )?(is |may be )?for sale|buy this domain|this domain (is|has been) (parked|registered)"
                    r"|parked (free|domain)|domaine (est )?[àa] vendre|ce nom de domaine|hugedomains|sedo(parking)?\.com"
                    r"|dan\.com|afternic|domain parking|godaddy\.com/forsale|bodis\.com"
                    # page par défaut d'un hébergeur, index vide, site suspendu ou en construction
                    r"|^\s*index of /|welcome to nginx|apache2? .{0,20}default page|default web ?site page|it works!"
                    r"|account (has been )?suspended|site (is )?(en|under) construction|website (is )?coming soon"
                    r"|future home of|cpanel|plesk default|this site is currently unavailable", re.I | re.M)


CONTACT_LINK = re.compile(r"contact|kontakt|contacto|contatti|about|a-propos|qui-sommes|location|find-us|nous-trouver"
                          r"|acces|plan-d-acces|impressum|mentions|legal|over-ons", re.I)


def whole_text(html):
    """Tout le texte de la page, pied de page compris (l'adresse y est souvent), sans scripts ni styles."""
    try:
        tree = lxml.html.fromstring(html)
    except (lxml.etree.ParserError, ValueError):
        return ""
    for node in tree.xpath("//script|//style|//noscript"):
        node.drop_tree()
    return " ".join(tree.text_content().split())


def link_to(html, base_url, pattern):
    """Première page du même site dont le lien ou le libellé répond au motif."""
    try:
        tree = lxml.html.fromstring(html)
    except (lxml.etree.ParserError, ValueError):
        return None
    for anchor in tree.iter("a"):
        href = anchor.get("href") or ""
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        label = " ".join(anchor.text_content().split())
        if pattern.search(label) or pattern.search(href):
            url = urljoin(base_url, href).split("#")[0]
            if domain_of(url) == domain_of(base_url) and url.rstrip("/") != base_url.rstrip("/"):
                return url
    return None


def location_on(row, text):
    """(points, raison) si le code postal (20) ou, à défaut, la ville (15) de l'entreprise figure dans le texte."""
    postcode = (row.get("code_postal") or "").strip()
    if postcode and re.search(rf"(?<![\w]){re.escape(postcode)}(?![\w])", text):
        return 20, "code postal"
    city = normalize(re.split(r"\d", row.get("ville") or "")[0]).replace(" cedex", "").strip()
    if len(city) >= 3 and f" {city} " in f" {normalize(text)} ":
        return 15, "ville"
    return 0, ""


def check_candidate(client, row, home_url, from_source=False):
    """Note un site candidat. Renvoie (score, preuve, url_finale). from_source : la collecte donnait ce site
    pour l'entreprise (base de lieux, liste), ce qui compte comme un indice.

    La localisation est cherchée dans tout l'accueil (pied de page compris), puis dans les mentions légales ou la
    page de contact : c'est elle qui départage deux homonymes (« United Tool & Machine » dans l'Ohio et dans le
    Massachusetts)."""
    final_url, html = client.get(home_url)
    text = page_text(html)
    full = whole_text(html)
    company_id, country, label = official_id(row)
    if company_id and contains_company_id(html if country == "FR" else full, company_id, country):
        return 100, f"{label} trouvé sur {final_url}", final_url
    if PARKED.search(page_title(html)) or (len(full) < 1500 and PARKED.search(full)):
        return 0, "domaine parqué, à vendre ou page par défaut", final_url
    points, where = location_on(row, full)
    found_on = "la page" if points else ""
    if company_id or not points:
        # Mentions légales (numéro officiel) ou, à défaut de localisation sur l'accueil, page de contact.
        second = (legal_page_url(html, final_url) if company_id else None) or link_to(html, final_url, CONTACT_LINK)
        if second:
            try:
                _, second_html = client.get(second)
                if company_id and contains_company_id(second_html if country == "FR" else whole_text(second_html),
                                                      company_id, country):
                    return 100, f"{label} trouvé sur {second}", final_url
                if not points:
                    points, where = location_on(row, whole_text(second_html))
                    found_on = "la page de contact ou les mentions légales" if points else ""
            except PageError:
                pass
    page = normalize(page_title(html) + " " + text)
    score, reasons = 0, []
    in_domain = name_in_domain(row["nom"], domain_of(final_url))
    if in_domain == 2:
        score += 40
        reasons.append("nom dans le domaine")
    elif in_domain == 1:
        score += 20
        reasons.append("une partie du nom dans le domaine")
    if name_on_page(row["nom"], page) or name_on_page(row["nom"], normalize(full)):
        score += 25
        reasons.append("nom sur la page")
    if points:
        score += points
        reasons.append(f"{where} sur {found_on}")
    if from_source and (points or "nom sur la page" in reasons):
        # La source compte seulement si la page elle-même parle de l'entreprise : une adresse qui porte son nom
        # mais n'affiche plus rien d'elle (site abandonné, homonyme) ne gagne rien à être citée par la source.
        score += 20
        reasons.append("site indiqué par la source de collecte")
    # 100 et la confiance « haute » sont réservés à la preuve par le numéro officiel.
    return min(score, WITHOUT_ID_MAX), ", ".join(reasons) or "aucun indice", final_url


def process(row, client, pause):
    """Adresses devinées, puis recherche par le nom, puis recherche par l'identifiant officiel,
    jusqu'à une preuve par cet identifiant."""
    result = {"id": row["id"], "nom": row["nom"], "ville": row.get("ville", ""), "site_web": "",
              "confiance": "aucune", "score": 0, "preuve": "", "candidats": "", "erreur": ""}
    name = re.sub(r"\s*\([^)]*\)", "", row["nom"]).strip() or row["nom"]  # « TECNYX (TECNYX) » -> « TECNYX »
    company_id, country, _ = official_id(row)
    suffix = search_locale(country)[1]
    best, errors, tested, scores = None, [], [], {}

    def evaluate(candidates, min_score=0):
        nonlocal best
        for domain, url in candidates:
            if domain not in tested:
                tested.append(domain)
                try:
                    scores[domain] = check_candidate(client, row, url, from_source=domain in given_domains)
                except PageError as exc:
                    errors.append(f"{domain} : {exc}")
                    continue
            scored = scores.get(domain)
            if scored is None:
                continue
            if scored[0] >= min_score and (best is None or scored[0] > best[0]):
                best = scored
            if scored[0] >= 100:
                return

    def good_enough(threshold):
        return best is not None and best[0] >= threshold

    def tried_in_vain():
        """Domaines déjà essayés sans indice : inutile que la recherche les repropose. Une adresse devinée qui a
        le nom de l'entreprise (40 points) reste permise : si la recherche la donne aussi, elle est retenue."""
        return {d for d in tested if d not in scores or scores[d][0] < 40}

    unavailable = []
    city = row.get("ville", "")
    name_queries = [f'"{name}" {city}'.strip(), f'{name} {city} {suffix}'.strip()]
    given_domains = set()
    if row.get("site_web") and not is_social_site(row["site_web"]):
        # Le site donné par la source (base de lieux, liste) : retenu s'il est probant, sinon on cherche plus loin.
        given = (row["site_web"] if "://" in row["site_web"] else "https://" + row["site_web"]).strip()
        given_domains = {domain_of(given), root_domain(domain_of(given))}
        evaluate(with_root([(domain_of(given), given)]))
    if not good_enough(60):
        # Une adresse devinée n'est retenue que sur des indices solides, pour éviter les homonymes.
        evaluate(guessed_domains(row["nom"], country), min_score=60)
    if not good_enough(STRONG):
        evaluate(candidates_from(name_queries, country, pause, tried_in_vain(), unavailable))
    if not good_enough(60) and company_id:
        evaluate(candidates_from([f'"{company_id}"'], country, pause, tried_in_vain(), unavailable))
    # Firecrawl, payant, seulement pour ce que les moteurs gratuits n'ont pas résolu.
    if not good_enough(60) and firecrawl_client() is not None:
        paid_unavailable = []
        evaluate(candidates_from(name_queries[:1], country, pause, tried_in_vain(), paid_unavailable, paid=True))
        if best is None and company_id:  # rien du tout par le nom : l'identifiant fait remonter les mentions légales
            evaluate(candidates_from([f'"{company_id}"'], country, pause, tried_in_vain(), paid_unavailable, paid=True))
        if not paid_unavailable:
            unavailable.clear()  # Firecrawl a cherché à la place des moteurs gratuits : la recherche est complète
    result["candidats"] = " ; ".join(tested)
    if best and best[0] >= 40:
        score, proof, final_url = best
        host = domain_of(final_url)
        main_site = scores.get(root_domain(host))
        if host.split(".")[0] in STAGING and main_site and main_site[0] >= 40:
            # La copie de test prouve l'identité ; on donne l'adresse du site principal.
            proof, final_url = f"{proof} (vérifié sur {host}, copie de test du site)", main_site[2]
        result.update(site_web=f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}", score=score,
                      preuve=proof, confiance="haute" if score >= 100 else "moyenne" if score >= 60 else "basse")
    if unavailable and not good_enough(60):
        errors.insert(0, SEARCH_UNAVAILABLE)
    result["erreur"] = " | ".join(errors)
    remember_candidates(row["id"], tested, result["site_web"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--entree", default="1-collecte.csv")
    parser.add_argument("--sortie", default="sites.csv")
    parser.add_argument("--max", type=int, default=0, help="nombre maximal d'entreprises à traiter dans ce lancement")
    parser.add_argument("--ids", help="identifiants à traiter, séparés par des virgules")
    parser.add_argument("--pause", type=float, default=1.5, help="pause en secondes après chaque recherche web")
    parser.add_argument("--paralleles", type=int, default=4, help="entreprises traitées en même temps")
    parser.add_argument("--duree-max", type=int, default=180,
                        help="secondes après lesquelles l'outil ne commence plus d'entreprise (0 = sans limite)")
    parser.add_argument("--reessayer-erreurs", action="store_true",
                        help="reprendre les entreprises restées sans site parce que la recherche web était "
                             "indisponible ou qu'aucun site candidat n'a répondu")
    args = parser.parse_args()

    if args.reessayer_erreurs:
        # Les entreprises qui ont déjà leur fiche d'enrichissement (vagues précédentes) restent comme elles sont.
        finished = fiche_ids("enrichissement")
        rewrite_csv(args.sortie, FIELDS, lambda r: not retryable(r) or r["id"] in finished)
    rows = read_rows(args.entree)
    load_common_words(["vivier.csv", args.entree])
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        rows = [r for r in rows if r["id"] in wanted]
    if os.path.exists(args.sortie):  # domaines déjà vus lors des lancements précédents
        for previous in read_rows(args.sortie):
            remember_candidates(previous["id"], [d.strip() for d in previous.get("candidats", "").split(";") if d.strip()],
                                previous.get("site_web", ""))
    output = CsvAppender(args.sortie, FIELDS)
    todo = [r for r in rows if r["id"] not in output.done]
    if args.max:
        todo = todo[: args.max]
    client = PoliteClient(delay=1.0)
    counts = {"haute": 0, "moyenne": 0, "basse": 0, "aucune": 0}

    unreachable = []

    def handle(result):
        counts[result["confiance"]] += 1
        if retryable(result) and SEARCH_UNAVAILABLE not in result["erreur"]:
            unreachable.append(result["id"])
        output.write(result)
        print(f'{result["id"]} {result["nom"][:40]:40} -> {result["site_web"] or "-"} ({result["confiance"]})', flush=True)

    processed = run_with_budget(todo, lambda row: process(row, client, args.pause), args.paralleles, args.duree_max, handle)
    remaining = len([r for r in rows if r["id"] not in output.done])
    print(f"Traitées : {processed} ; confiance haute {counts['haute']}, moyenne {counts['moyenne']}, "
          f"basse {counts['basse']}, aucun site {counts['aucune']} ; restantes : {remaining}")
    if remaining:
        print("Arrêt volontaire pour rester sous le délai des commandes : relancez la même commande pour continuer.")
    if len(unreachable) >= 3:
        print(f"{len(unreachable)} entreprises sans site parce qu'aucun candidat n'a répondu : le réseau était peut-être "
              "saturé. Quand il ne reste plus d'entreprises, relancez une fois avec --reessayer-erreurs.")
    if _search_state["skipped"]:
        print(f"Moteurs de recherche indisponibles pendant ce lancement ({_search_state['skipped']} recherches sautées). "
              "Quand il ne reste plus d'entreprises, relancez une fois avec --reessayer-erreurs.")
    if firecrawl_report():
        print(firecrawl_report())


if __name__ == "__main__":
    main()
