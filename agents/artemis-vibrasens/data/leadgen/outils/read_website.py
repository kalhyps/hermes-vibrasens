"""Lit les pages clés du site de chaque entreprise et en garde le texte utile pour l'enrichissement.

Entrée : le CSV de find_website.py (colonnes id, nom, site_web, confiance).
Sorties :
  - pages/<id>.md : le texte des pages lues (accueil, entreprise, savoir-faire, moyens, références,
    recrutement, actualités, contact, mentions légales), avec l'URL de chaque page ;
  - lecture.csv : une ligne par entreprise (pages lues, emails génériques, téléphones fixes, réseaux,
    mots-clés trouvés, indices de recrutement).

Les pages sont choisies dans les liens du site et dans son plan (sitemap.xml), d'après les rubriques
(en français, anglais, allemand, néerlandais, espagnol, italien) et d'après les mots cibles (--cibles).
Seuls les emails génériques (contact@, info@…) et les téléphones fixes sont gardés : pas de coordonnées
personnelles. robots.txt est respecté, avec une requête par seconde au plus sur chaque site.
Si la clé FIRECRAWL_API_KEY est présente, Firecrawl lit les pages qui exigent un navigateur (texte trop
maigre) ou les sites qui ne répondent pas ; jamais celles qu'un site refuse (robots.txt, HTTP 401 ou 403).

Exemples :
  python read_website.py --entree sites.csv --mots-cles "parc machines,CNC,maintenance" --cibles "machines,atelier"
  python read_website.py --completer --ids 123456789,987654321 --cibles "parc machines,atelier,effectif"
  python read_website.py --ids 123456789 --urls "https://www.federation.fr/membres/x/,https://www.exemple.fr/usine/"
Chaque lancement s'arrête de lui-même au bout de 3 minutes environ (--duree-max) : relancer la même
commande reprend là où le traitement s'est arrêté, jusqu'à « restants : 0 ».
"""
import argparse
import csv
import datetime
import os
import re
from urllib.parse import unquote, urljoin, urlparse

import lxml.html

from common import (CsvAppender, PageError, PoliteClient, country_code, domain_of, fiche_ids, firecrawl_client,
                    firecrawl_report, firecrawl_scrape, is_generic_local, is_social_site, name_tokens, normalize,
                    page_text, read_rows, rewrite_csv, root_domain, run_with_budget)

try:
    import phonenumbers
except ImportError:  # image antérieure à phonenumberslite : numéros français seulement
    phonenumbers = None

FIELDS = ["id", "nom", "site_web", "pages_lues", "emails_generiques", "telephones", "contacts_pages", "reseaux",
          "mots_cles", "recrutement", "peu_de_texte", "firecrawl", "erreur"]

# Pages recherchées, par ordre de priorité : (rubrique, mots repérés dans le lien ou son libellé)
SECTIONS = [
    ("entreprise", ("qui-sommes-nous", "qui sommes", "a-propos", "a propos", "about", "entreprise", "societe",
                    "presentation", "histoire", "notre-entreprise", "company", "unternehmen", "uber uns",
                    "ueber-uns", "over ons", "over-ons", "quienes somos", "empresa", "chi siamo", "azienda")),
    ("savoir-faire", ("savoir-faire", "savoir faire", "metier", "activite", "services", "prestations",
                      "competences", "expertise", "solutions", "produits", "products", "capabilities",
                      "leistungen", "produkte", "diensten", "producten", "servicios", "productos", "servizi",
                      "prodotti")),
    ("moyens", ("parc-machine", "parc machine", "machines", "equipements", "moyens", "atelier", "capacites",
                "production", "usine", "equipment", "machinery", "facilities", "workshop", "maschinenpark",
                "fertigung", "maschinen", "machinepark", "productie", "maquinaria", "instalaciones",
                "macchinari", "produzione")),
    ("references", ("references", "clients", "secteurs", "realisations", "marches", "customers", "industries",
                    "referenzen", "kunden", "referenties", "clientes", "clienti")),
    ("recrutement", ("recrutement", "carriere", "emploi", "jobs", "rejoindre", "offres", "careers", "vacancies",
                     "karriere", "stellenangebote", "vacatures", "werken bij", "empleo", "trabaja", "lavora con noi")),
    ("actualites", ("actualites", "actualite", "news", "blog", "aktuelles", "nieuws", "noticias", "notizie")),
    ("contact", ("contact", "nous-contacter", "kontakt", "contacto", "contatti")),
    ("mentions", ("mentions-legales", "mentions legales", "mentions", "legal", "impressum", "aviso legal",
                  "note legali", "colofon", "disclaimer")),
]
# Texte gardé par page : plus long pour les pages qui décrivent l'activité et les moyens de production.
TEXT_LIMITS = {"moyens": 12000, "savoir-faire": 10000, "cible": 12000, "source": 12000}
TEXT_PER_PAGE = 6000
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Messageries grand public : gardées seulement quand l'entreprise n'a pas d'adresse à son nom.
FREE_MAIL = ("gmail.", "googlemail.", "hotmail.", "outlook.", "live.", "yahoo.", "icloud.", "aol.", "gmx.",
             "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "neuf.fr", "laposte.net", "bbox.fr", "web.de",
             "t-online.de", "skynet.be", "proximus.be", "telenet.be", "btinternet.com")
PHONE = re.compile(r"(?:\+33\s?\(?0?\)?\s?|\b0)([1-9])(?:[\s.-]?(\d{2})){4}\b")
# Liens vers des documents ou des médias : ce ne sont pas des pages à lire.
DOCUMENT = re.compile(r"\.(pdf|jpe?g|png|gif|webp|avif|heic|bmp|tiff?|ico|svg|zip|rar|7z|docx?|xlsx?|pptx?|odt|ods|csv"
                      r"|mp3|mp4|m4v|webm|mov|avi|dwg|dxf|stp|step|igs|stl|exe|dmg)$", re.I)
SOCIAL = ("linkedin.com/company", "facebook.com/", "youtube.com/", "instagram.com/", "x.com/", "twitter.com/")
JOB_WORDS = re.compile(r"\b(h/f|f/h|m/w/d|w/m/d|cdi|cdd|alternance|nous recrutons|on recrute|we are hiring"
                       r"|vacanc\w*|stellenangebot\w*|vacature\w*|offres? d.emploi|job offers?)\b", re.I)
MD_LINK = re.compile(r"\[([^\]]{0,80})\]\((https?://[^)\s]+)\)")
SECTION_HEADER = re.compile(r"^## (\S+) : (\S+)$", re.M)


def internal_links(html, base_url):
    try:
        tree = lxml.html.fromstring(html)
    except (lxml.etree.ParserError, ValueError):
        return [], []
    links, socials = [], []
    for anchor in tree.iter("a"):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if any(s in url for s in SOCIAL):
            socials.append(url)
        elif domain_of(url) == domain_of(base_url) and not DOCUMENT.search(urlparse(url).path):
            links.append((url, normalize(" ".join(anchor.text_content().split())) + " " + normalize(unquote(urlparse(url).path))))
    return links, socials


def markdown_links(markdown, base_url):
    """Liens internes d'une page rendue par Firecrawl (format Markdown)."""
    return [(url.split("#")[0], normalize(label) + " " + normalize(unquote(urlparse(url).path)))
            for label, url in MD_LINK.findall(markdown or "")
            if domain_of(url) == domain_of(base_url) and not DOCUMENT.search(urlparse(url).path)]


def sitemap_links(client, base_url, limit=400):
    """Pages du plan du site (sitemap.xml et ses sous-plans), sur le même domaine."""
    origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    queue = [origin + "/sitemap.xml", origin + "/sitemap_index.xml", origin + "/wp-sitemap.xml"]
    seen, urls = set(), []
    while queue and len(seen) < 6 and len(urls) < limit:
        sitemap = queue.pop(0)
        if sitemap in seen:
            continue
        seen.add(sitemap)
        try:
            _, xml = client.get(sitemap, allow_xml=True)
        except PageError:
            continue
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
            if loc.endswith(".xml") or "sitemap" in loc.rsplit("/", 1)[-1]:
                queue.append(loc)
            elif domain_of(loc) == domain_of(base_url) and not DOCUMENT.search(urlparse(loc).path):
                urls.append(loc)
    return [(u, normalize(unquote(urlparse(u).path))) for u in urls[:limit]]


def pick_pages(links, limit, targets=(), exclude=(), targets_only=False):
    """Pages à lire : d'abord celles qui répondent aux mots cibles, puis une par rubrique."""
    chosen, seen = [], set(exclude)
    for target in targets:
        word = normalize(target)
        for url, label in links:
            if url not in seen and word and word in label:
                chosen.append(("cible", url))
                seen.add(url)
                break
        if len(chosen) >= limit:
            return chosen
    if targets_only:
        return chosen
    for section, words in SECTIONS:
        for url, label in links:
            if url in seen:
                continue
            if any(normalize(word) in label for word in words):
                chosen.append((section, url))
                seen.add(url)
                break
        if len(chosen) >= limit:
            break
    return chosen


def fetch(client, url):
    """(url_finale, html, texte, via_firecrawl). Firecrawl n'intervient que pour une page trop maigre
    (rendue en JavaScript) ou un site qui ne répond pas ; jamais pour une page refusée par le site."""
    use_firecrawl = firecrawl_client() is not None
    try:
        final_url, html = client.get(url)
    except PageError as exc:
        refused = any(r in str(exc) for r in ("robots", "HTTP 401", "HTTP 403", "HTTP 404", "HTTP 410", "pas une page"))
        rendered = firecrawl_scrape(url) if use_firecrawl and not refused else None
        if rendered:
            return url, "", rendered, True
        raise
    text = page_text(html)
    if use_firecrawl and len(text) < 300:
        rendered = firecrawl_scrape(final_url)
        if rendered and len(rendered) > len(text):
            return final_url, html, rendered, True
    return final_url, html, text, False


def main_label(domain):
    """« www.savimetal.com » -> « savimetal », « atelier.co.uk » -> « atelier »"""
    return root_domain(domain).split(".")[0]


def company_domain(domain, site_domain, name):
    """Le domaine d'un email est-il celui de l'entreprise ? Même nom que son site (savimetal.be pour
    savimetal.com), ou son nom (cornely.lu pour « Schreinerei Cornely Rainer », scopnea.com pour « SCOP NEA »)."""
    label = main_label(domain)
    flat = label.replace("-", "")
    if flat == main_label(site_domain).replace("-", ""):
        return True
    tokens = name_tokens(name)
    if flat in ("".join(tokens), normalize(name).replace(" ", "")):
        return True
    return any(len(t) >= 4 and (t in label.split("-") or flat.startswith(t)) for t in tokens)


# Boîtes à mettre en dernier : elles ne servent pas à une prise de contact commerciale.
BACK_OFFICE = ("rh", "recrutement", "emploi", "jobs", "compta", "achats", "qualite", "sav")


def generic_emails(html, site_domain, name):
    """Emails génériques (contact@, info@…) à un domaine de l'entreprise ; à défaut, sur une messagerie grand
    public. Ceux de l'hébergeur, de l'agence web ou du modèle de site cités dans les pages sont écartés."""
    own, free = [], []
    for email in EMAIL.findall(html):
        local, domain = email.split("@", 1)
        labels = domain.split(".")
        if len(labels) > 2 and labels[-1][:1].isupper() and domain[: -len(labels[-1])].islower():
            domain = ".".join(labels[:-1])  # « contact@dbc.fr.Vous » : phrase collée à l'adresse
        local, domain = local.lower(), domain.lower()
        if domain.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")) or not is_generic_local(local):
            continue
        if company_domain(domain, site_domain, name):
            found = own
        elif any(domain == f or (f.endswith(".") and domain.startswith(f)) for f in FREE_MAIL):
            found = free
        else:
            continue
        if f"{local}@{domain}" not in found:
            found.append(f"{local}@{domain}")
    emails = own or free
    # « contactercontact@… » : un mot collé devant une adresse déjà trouvée.
    emails = [e for e in emails if not any(o != e and o.split("@")[1] == e.split("@")[1]
                                           and e.split("@")[0].endswith(o.split("@")[0]) for o in emails)]
    return sorted(emails, key=lambda e: e.startswith(BACK_OFFICE))[:3]


def landline_phones(text, country):
    """Téléphones fixes et numéros d'entreprise du pays, au format international ; les mobiles et les numéros
    d'autres pays (hébergeur, filiales) sont ignorés."""
    if phonenumbers is None:
        return french_landlines(text) if country == "FR" else []
    found = []
    for match in phonenumbers.PhoneNumberMatcher(text, country):
        if phonenumbers.region_code_for_number(match.number) != country:
            continue
        if phonenumbers.number_type(match.number) in (phonenumbers.PhoneNumberType.MOBILE,
                                                      phonenumbers.PhoneNumberType.PERSONAL_NUMBER):
            continue
        number = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        if number not in found:
            found.append(number)
    return found[:3]


def contact_pages(emails, phones, pages):
    """Page où chaque email et chaque téléphone a été lu (« valeur : adresse ; … »), pour que le livrable dise d'où
    viennent les coordonnées. Un téléphone est reconnu par ses neuf derniers chiffres, quelle que soit son écriture."""
    found = []
    for email in emails:
        page = next((url for url, text in pages if email.lower() in text.lower()), "")
        if page:
            found.append(f"{email} : {page}")
    digits_by_page = [(url, re.sub(r"\D", "", text)) for url, text in pages]
    for phone in phones:
        digits = re.sub(r"\D", "", phone)[-9:]
        page = next((url for url, text in digits_by_page if digits and digits in text), "")
        if page:
            found.append(f"{phone} : {page}")
    return found


def drop_shared_phones(path):
    """Un numéro présent sur les sites de plusieurs entreprises est celui d'une agence web ou d'un hébergeur :
    on le retire de lecture.csv (et de la page où il a été lu)."""
    rows = read_rows(path)
    owners = {}
    for row in rows:
        for phone in filter(None, row["telephones"].split(" ; ")):
            owners.setdefault(phone, set()).add(row["id"])
    shared = {phone for phone, ids in owners.items() if len(ids) > 1}
    if not shared:
        return
    for row in rows:
        row["telephones"] = " ; ".join(p for p in row["telephones"].split(" ; ") if p and p not in shared)
        row["contacts_pages"] = " ; ".join(p for p in (row.get("contacts_pages") or "").split(" ; ")
                                           if p and p.split(" : ")[0] not in shared)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def french_landlines(text):
    """Téléphones fixes français ; les mobiles (06, 07) sont ignorés."""
    found = []
    for match in PHONE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        digits = "0" + digits[-9:]
        if digits[1] in "67" or digits in found:
            continue
        found.append(digits)
    return [" ".join(d[i:i + 2] for i in range(0, 10, 2)) for d in found[:3]]


def write_sections(path, row, sections, append):
    with open(path, "a" if append else "w", encoding="utf-8") as handle:
        if not append:
            handle.write(f'# {row["nom"]} : {row["site_web"]}\n\nLu le {datetime.date.today().isoformat()}.\n')
        for section, url, text, _ in sections:
            limit = TEXT_LIMITS.get(section, TEXT_PER_PAGE)
            excerpt = text[:limit] + (" […]" if len(text) > limit else "")
            handle.write(f"\n## {section} : {url}\n\n{excerpt}\n")


SOURCE_BLOCK = re.compile(r"^## source : \S+$.*?(?=^## |\Z)", re.M | re.S)


def source_blocks(text):
    """Pages de tiers ajoutées aux preuves (--urls) : rubriques « ## source : <adresse> » de pages/<id>.md."""
    return SOURCE_BLOCK.findall(text or "")


def own_text(text):
    """Le texte des pages de l'entreprise, sans les pages de tiers : les contacts d'une fédération ou d'un article ne
    sont pas ceux de l'entreprise."""
    return SOURCE_BLOCK.sub("", text or "")


def process(row, client, keywords, targets, folder, max_pages, complete, complete_pages):
    """Lit le site d'une entreprise (ou le complète), écrit pages/<id>.md et renvoie la ligne de lecture.csv."""
    path = os.path.join(folder, f'{row["id"]}.md')
    previous_text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    already = SECTION_HEADER.findall(previous_text) if complete else []
    read_urls = {url for _, url in already}
    sections, all_html, errors, socials, page_texts = [], [], [], [], []
    try:
        home_url, home_html, home_text, home_fc = fetch(client, row["site_web"])
        links, socials = internal_links(home_html, home_url) if home_html else ([], [])
        links += markdown_links(home_text, home_url) if home_fc else []
        if home_url not in read_urls:
            sections.append(("accueil", home_url, home_text, home_fc))
        all_html.append(home_html)  # même déjà lu : ses liens mailto: comptent pour les contacts
        page_texts.append((home_url, (home_html or "") + "\n" + home_text))
        known = {url for url, _ in links}
        links += [(u, label) for u, label in sitemap_links(client, home_url) if u not in known]
        # Pour compléter : quelques pages seulement, choisies uniquement d'après les mots cibles.
        budget = min(complete_pages, max_pages) if complete else max_pages - 1
        for section, url in pick_pages(links, budget, targets, exclude=read_urls | {home_url}, targets_only=complete):
            try:
                final_url, html, text, via_fc = fetch(client, url)
            except PageError as exc:
                errors.append(f"{section} : {exc}")
                continue
            sections.append((section, final_url, text, via_fc))
            all_html.append(html)
            page_texts.append((final_url, (html or "") + "\n" + text))
            socials += internal_links(html, final_url)[1] if html else []
    except PageError as exc:
        errors.append(f"accueil : {exc}")

    write_sections(path, row, sections, append=bool(already))
    kept = [] if complete else source_blocks(previous_text)
    if kept:  # relecture complète : les pages de tiers ajoutées aux preuves restent
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(block.rstrip() + "\n" for block in kept))
    full_text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    company_text = own_text(full_text)
    joined_html = "\n".join(all_html)
    lowered = normalize(full_text)
    jobs = len(JOB_WORDS.findall(full_text))
    has_jobs_page = any(s == "recrutement" for s, _, _, _ in sections) or any(s == "recrutement" for s, _ in already)
    emails = generic_emails(joined_html + "\n" + company_text, domain_of(row["site_web"]), row["nom"])
    phones = landline_phones(company_text, country_code(row.get("pays")))
    return {
        "id": row["id"], "nom": row["nom"], "site_web": row["site_web"],
        "pages_lues": len(already) + len(sections),
        "emails_generiques": " ; ".join(emails),
        "telephones": " ; ".join(phones),
        "contacts_pages": " ; ".join(contact_pages(emails, phones, page_texts)),
        "reseaux": " ; ".join(dict.fromkeys(socials)),
        "mots_cles": " ; ".join(f"{k} ({lowered.count(normalize(k))})" for k in keywords if normalize(k) in lowered),
        "recrutement": f"oui ({jobs} indice(s) d'offres)" if jobs else ("page carrières" if has_jobs_page else ""),
        "peu_de_texte": "oui" if (already or sections) and len(full_text) < 800 else "",
        "firecrawl": str(sum(1 for *_, via_fc in sections if via_fc) or ""),
        "erreur": " | ".join(errors),
    }


# Réseaux sociaux et bases commerciales de prospection : leurs chiffres sont des estimations invérifiables, souvent tirées
# de LinkedIn ; ils ne servent jamais de preuve.
NOT_EVIDENCE = ("linkedin.", "zoominfo.", "apollo.io", "rocketreach.", "lusha.", "prospeo.", "signalhire.", "datanyze.",
                "kaspr.", "dropcontact.", "leadiq.", "seamless.ai", "contactout.", "dnb.com", "kompass.", "societe.com",
                "pappers.", "verif.com", "manageo.", "corporama.", "infonet.", "b2bhint.", "growjo.", "craft.co",
                "owler.", "crunchbase.", "cbinsights.", "zaubacorp.", "bizapedia.", "buzzfile.", "manta.com")


def add_pages(args):
    """--urls : lit des pages précises et les ajoute à pages/<id>.md (rubrique « source »), pour que toute preuve citée
    par un agent soit conservée et relisible. robots.txt est respecté ; une page refusée est signalée, pas contournée."""
    ids = [i.strip() for i in (args.ids or "").split(",") if i.strip()]
    if len(ids) != 1:
        raise SystemExit("--urls demande --ids avec un seul identifiant.")
    company = ids[0]
    names = {r["id"]: r for r in read_rows("1-collecte.csv")} if os.path.exists("1-collecte.csv") else {}
    if company not in names:
        raise SystemExit(f"Identifiant inconnu dans 1-collecte.csv : {company}")
    os.makedirs(args.dossier, exist_ok=True)
    path = os.path.join(args.dossier, f"{company}.md")
    already = set(SECTION_HEADER.findall(open(path, encoding="utf-8").read())) if os.path.exists(path) else set()
    known = {url for _, url in already}
    row = {"nom": names[company].get("nom", ""), "site_web": ""}
    client = PoliteClient(delay=1.0)
    sections = []
    for url in [u.strip() for u in args.urls.split(",") if u.strip()]:
        if any(bad in domain_of(url) for bad in NOT_EVIDENCE) or is_social_site(url):
            print(f"REFUSÉE : {url} (réseau social ou base commerciale de prospection : pas une preuve)")
            continue
        if url in known:
            print(f"déjà dans les pages : {url}")
            continue
        try:
            final_url, _, text, via_fc = fetch(client, url)
        except PageError as exc:
            # Un document (PDF…) n'est pas une page HTML : Firecrawl sait le lire, si sa clé est là.
            rendered = firecrawl_scrape(url) if "pas une page HTML" in str(exc) and firecrawl_client() else None
            if not rendered:
                print(f"ILLISIBLE : {url} ({exc})")
                continue
            final_url, text, via_fc = url, rendered, True
        sections.append(("source", final_url, text, via_fc))
        print(f"ajoutée : {final_url} ({len(text)} caractères)")
    if sections:
        write_sections(path, row, sections, append=os.path.exists(path))
    print(f"{len(sections)} page(s) ajoutée(s) à {path}.")
    if firecrawl_report():
        print(firecrawl_report())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--entree", default="sites.csv")
    parser.add_argument("--sortie", default="lecture.csv")
    parser.add_argument("--dossier", default="pages", help="dossier des fichiers texte par entreprise")
    parser.add_argument("--max-pages", type=int, default=10, help="pages lues par site, accueil compris")
    parser.add_argument("--mots-cles", default="", help="mots-clés du brief à compter, séparés par des virgules")
    parser.add_argument("--cibles", default="", help="mots désignant les pages à lire en priorité (ex. machines,atelier)")
    parser.add_argument("--confiance-min", choices=["basse", "moyenne", "haute"], default="basse")
    parser.add_argument("--max", type=int, default=0, help="nombre maximal de sites à lire dans ce lancement")
    parser.add_argument("--ids", help="identifiants à traiter, séparés par des virgules")
    parser.add_argument("--pages-completer", type=int, default=4,
                        help="avec --completer : pages ciblées de plus, au plus, par site")
    parser.add_argument("--completer", action="store_true",
                        help="avec --ids : lire des pages supplémentaires de sites déjà lus, choisies d'après --cibles")
    parser.add_argument("--urls", help="avec --ids <un identifiant> : pages précises à ajouter aux preuves de "
                                       "l'entreprise (page du site, fiche d'une fédération, offre d'emploi, article), "
                                       "séparées par des virgules")
    parser.add_argument("--paralleles", type=int, default=4, help="sites lus en même temps")
    parser.add_argument("--reessayer-erreurs", action="store_true",
                        help="relire les sites dont l'accueil n'a pas pu être lu lors d'un passage précédent")
    parser.add_argument("--duree-max", type=int, default=180,
                        help="secondes après lesquelles l'outil ne commence plus de site (0 = sans limite)")
    args = parser.parse_args()
    if args.completer and not args.ids:
        parser.error("--completer demande --ids")
    if args.urls:
        return add_pages(args)

    levels = {"basse": 1, "moyenne": 2, "haute": 3}
    rows = [r for r in read_rows(args.entree)
            if r.get("site_web") and levels.get(r.get("confiance"), 0) >= levels[args.confiance_min]]
    wanted = {i.strip() for i in (args.ids or "").split(",") if i.strip()}
    if wanted:
        rows = [r for r in rows if r["id"] in wanted]
    # Le pays de chaque entreprise (pour lire ses numéros de téléphone) vient de la collecte.
    collecte = os.path.join(os.path.dirname(args.entree), "1-collecte.csv")
    countries = {r["id"]: r.get("pays", "") for r in read_rows(collecte)} if os.path.exists(collecte) else {}
    for row in rows:
        row.setdefault("pays", countries.get(row["id"], ""))
    finished = fiche_ids("enrichissement")  # entreprises des vagues précédentes, déjà jugées
    if os.path.exists(args.sortie):
        with open(args.sortie, encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle), [])
        if header and header != FIELDS:
            rewrite_csv(args.sortie, FIELDS, lambda r: True)  # campagne commencée avant l'ajout d'une colonne
    if args.reessayer_erreurs:
        rewrite_csv(args.sortie, FIELDS, lambda r: not r.get("erreur", "").startswith("accueil") or r["id"] in finished)
    previous = {}
    if args.completer:
        # Les lignes complétées remplacent les anciennes à la fin seulement : un lancement interrompu ne perd rien.
        previous = {r["id"]: r for r in read_rows(args.sortie)} if os.path.exists(args.sortie) else {}
    os.makedirs(args.dossier, exist_ok=True)
    output = CsvAppender(args.sortie, FIELDS) if not args.completer else None
    completed = {}
    todo = [r for r in rows if args.completer or r["id"] not in output.done]
    if args.max:
        todo = todo[: args.max]
    keywords = [k.strip() for k in args.mots_cles.split(",") if k.strip()]
    targets = [k.strip() for k in args.cibles.split(",") if k.strip()]
    client = PoliteClient(delay=1.0)

    def handle(result):
        old = previous.get(result["id"], {})
        for key in ("emails_generiques", "telephones", "contacts_pages", "reseaux"):  # en complément : réunir l'ancien et le nouveau
            merged = [v for v in (old.get(key, "") + " ; " + result.get(key, "")).split(" ; ") if v]
            result[key] = " ; ".join(dict.fromkeys(merged))
        for key in ("mots_cles", "recrutement"):
            if not result.get(key) and old.get(key):
                result[key] = old[key]
        if old.get("firecrawl"):
            result["firecrawl"] = str(int(old["firecrawl"] or 0) + int(result.get("firecrawl") or 0))
        if args.completer:
            completed[result["id"]] = result
        else:
            output.write(result)
        print(f'{result["id"]} {result["nom"][:40]:40} {result["pages_lues"]} page(s) {result["mots_cles"][:60]}',
              flush=True)

    processed = run_with_budget(
        todo, lambda row: process(row, client, keywords, targets, args.dossier, args.max_pages, args.completer,
                                  args.pages_completer),
        args.paralleles, args.duree_max, handle)
    if args.completer:
        rewrite_csv(args.sortie, FIELDS, lambda r: r["id"] not in completed)
        output = CsvAppender(args.sortie, FIELDS)
        for result in completed.values():
            output.write(result)
        remaining = len([r for r in todo if r["id"] not in completed])
    output.close()
    drop_shared_phones(args.sortie)
    if not args.completer:
        remaining = len([r for r in rows if r["id"] not in output.done])
    print(f"Sites lus : {processed} ; restants : {remaining}")
    if remaining:
        print("Arrêt volontaire pour rester sous le délai des commandes : relancez la même commande pour continuer.")
    failed = [r for r in read_rows(args.sortie) if r.get("erreur", "").startswith("accueil") and r["id"] not in finished]
    if failed and not remaining and not args.completer:
        print(f"{len(failed)} site(s) dont l'accueil n'a pas pu être lu : relancez une fois avec --reessayer-erreurs.")
    if firecrawl_report():
        print(firecrawl_report())


if __name__ == "__main__":
    main()
