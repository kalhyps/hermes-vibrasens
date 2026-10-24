"""Fonctions partagées par les outils leadgen : HTTP poli, robots.txt, texte, CSV, pays, Firecrawl."""
import csv
import fcntl
import json
import os
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib import robotparser
from urllib.parse import urlparse

import httpx
import lxml.html
import trafilatura

USER_AGENT = os.environ.get("LEADGEN_USER_AGENT", "Mozilla/5.0 (compatible; LeadgenBot/0.1)")
ROBOTS_AGENT = "LeadgenBot"
MAX_CHARS = 2_000_000

# Mots qui n'identifient pas une entreprise : formes juridiques (France et étranger) et mots vides.
LEGAL_WORDS = {
    "sarl", "sas", "sasu", "eurl", "sa", "snc", "sci", "scop", "selarl", "sarlu", "ets",
    "etablissements", "etablissement", "societe", "ste", "groupe", "france", "compagnie", "cie",
    "fils", "freres", "et", "de", "des", "du", "la", "le", "les", "en", "sur", "aux", "the",
    "ltd", "limited", "plc", "llp", "inc", "llc", "corp", "company", "and", "gmbh", "mbh", "ag", "kg",
    "ug", "ohg", "srl", "spa", "sl", "slu", "bv", "nv", "bvba", "sprl", "srls", "oy", "ab", "aps",
    "zoo", "und", "der", "die", "van", "het", "di", "del", "della", "y",
}

# Pays : nom ou code -> code ISO à deux lettres.
COUNTRY_CODES = {
    "france": "FR", "belgique": "BE", "belgium": "BE", "royaume uni": "GB", "united kingdom": "GB", "uk": "GB",
    "angleterre": "GB", "allemagne": "DE", "germany": "DE", "espagne": "ES", "spain": "ES", "italie": "IT",
    "italy": "IT", "suisse": "CH", "switzerland": "CH", "luxembourg": "LU", "pays bas": "NL",
    "netherlands": "NL", "portugal": "PT", "irlande": "IE", "ireland": "IE", "autriche": "AT", "austria": "AT",
    "etats unis": "US", "etats unis d amerique": "US", "united states": "US", "united states of america": "US",
    "usa": "US", "deutschland": "DE", "espana": "ES", "italia": "IT", "nederland": "NL", "belgie": "BE",
    "schweiz": "CH", "svizzera": "CH", "osterreich": "AT", "canada": "CA", "pologne": "PL", "poland": "PL",
    "polska": "PL", "suede": "SE", "sweden": "SE", "danemark": "DK", "denmark": "DK", "norvege": "NO", "norway": "NO",
    "finlande": "FI", "finland": "FI", "republique tcheque": "CZ", "czechia": "CZ", "maroc": "MA", "morocco": "MA",
    "tunisie": "TN", "tunisia": "TN", "quebec": "CA",
}

# Recherche web par pays : (région de la bibliothèque ddgs, mots ajoutés à la requête, extensions de domaine).
SEARCH_LOCALES = {
    "FR": ("fr-fr", "site officiel", ("fr", "com")),
    "BE": ("be-fr", "site officiel", ("be", "com", "eu")),
    "CH": ("ch-fr", "site officiel", ("ch", "com")),
    "LU": ("fr-fr", "site officiel", ("lu", "com")),
    "GB": ("uk-en", "official website", ("co.uk", "com", "uk")),
    "IE": ("ie-en", "official website", ("ie", "com")),
    "DE": ("de-de", "offizielle Webseite", ("de", "com")),
    "AT": ("at-de", "offizielle Webseite", ("at", "com")),
    "ES": ("es-es", "sitio web oficial", ("es", "com")),
    "IT": ("it-it", "sito ufficiale", ("it", "com")),
    "NL": ("nl-nl", "officiële website", ("nl", "com")),
    "PT": ("pt-pt", "site oficial", ("pt", "com")),
}


# Boîtes génériques d'une entreprise (pas de prénom.nom@) : les seules adresses que les outils gardent. Une boîte peut
# porter une terminaison (« commerciale », « contacts », « salesuk ») et un seul complément court (« info.fr », « sales2 »).
GENERIC_MAILBOXES = ("contact", "info", "information", "accueil", "commercial", "commerce", "vente", "sales", "devis",
                     "admin", "administration", "secretariat", "direction", "rh", "recrutement", "emploi", "jobs",
                     "hello", "bonjour", "atelier", "production", "sav", "service", "serviceclient", "customerservice",
                     "compta", "comptabilite", "achats", "qualite", "office", "mail", "enquiries", "enquiry", "kontakt",
                     "verkauf", "vertrieb", "contacto", "contatti", "ventas", "exploitation", "reception", "export",
                     "logistique", "transport", "planning", "support", "team", "welcome", "general", "mail")
# Boîtes qui ne servent pas à un contact commercial : protection des données, technique, envois automatiques.
NOT_FOR_CONTACT = ("privacy", "dpo", "rgpd", "gdpr", "cnil", "dataprotection", "datenschutz", "informationgovernance",
                   "webmaster", "noreply", "donotreply", "nepasrepondre", "postmaster", "abuse", "hostmaster", "legal",
                   "compliance", "unsubscribe", "newsletter", "bounce", "mailerdaemon")
GENERIC_BOX = re.compile(r"^(?:" + "|".join(sorted(set(GENERIC_MAILBOXES), key=len, reverse=True)) + r")[a-z]{0,4}$")
SHORT_SUFFIX = re.compile(r"^(?:[a-z]{2,3}|\d{1,3})$")  # « fr », « uk », « de », « 2 », « 01 »


def is_generic_local(local):
    """« contact », « commerciale », « info.fr », « sales2 », « contactus » : oui ; « informationgovernance »,
    « info-rgpd », « contact.jean.dupont », « mail.jdupont » : non."""
    local = (local or "").lower()
    if not local:
        return False
    parts = re.split(r"[._-]+", local)
    flat = local.replace("-", "").replace("_", "").replace(".", "")
    if any(p.startswith(NOT_FOR_CONTACT) or p in ("no", "reply") for p in parts) or flat.startswith(NOT_FOR_CONTACT):
        return False
    head = re.sub(r"\d+$", "", parts[0])
    if not GENERIC_BOX.match(head):
        return False
    return len(parts) == 1 or (len(parts) == 2 and bool(SHORT_SUFFIX.match(parts[1]) or GENERIC_BOX.match(parts[1])))


def is_generic_email(email):
    return "@" in (email or "") and is_generic_local(email.split("@", 1)[0])


def domain_matches(domain, entries):
    """Le domaine appartient-il à une liste ? « x.com » vise x.com et ses sous-domaines (pas apex.com) ; « bing. » vise
    un libellé « bing » (pas ohiotubing.com) ; un mot sans point d'au moins 6 lettres (« pagesjaunes ») est cherché
    dans tout le domaine, un mot plus court seulement comme libellé entier."""
    domain = (domain or "").lower().removeprefix("www.")
    labels = domain.split(".")
    for entry in entries:
        if entry.endswith("."):
            if entry[:-1] in labels:
                return True
        elif "." in entry:
            if domain == entry or domain.endswith("." + entry):
                return True
        elif (len(entry) >= 6 and entry in domain) or entry in labels:
            return True
    return False


# Pages de réseaux sociaux ou de liens : jamais le site officiel d'une entreprise.
SOCIAL_SITES = ("facebook.", "instagram.", "linkedin.", "twitter.", "x.com", "tiktok.", "youtube.", "youtu.be",
                "linktr.ee", "pinterest.", "business.site", "wa.me", "fb.com", "fb.me")


def is_social_site(url):
    value = (url or "").strip()
    return domain_matches(domain_of(value if "://" in value else "http://" + value), SOCIAL_SITES)


def country_code(value):
    """« France », « Belgique », « BE », « GB »… -> code ISO à deux lettres (France par défaut)."""
    text = re.sub(r"[^a-z ]+", " ", unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()).strip()
    if text in COUNTRY_CODES:
        return COUNTRY_CODES[text]
    return text.upper() if len(text) == 2 else "FR"


def status_kind(status):
    """Statut d'une fiche, quelle que soit son écriture : « Qualifiée », « qualifie », « À revoir », « écarté(e) »,
    « Gardée »… -> « qualifié », « à revoir », « écarté », « gardée » ; texte vide si le statut est inconnu."""
    text = normalize(status)
    for prefix, kind in (("qualifi", "qualifié"), ("a revoir", "à revoir"), ("ecart", "écarté"), ("gard", "gardée")):
        if text.startswith(prefix):
            return kind
    return ""


def search_locale(country):
    return SEARCH_LOCALES.get(country, ("wt-wt", "official website", ("com",)))


class PageError(Exception):
    """Page illisible : erreur réseau, code HTTP, robots.txt ou contenu non HTML."""


class PoliteClient:
    """Client HTTP qui respecte robots.txt et attend entre deux requêtes vers un même site."""

    def __init__(self, delay=1.0):
        self.delay = delay
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5"},
            timeout=httpx.Timeout(12.0, connect=6.0),
            follow_redirects=True,
        )
        self._robots = {}
        self._last_call = {}
        self._lock = threading.Lock()

    def _wait(self, host):
        """Réserve le prochain créneau libre sur ce site : sûr même avec plusieurs fils en parallèle."""
        with self._lock:
            now = time.monotonic()
            ready_at = max(now, self._last_call.get(host, 0.0) + self.delay)
            self._last_call[host] = ready_at
        time.sleep(ready_at - now)

    def allowed(self, url):
        """RFC 9309 : robots.txt en 4xx autorise tout ; en 5xx ou injoignable, n'autorise rien.

        Une panne passagère n'est pas un refus : deux essais, et l'échec n'est pas gardé en mémoire,
        pour qu'un appel suivant puisse réussir. Un site qui ne répond pas du tout est abandonné aussitôt.
        Lève PageError si robots.txt reste injoignable.
        """
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = None
            for attempt in range(2):
                try:
                    self._wait(parts.netloc)
                    response = self.client.get(origin + "/robots.txt")
                except httpx.ConnectTimeout as exc:
                    # Le réseau du conteneur peut saturer un instant : un second essai, puis abandon.
                    if attempt == 0:
                        time.sleep(3)
                        continue
                    raise PageError("site injoignable") from exc
                except httpx.ConnectError as exc:
                    raise PageError("site injoignable") from exc  # pas de serveur qui répond : inutile d'insister
                except httpx.HTTPError:
                    time.sleep(2)
                    continue
                if response.status_code >= 500:
                    time.sleep(2)
                    continue
                parser = robotparser.RobotFileParser()
                if response.status_code >= 400:
                    parser.allow_all = True
                else:
                    parser.parse(response.text.splitlines())
                break
            if parser is None:
                raise PageError("robots.txt injoignable, réessayer plus tard")
            self._robots[origin] = parser
        return self._robots[origin].can_fetch(ROBOTS_AGENT, url)

    def get(self, url, allow_xml=False):
        """Renvoie (url_finale, html) ou lève PageError avec une raison courte. allow_xml : accepte aussi le XML (plans de site)."""
        if not self.allowed(url):
            raise PageError("interdit par robots.txt")
        self._wait(urlparse(url).netloc)
        try:
            response = self.client.get(url)
        except httpx.HTTPError as exc:
            raise PageError(type(exc).__name__) from exc
        if response.status_code >= 400:
            raise PageError(f"HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and not (allow_xml and "xml" in content_type):
            raise PageError(f"pas une page HTML ({content_type.split(';')[0] or 'type inconnu'})")
        return str(response.url), response.text[:MAX_CHARS]


# Lignes d'un bandeau de cookies (« Ce site utilise des cookies… ») ; pas « nos cookies au chocolat ».
COOKIE_BANNER = re.compile(r"cookie.*(consent|accept|refus|navig|browser|website|site web|ce site|this site|param"
                           r"|setting|traceur|tracking)|(consent|accept|refus|navig|browser|website|site web|ce site"
                           r"|this site|param|setting|traceur|tracking).*cookie", re.I)


BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "dt", "dd", "div", "section", "article",
              "tr", "br", "blockquote", "figcaption", "address", "header", "footer", "aside", "nav", "ul", "ol",
              "table", "main", "figure", "label", "option"}


def visible_lines(html):
    """Lignes de texte visibles de la page, bloc par bloc (listes, tableaux, chronologies, chiffres clés compris)."""
    try:
        tree = lxml.html.fromstring(html)
    except (lxml.etree.ParserError, ValueError):
        return []
    for node in tree.xpath("//script|//style|//noscript|//svg|//template"):
        node.drop_tree()
    for node in tree.iter():
        if isinstance(node.tag, str) and node.tag.lower() in BLOCK_TAGS:
            node.tail = "\n" + (node.tail or "")
            if node.tag.lower() in ("li", "td", "th", "dt", "dd", "br"):
                node.text = " " + (node.text or "")
    lines = []
    for line in tree.text_content().splitlines():
        line = " ".join(line.split())
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return lines


def page_text(html):
    """Texte d'une page : le texte principal (trafilatura), sans bandeau de cookies, puis les lignes visibles qu'il a
    laissées de côté (listes de machines, chronologies, chiffres clés, pied de page), sans doublon. Une page construite
    en JavaScript donne peu de texte : Firecrawl peut alors la lire (read_website.py)."""
    text = trafilatura.extract(html, favor_recall=True, include_tables=True, include_comments=False) or ""
    text = "\n".join(line for line in text.splitlines() if not COOKIE_BANNER.search(line))
    seen = normalize(text)
    extra = []
    for line in visible_lines(html):
        key = normalize(line)
        if len(key) < 12 or key in seen or COOKIE_BANNER.search(line):
            continue
        seen += " " + key
        extra.append(line)
    return (text + "\n" + "\n".join(extra)).strip() if extra else text


def page_title(html):
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return " ".join(match.group(1).split()) if match else ""


def normalize(text):
    """Minuscules, sans accents ni ponctuation : « Société Lyonnaise » -> « societe lyonnaise »."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def name_tokens(name):
    """Mots distinctifs d'une raison sociale. Les lettres isolées sont regroupées : « A M A P » -> « amap »."""
    merged, letters = [], []
    for token in normalize(name).split():
        if len(token) == 1:
            letters.append(token)
            continue
        if letters:
            merged.append("".join(letters))
            letters = []
        merged.append(token)
    if letters:
        merged.append("".join(letters))
    return [t for t in merged if t not in LEGAL_WORDS and len(t) >= 3]


def domain_of(url):
    host = urlparse(url if "://" in url else "http://" + url).netloc.lower().split(":")[0]
    return host.removeprefix("www.")


# Deuxièmes niveaux réservés (« asso.fr », « co.uk », « com.au ») : le domaine principal a un niveau de plus.
SECOND_LEVELS = {"co", "com", "org", "net", "ac", "asso", "gouv", "gov", "edu", "ltd", "plc", "nom", "sch", "or", "ne"}


def root_domain(domain):
    """« deploy.pernatindustrie.com » -> « pernatindustrie.com », « shop.atelier.co.uk » -> « atelier.co.uk »,
    « aepv.asso.fr » -> « aepv.asso.fr »"""
    labels = domain.split(".")
    keep = 3 if len(labels) >= 3 and labels[-2] in SECOND_LEVELS and len(labels[-1]) == 2 else 2
    return ".".join(labels[-keep:])


def contains_siren(text, siren):
    """Vrai si le SIREN apparaît dans le texte, seul ou dans un SIRET ou un n° de TVA (« FR12 971 506 308 »)."""
    siren = re.sub(r"\D", "", siren or "")
    if len(siren) != 9:
        return False
    for chunk in re.findall(r"\d[\d  .]{7,18}\d", text or ""):
        if siren in re.sub(r"\D", "", chunk):
            return True
    return False


def contains_company_id(text, company_id, country):
    """Vrai si l'identifiant officiel de l'entreprise figure dans le texte.

    France : SIREN, seul ou dans un SIRET ou un n° de TVA. Belgique : numéro d'entreprise (0123.456.789,
    BE0123456789). Royaume-Uni : numéro Companies House, près des mots « company », « registered » ou « no ».
    Ailleurs : le numéro (5 chiffres au moins) juste après le nom de son registre (« HRB 723456 »).
    """
    if not company_id or not text:
        return False
    if country == "FR":
        return contains_siren(text, company_id)
    digits = re.sub(r"\D", "", company_id)
    chunks = [re.sub(r"\D", "", c) for c in re.findall(r"\d[\d  ./-]{4,24}\d", text)]
    if country == "BE" and len(digits) in (9, 10):
        return any(digits.zfill(10)[-9:] in c for c in chunks)
    if country == "GB":
        spaced = r"\s?".join(re.escape(ch) for ch in company_id.upper())
        return re.search(rf"(?i)(compan|regist|\bno\b|number)[^A-Z0-9]{{0,40}}{spaced}(?![0-9])", text) is not None
    if len(digits) < 5:
        return False
    # Le numéro entier, isolé, juste après le nom de son registre (« HRB 723456 », « KvK-nummer 12345678 ») : un
    # numéro seul se retrouve trop facilement dans un téléphone, un code postal ou l'adresse d'une image.
    number = r"[\s  ./-]?".join(re.escape(ch) for ch in digits)
    keyword = (r"(hrb|hra|handelsregister|registergericht|registernummer|kvk|kamer van koophandel|uid|che|cif|nif"
               r"|nipc|iva|vat|ust|registr|company|firmenbuch|fn|rcs|cvr|org\.? ?nr|organisasjonsnummer|y-tunnus|krs"
               r"|regon|nip|i[cč]o|abn|acn|rea|n[°o]\.?)")
    return re.search(rf"(?i)\b{keyword}[^\w\n]{{0,12}}(?:[a-zäöü]{{0,12}}[\s.:-]{{0,4}})?{number}(?![\d])", text) is not None


# Firecrawl : utilisé seulement si la clé est présente. Chaque appel consomme des crédits (environ 2 par recherche,
# 1 par page). La dépense de la campagne est tenue dans firecrawl.json (dossier de la campagne) ; si le brief fixe un
# budget (ligne « Budget Firecrawl : 300 crédits »), les outils cessent d'utiliser Firecrawl une fois ce budget atteint.
_firecrawl = {"client": None, "searches": 0, "pages": 0, "budget_reached": False}
_firecrawl_lock = threading.Lock()
LEDGER = "firecrawl.json"
# « Budget Firecrawl : 300 crédits », « **Budget Firecrawl** : 1 000 », « Budget Firecrawl (crédits) : 300 » ; pas
# « Budget Firecrawl : aucun (au besoin 2 recherches) ».
BUDGET_LINE = re.compile(r"budget\W{1,3}firecrawl\W*?(?:\([^)\n]*\))?[ \t:=*_]*(\d[\d \u00a0\u202f.,']*)", re.I)


def firecrawl_budget():
    """Budget Firecrawl de la campagne, en crédits, lu dans brief.md ; None s'il n'y en a pas."""
    try:
        with open("brief.md", encoding="utf-8") as handle:
            match = BUDGET_LINE.search(handle.read())
    except OSError:
        return None
    if not match:
        return None
    # « 1 000 », « 1.000 », « 1,000 » ; mais « 300 crédits, soit » s'arrête au premier séparateur suivi d'autre chose
    number = re.match(r"\d{1,3}(?:[ \u00a0\u202f.,']\d{3})+(?!\d)|\d+", match.group(1).strip())
    return int(re.sub(r"\D", "", number.group(0))) if number else None


def _ledger():
    try:
        with open(LEDGER, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _record_firecrawl(kind):
    """Compte un appel Firecrawl dans ce lancement et dans firecrawl.json (seulement dans un dossier de campagne).
    Plusieurs outils peuvent tourner en même temps : le fichier est verrouillé pendant sa mise à jour. Un échec de
    la comptabilité ne doit jamais arrêter l'outil."""
    with _firecrawl_lock:
        _firecrawl["searches" if kind == "recherche" else "pages"] += 1
        if not os.path.exists("brief.md"):
            return
        try:
            with open("." + LEDGER + ".verrou", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                ledger = _ledger()
                ledger.setdefault("recherches", 0)
                ledger.setdefault("pages", 0)
                ledger["recherches" if kind == "recherche" else "pages"] += 1
                ledger["credits_estimes"] = 2 * ledger["recherches"] + ledger["pages"]
                tool = os.path.basename(sys.argv[0]).removesuffix(".py") or "outil"
                ledger.setdefault("par_outil", {})
                ledger["par_outil"][tool] = ledger["par_outil"].get(tool, 0) + (2 if kind == "recherche" else 1)
                ledger["budget"] = firecrawl_budget()
                ledger["mis_a_jour"] = time.strftime("%Y-%m-%d %H:%M:%S")
                temporary = f"{LEDGER}.{os.getpid()}.tmp"
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump(ledger, handle, ensure_ascii=False, indent=2)
                os.replace(temporary, LEDGER)
        except OSError as exc:
            print(f"Comptabilité Firecrawl non mise à jour ({exc}).", flush=True)


def firecrawl_spent():
    """Crédits Firecrawl déjà dépensés par la campagne (estimation de firecrawl.json)."""
    return _ledger().get("credits_estimes", 0)


def firecrawl_client():
    """Client Firecrawl, ou None : pas de clé, ou budget de la campagne atteint."""
    # Hermes ne transmet jamais FIRECRAWL_API_KEY (clé d'un de ses fournisseurs) aux commandes de ses agents :
    # dans une tâche Kanban, la clé arrive sous le nom LEADGEN_FIRECRAWL_API_KEY (docker-compose.yml).
    key = os.environ.get("LEADGEN_FIRECRAWL_API_KEY") or os.environ.get("FIRECRAWL_API_KEY")
    if not key or _firecrawl["budget_reached"]:
        return None
    budget = firecrawl_budget()
    if budget is not None and firecrawl_spent() >= budget:
        _firecrawl["budget_reached"] = True
        print(f"BUDGET FIRECRAWL ATTEINT ({budget} crédits, fixé par le brief) : la suite se fait avec les moteurs "
              "gratuits et sans rendu des pages en JavaScript.", flush=True)
        return None
    with _firecrawl_lock:
        if _firecrawl["client"] is None:
            from firecrawl.v2.client import FirecrawlClient
            # Pas de reprises en rafale dans la bibliothèque : _firecrawl_call attend le délai que demande Firecrawl.
            _firecrawl["client"] = FirecrawlClient(api_key=key, timeout=90, max_retries=1)
    return _firecrawl["client"]


# Les offres Firecrawl limitent le nombre de requêtes par minute : un seul appel à la fois, et sur un refus pour
# débit dépassé (429), tous les appels attendent le délai indiqué par Firecrawl avant de reprendre.
_firecrawl_gate = threading.Lock()
RETRY_AFTER = re.compile(r"retry after (\d+)\s*s", re.I)


def _firecrawl_call(method, **kwargs):
    """Appel Firecrawl régulé. Renvoie le résultat, ou None si Firecrawl refuse encore après plusieurs attentes ou si
    le compte n'a plus de crédits. Les autres erreurs remontent à l'appelant."""
    from firecrawl.v2.utils.error_handler import PaymentRequiredError, RateLimitError
    for _ in range(3):
        with _firecrawl_gate:
            budget = firecrawl_budget()
            if _firecrawl["budget_reached"] or (budget is not None and firecrawl_spent() >= budget):
                _firecrawl["budget_reached"] = True  # vérifié à chaque appel : plusieurs fils ne dépassent pas le budget
                return None
            try:
                return method(**kwargs)
            except RateLimitError as exc:
                match = RETRY_AFTER.search(str(exc))
                time.sleep(min(int(match.group(1)) + 1, 65) if match else 30)
            except PaymentRequiredError:
                _firecrawl["budget_reached"] = True
                print("FIRECRAWL : plus de crédits sur le compte ; la suite se fait sans Firecrawl.", flush=True)
                return None
    return None


def firecrawl_search(query, country, limit=8):
    """Résultats [{href, title}] de la recherche Firecrawl, ou None si elle n'est pas disponible."""
    client = firecrawl_client()
    if client is None:
        return None
    try:
        try:
            data = _firecrawl_call(client.search, query=query, limit=limit, country=country)
        except Exception:
            data = _firecrawl_call(client.search, query=query, limit=limit)  # pays non reconnu par Firecrawl
    except Exception:
        return None
    if data is None:
        return None
    _record_firecrawl("recherche")
    results = []
    for item in data.web or []:
        url = getattr(item, "url", None) or getattr(getattr(item, "metadata", None), "url", None)
        if url:
            results.append({"href": url, "title": getattr(item, "title", "") or ""})
    return results


def firecrawl_scrape(url):
    """Texte (Markdown) d'une page rendue par Firecrawl, ou None. Sert aux sites qui exigent un navigateur."""
    client = firecrawl_client()
    if client is None:
        return None
    try:
        document = _firecrawl_call(client.scrape, url=url, formats=["markdown"], only_main_content=True, timeout=60000)
    except Exception:
        return None
    if document is None:
        return None
    _record_firecrawl("page")
    return document.markdown or ""


def firecrawl_usage():
    """Appels Firecrawl de ce lancement (environ 2 crédits par recherche, 1 par page), et total de la campagne."""
    return {"recherches": _firecrawl["searches"], "pages": _firecrawl["pages"],
            "credits_estimes": 2 * _firecrawl["searches"] + _firecrawl["pages"],
            "campagne": firecrawl_spent(), "budget": firecrawl_budget()}


def firecrawl_report():
    """Ligne de fin de lancement sur la dépense Firecrawl, ou chaîne vide si Firecrawl n'a pas servi."""
    usage = firecrawl_usage()
    if not (usage["recherches"] or usage["pages"]):
        return ""
    budget = f" sur un budget de {usage['budget']}" if usage["budget"] is not None else ""
    return (f"Firecrawl : {usage['recherches']} recherche(s), {usage['pages']} page(s), environ "
            f"{usage['credits_estimes']} crédits ; campagne : {usage['campagne']} crédits{budget} (firecrawl.json).")


def fiche_ids(folder):
    """Identifiants qui ont déjà une fiche dans un dossier d'agent (fichiers <id>.json ou lots, listes JSON)."""
    ids = set()
    if not os.path.isdir(folder):
        return ids
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                content = json.load(handle)
        except (OSError, ValueError):
            continue  # fichier en cours d'écriture ou invalide : assemble_csv.py le signale
        for record in content if isinstance(content, list) else [content]:
            if isinstance(record, dict) and record.get("id") is not None:
                ids.add(str(record["id"]).strip())
    return ids


def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def run_with_budget(items, worker, parallel, seconds, on_result):
    """Traite les éléments en parallèle, sans en lancer de nouveaux après `seconds` secondes (0 = sans limite).

    Les éléments déjà commencés vont au bout. Ainsi, un lancement reste plus court que le délai
    accordé aux commandes des agents, et il suffit de relancer la commande pour continuer.
    Renvoie le nombre d'éléments traités.
    """
    deadline = time.monotonic() + seconds
    queue = iter(items)
    processed = 0
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        pending = set()

        def submit_next():
            if seconds and time.monotonic() >= deadline:
                return
            item = next(queue, None)
            if item is not None:
                pending.add(pool.submit(worker, item))

        for _ in range(parallel):
            submit_next()
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                pending.discard(future)
                on_result(future.result())
                processed += 1
                submit_next()
    return processed


def rewrite_csv(path, fields, keep):
    """Réécrit un CSV en ne gardant que les lignes pour lesquelles keep(ligne) est vrai."""
    rows = [r for r in read_rows(path) if keep(r)] if os.path.exists(path) else []
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


class CsvAppender:
    """Ajoute les lignes au fil de l'eau : un traitement interrompu reprend là où il s'est arrêté."""

    def __init__(self, path, fields):
        exists = os.path.exists(path) and os.path.getsize(path) > 0
        self.done = {row["id"] for row in read_rows(path)} if exists else set()
        self._handle = open(path, "a", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._handle, fieldnames=fields, extrasaction="ignore")
        self._lock = threading.Lock()
        if not exists:
            self._writer.writeheader()

    def write(self, row):
        with self._lock:
            self._writer.writerow(row)
            self._handle.flush()
            self.done.add(row["id"])

    def close(self):
        self._handle.close()
