#!/usr/bin/env python3
"""Source LinkedIn pour l'agent Hermes de prospection.

Principe : on ne se connecte jamais à LinkedIn et on n'ouvre aucune page
linkedin.com. On interroge un moteur de recherche (Serper = Google, ou Brave)
avec des requêtes `site:linkedin.com/...` et on exploite uniquement le titre,
l'URL et l'extrait renvoyés par le moteur.

Sous-commandes :
  companies  pages entreprises LinkedIn pour des mots-clés et des pays
  people     décideurs publics d'une liste d'entreprises
  liens      liens de recherche LinkedIn / Sales Navigator à valider à la main

Aucune dépendance hors bibliothèque standard.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SEEN = os.environ.get("LINKEDIN_SEEN_FILE", os.path.join(os.path.dirname(HERE), "data", "linkedin_seen.json"))
# Ce qui a été écarté parce que déjà vu : écrit à côté du fichier de sortie, pour rester traçable.
SEEN_FIELDS = ["type", "nom", "entreprise", "linkedin_url", "vu_le", "campagne", "requete"]

# Intitulés recherchés par défaut (international : FR, EN, DE, ES, IT).
DEFAULT_TITLES = [
    "CEO", "Directeur général", "Président", "Geschäftsführer",
    "Directeur industriel", "Directeur des opérations", "COO",
    "Directeur technique", "CTO", "Head of Engineering",
    "Directeur achats", "Head of Procurement", "Einkaufsleiter",
    "Directeur d'usine", "Plant Manager", "Werksleiter",
]

# Mots qui font d'un intitulé un décideur (comparaison sans accents, minuscules).
DECISION_KEYWORDS = [
    "ceo", "coo", "cto", "cfo", "cpo", "founder", "fondateur", "owner",
    "president", "directeur", "directrice", "director", "head of", "vp",
    "vice president", "geschaftsfuhrer", "leiter", "leiterin", "inhaber",
    "gerant", "dirigeant", "managing director", "general manager",
    "plant manager", "direttore", "director general", "amministratore",
]


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------

def norm(text):
    """Minuscules, sans accents, espaces compactés."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def strip_linkedin_suffix(title):
    return re.sub(r"\s*[|\-–—]\s*LinkedIn\s*$", "", title or "", flags=re.I).strip()


def canonical_company_url(url):
    m = re.search(r"linkedin\.com/company/([^/?#]+)", url or "", re.I)
    return f"https://www.linkedin.com/company/{m.group(1).lower()}" if m else None


def canonical_profile_url(url):
    m = re.search(r"linkedin\.com/in/([^/?#]+)", url or "", re.I)
    return f"https://www.linkedin.com/in/{m.group(1).lower()}" if m else None


def is_decision_maker(title):
    t = norm(title)
    return any(re.search(rf"\b{re.escape(k)}\b", t) for k in DECISION_KEYWORDS)


def load_seen(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_seen(path, seen):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def campaign_name():
    """Le dossier courant : celui de la campagne quand le collecteur lance le script."""
    return os.path.basename(os.getcwd())


def seen_csv_for(out):
    return os.path.join(os.path.dirname(os.path.abspath(out)), "deja_vues.csv")


def already_seen(url, seen, args, skipped, query, kind):
    """Note l'URL dans `skipped` si elle a été vue lors d'un passage précédent ; True s'il faut l'écarter."""
    entry = seen.get(url)
    if entry is None:
        return False
    skipped.append({"type": kind, "nom": entry.get("nom", ""), "entreprise": entry.get("entreprise", ""),
                    "linkedin_url": url, "vu_le": entry.get("vu_le", ""), "campagne": entry.get("campagne", ""),
                    "requete": query})
    return not args.revoir


def append_csv(path, rows, fieldnames):
    if not rows:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerows(rows)


# Colonnes acceptées en entrée (sans casse ni accents) : fichiers du skill, ou leads.csv de la campagne.
COLUMN_ALIASES = {
    "entreprise": ["entreprise", "company_name", "company", "nom", "raison sociale", "raison_sociale"],
    "site_web": ["site_web", "site web", "website", "domain", "site"],
    "linkedin_url": ["linkedin_url", "linkedin", "page linkedin"],
}


def parse_filters(values):
    filters = []
    for v in values or []:
        if "=" not in v:
            sys.exit(f"--filtre attend COLONNE=VALEUR, reçu : {v}")
        col, val = v.split("=", 1)
        filters.append((col.strip(), val.strip()))
    return filters


def read_companies(path, filters=None):
    """Lit un CSV d'entreprises (séparateur « , » ou « ; » détecté, BOM accepté), garde les lignes qui passent
    les filtres COLONNE=VALEUR et renvoie des lignes aux clés entreprise, site_web, linkedin_url."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(f, dialect=dialect))
    header = {norm(k): k for k in (rows[0].keys() if rows else []) if k}
    for col, _ in filters or []:
        if norm(col) not in header:
            sys.exit(f"colonne « {col} » absente de {path} (colonnes : {', '.join(header.values())})")
    out = []
    for row in rows:
        if any(norm(row.get(header[norm(col)]) or "") != norm(val) for col, val in filters or []):
            continue
        item = {}
        for field, aliases in COLUMN_ALIASES.items():
            key = next((header[norm(a)] for a in aliases if norm(a) in header), None)
            item[field] = (row.get(key) or "").strip() if key else ""
        out.append(item)
    return out


# --------------------------------------------------------------------------
# Moteur de recherche
# --------------------------------------------------------------------------

def _http_json(req):
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def search(query, num=10, lang="fr", country=None):
    """Renvoie [{title, url, snippet}] via Serper (défaut) ou Brave."""
    provider = os.environ.get("SEARCH_PROVIDER", "serper").lower()
    if provider == "serper":
        key = os.environ.get("SERPER_API_KEY")
        if not key:
            sys.exit("SERPER_API_KEY manquant (voir .env.example)")
        body = {"q": query, "num": num, "hl": lang}
        if country:
            body["gl"] = country.lower()
        req = urllib.request.Request(
            "https://google.serper.dev/search",
            data=json.dumps(body).encode(),
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
        )
        data = _http_json(req)
        return [{"title": r.get("title", ""), "url": r.get("link", ""),
                 "snippet": r.get("snippet", "")} for r in data.get("organic", [])]
    if provider == "brave":
        key = os.environ.get("BRAVE_API_KEY")
        if not key:
            sys.exit("BRAVE_API_KEY manquant (voir .env.example)")
        params = {"q": query, "count": min(num, 20), "search_lang": lang}
        if country:
            params["country"] = country.upper()
        req = urllib.request.Request(
            "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params),
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        data = _http_json(req)
        return [{"title": r.get("title", ""), "url": r.get("url", ""),
                 "snippet": r.get("description", "")}
                for r in data.get("web", {}).get("results", [])]
    sys.exit(f"SEARCH_PROVIDER inconnu : {provider} (serper ou brave)")


# --------------------------------------------------------------------------
# Lecture des résultats
# --------------------------------------------------------------------------

_FIELD_PATTERNS = {
    "secteur": r"(?:Industry|Secteur|Branche|Sector|Settore)\s*:?\s*([^|·•\n]+?)(?=\s*[|·•]|\s+(?:Company size|Taille|Unternehmensgröße|Headquarters|Siège|Hauptsitz)|$)",
    "taille": r"(?:Company size|Taille de l[’']entreprise|Unternehmensgröße|Tamaño de la empresa)\s*:?\s*([\d\s.,+\-–]+(?:employees|employés|Beschäftigte|empleados)?)",
    "siege": r"(?:Headquarters|Siège social|Hauptsitz|Sede)\s*:?\s*([^|·•\n]+?)(?=\s*[|·•]|$)",
    "abonnes": r"([\d\s.,]+[kK]?)\s*(?:followers|abonnés|Follower)",
}


def parse_company_result(res):
    url = canonical_company_url(res["url"])
    if not url:
        return None
    name = strip_linkedin_suffix(res["title"])
    name = re.split(r"\s*[|:]\s*", name)[0].strip()
    row = {"entreprise": name, "linkedin_url": url, "extrait": res["snippet"]}
    for field, pat in _FIELD_PATTERNS.items():
        m = re.search(pat, res["snippet"] or "", re.I)
        row[field] = m.group(1).strip(" .,") if m else ""
    return row


def parse_profile_result(res, company):
    """Profil public : « Prénom Nom - Intitulé - Entreprise | LinkedIn »."""
    url = canonical_profile_url(res["url"])
    if not url:
        return None
    title = strip_linkedin_suffix(res["title"])
    parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+", title) if p.strip()]
    if not parts:
        return None
    name = parts[0]
    # On retire le segment « Entreprise » du titre, il reste l'intitulé.
    role = " - ".join(x for x in parts[1:] if norm(company) not in norm(x))
    # « Plant Manager at Acme » / « Directeur chez Acme »
    role = re.sub(r"\s+(?:at|chez|bei|en|presso)\s+.*$", "", role, flags=re.I) if role else ""
    # Le nom de l'entreprise doit apparaître : sinon c'est un homonyme ou un ancien salarié.
    haystack = norm(res["title"] + " " + res["snippet"])
    if norm(company) not in haystack:
        return None
    if not role:
        # L'intitulé est parfois seulement dans l'extrait.
        m = re.search(r"^([^·.|]{3,80}?)\s+(?:at|chez|bei)\s+", res["snippet"] or "", re.I)
        role = m.group(1).strip() if m else ""
    # Rejette les noms qui n'en sont pas (pages « 50+ profils », etc.)
    if len(name.split()) < 2 or re.search(r"\d|profil|profile", name, re.I):
        return None
    return {"nom": name, "intitule": role, "linkedin_url": url, "extrait": res["snippet"]}


# --------------------------------------------------------------------------
# Sous-commandes
# --------------------------------------------------------------------------

COMPANY_FIELDS = ["date_collecte", "source", "requete", "entreprise", "linkedin_url",
                  "secteur", "taille", "siege", "abonnes", "extrait", "statut"]
PEOPLE_FIELDS = ["date_collecte", "source", "requete", "entreprise", "site_web",
                 "nom", "intitule", "decideur", "linkedin_url", "extrait", "statut"]
LINK_FIELDS = ["entreprise", "page_linkedin_personnes", "recherche_linkedin",
               "booleen_sales_navigator"]


def cmd_companies(args):
    seen = load_seen(args.seen)
    rows, skipped = [], []
    countries = args.countries or [None]
    for kw in args.keywords:
        for country in countries:
            q = f'site:linkedin.com/company "{kw}"' + (f' "{country}"' if country else "")
            for res in search(q, num=args.num, lang=args.lang):
                row = parse_company_result(res)
                if not row:
                    continue
                if any(r["linkedin_url"] == row["linkedin_url"] for r in rows):
                    continue
                if already_seen(row["linkedin_url"], seen, args, skipped, q, "entreprise"):
                    continue
                row.update(date_collecte=date.today().isoformat(),
                           source="moteur de recherche (site:linkedin.com)",
                           requete=q, statut="a_trier")
                rows.append(row)
            time.sleep(args.pause)
    append_csv(args.out, rows, COMPANY_FIELDS)
    append_csv(seen_csv_for(args.out), skipped, SEEN_FIELDS)
    for r in rows:
        seen[r["linkedin_url"]] = {"type": "entreprise", "nom": r["entreprise"],
                                   "vu_le": r["date_collecte"], "campagne": campaign_name()}
    save_seen(args.seen, seen)
    print(f"LINKEDIN COMPANIES\nnouvelles: {len(rows)}\ndeja_vues: {len(skipped)}"
          f"{' (reprises)' if args.revoir and skipped else ''}\nfichier: {args.out}\nfichier_deja_vues: {seen_csv_for(args.out)}")


def cmd_people(args):
    companies = read_companies(args.input, parse_filters(args.filtre))
    titles = args.titles or DEFAULT_TITLES
    # Moteurs : requêtes courtes, on découpe les intitulés en paquets de 6.
    chunks = [titles[i:i + 6] for i in range(0, len(titles), 6)]
    seen = load_seen(args.seen)
    rows, skipped = [], []
    for c in companies:
        company = c["entreprise"]
        if not company:
            continue
        found = 0
        for chunk in chunks:
            or_titles = " OR ".join(f'"{t}"' for t in chunk)
            q = f'site:linkedin.com/in "{company}" ({or_titles})'
            for res in search(q, num=args.num, lang=args.lang):
                p = parse_profile_result(res, company)
                if not p:
                    continue
                if any(r["linkedin_url"] == p["linkedin_url"] for r in rows):
                    continue
                if already_seen(p["linkedin_url"], seen, args, skipped, q, "personne"):
                    continue
                dm = is_decision_maker(p["intitule"])
                if args.decideurs_only and not dm:
                    continue
                p.update(date_collecte=date.today().isoformat(),
                         source="moteur de recherche (site:linkedin.com)",
                         requete=q, entreprise=company,
                         site_web=c["site_web"],
                         decideur="oui" if dm else "non", statut="a_valider")
                rows.append(p)
                found += 1
            time.sleep(args.pause)
            if found >= args.max_per_company:
                break
    append_csv(args.out, rows, PEOPLE_FIELDS)
    append_csv(seen_csv_for(args.out), skipped, SEEN_FIELDS)
    for r in rows:
        seen[r["linkedin_url"]] = {"type": "personne", "nom": r["nom"],
                                   "entreprise": r["entreprise"], "vu_le": r["date_collecte"], "campagne": campaign_name()}
    save_seen(args.seen, seen)
    print(f"LINKEDIN PEOPLE\nentreprises: {len(companies)}\nnouveaux_contacts: {len(rows)}\n"
          f"decideurs: {sum(r['decideur'] == 'oui' for r in rows)}\ndeja_vus: {len(skipped)}\nfichier: {args.out}")


def build_links(company, linkedin_url, titles):
    or_titles = " OR ".join(f'"{t}"' if " " in t else t for t in titles)
    links = {"entreprise": company, "booleen_sales_navigator": f"({or_titles})"}
    links["recherche_linkedin"] = (
        "https://www.linkedin.com/search/results/people/?"
        + urllib.parse.urlencode({"keywords": f'"{company}" AND ({or_titles})'}))
    links["page_linkedin_personnes"] = (
        f"{linkedin_url.rstrip('/')}/people/?" + urllib.parse.urlencode({"keywords": " OR ".join(titles[:5])})
        if linkedin_url else "")
    return links


def cmd_liens(args):
    titles = args.titles or DEFAULT_TITLES[:8]
    rows = []
    for c in read_companies(args.input, parse_filters(args.filtre)):
        if c["entreprise"]:
            rows.append(build_links(c["entreprise"], c["linkedin_url"], titles))
    if os.path.exists(args.out):
        os.remove(args.out)  # liste régénérée à chaque fois
    append_csv(args.out, rows, LINK_FIELDS)
    print(f"LINKEDIN LIENS\nentreprises: {len(rows)}\nfichier: {args.out}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seen", default=DEFAULT_SEEN, help="mémoire des URLs déjà vues")
    p.add_argument("--revoir", action="store_true",
                   help="reprendre les entreprises et profils déjà vus lors d'une campagne précédente (ils sont quand même listés dans deja_vues.csv)")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("companies", help="trouver des pages entreprises LinkedIn")
    a.add_argument("--keywords", nargs="+", required=True, help='ex. "usinage" "mécanique de précision"')
    a.add_argument("--countries", nargs="*", help='ex. France Deutschland Italia')
    a.add_argument("--out", default="out/linkedin_entreprises.csv")
    a.set_defaults(func=cmd_companies)

    b = sub.add_parser("people", help="trouver les décideurs d'une liste d'entreprises")
    b.add_argument("--input", required=True,
                   help="CSV d'entreprises : linkedin/entreprises_retenues.csv, ou leads.csv de la campagne (séparateur « ; », colonnes Entreprise, Site web, Statut, Vague)")
    b.add_argument("--titles", nargs="*", help="intitulés à chercher (défaut : direction, industriel, achats)")
    b.add_argument("--max-per-company", type=int, default=5)
    b.add_argument("--decideurs-only", action="store_true")
    b.add_argument("--out", default="out/linkedin_contacts.csv")
    b.set_defaults(func=cmd_people)

    c = sub.add_parser("liens", help="liens LinkedIn / Sales Navigator pour validation manuelle")
    c.add_argument("--input", required=True)
    c.add_argument("--titles", nargs="*")
    c.add_argument("--out", default="out/linkedin_a_valider.csv")
    c.set_defaults(func=cmd_liens)

    for s in (b, c):
        s.add_argument("--filtre", action="append", default=[], metavar="COLONNE=VALEUR",
                       help="ne garder que les lignes dont la colonne vaut cette valeur (ex. \"Statut=qualifié\", \"Vague=2\") ; répétable")
    for s in (a, b):
        s.add_argument("--num", type=int, default=10, help="résultats par requête")
        s.add_argument("--lang", default="fr")
        s.add_argument("--pause", type=float, default=1.0, help="secondes entre deux requêtes")

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
