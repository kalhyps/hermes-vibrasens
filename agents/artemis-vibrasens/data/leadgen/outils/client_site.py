"""Lit le site du client (l'entreprise qui utilise l'équipe de prospection) pour constituer son dossier.

Même lecture que read_website.py pour les entreprises prospectées, mais hors campagne : l'accueil, puis les pages
d'offre, de produits, de références, de certifications, d'actualités et d'équipe, choisies dans les liens du site et
dans son plan (sitemap.xml). Sorties, dans le dossier du client (--dossier, /workspace/client par défaut) :
  - site.md : le texte de chaque page, sous une rubrique « ## <rubrique> : <adresse> » ;
  - lecture.json : pages lues, pages trop maigres (à lire avec web_extract), erreurs, date.
Relancer avec --urls "<adresse1>,<adresse2>" ajoute des pages précises à site.md.
robots.txt est respecté, une requête par seconde au plus. Si la clé Firecrawl est présente, elle sert aux pages qui
exigent un navigateur ; sans clé, ces pages sont signalées dans lecture.json.

Exemples :
  python client_site.py --site https://www.exemple.fr
  python client_site.py --site https://www.exemple.fr --urls "https://www.exemple.fr/references,https://www.exemple.fr/equipe"
"""
import argparse
import datetime
import json
import os
import re

from common import PageError, PoliteClient, firecrawl_report, normalize
from read_website import SECTION_HEADER, fetch, internal_links, markdown_links, pick_pages, sitemap_links

# Rubriques propres au site d'un fournisseur, lues avant les rubriques générales de read_website : ce qu'il vend,
# à qui, avec quelles preuves. Chaque mot cible choisit une page au plus.
CLIENT_TARGETS = ["offre", "offres", "produits", "products", "services", "prestations", "solutions", "references",
                  "clients", "temoignages", "cas-clients", "case", "secteurs", "industries", "certifications",
                  "qualite", "actualites", "news", "equipe", "team", "a-propos", "about", "contact"]
THIN_TEXT = 300  # en dessous, la page est sans doute rendue par script : à lire avec un navigateur


def read_site(site, folder, max_pages, extra_targets):
    client = PoliteClient(delay=1.0)
    sections, errors = [], []
    home_url, home_html, home_text, home_fc = fetch(client, site)
    links, _ = internal_links(home_html, home_url) if home_html else ([], [])
    links += markdown_links(home_text, home_url) if home_fc else []
    known = {url for url, _ in links}
    links += [(u, label) for u, label in sitemap_links(client, home_url) if u not in known]
    sections.append(("accueil", home_url, home_text, home_fc))
    targets = [t for t in extra_targets if t] + CLIENT_TARGETS
    for section, url in pick_pages(links, max_pages - 1, targets, exclude={home_url}):
        try:
            final_url, _, text, via_fc = fetch(client, url)
        except PageError as exc:
            errors.append(f"{url} : {exc}")
            continue
        label = next((normalize(t) for t in targets if normalize(t) in normalize(url)), section)
        sections.append((label, final_url, text, via_fc))
    return sections, errors


def add_urls(urls, known):
    client = PoliteClient(delay=1.0)
    sections, errors = [], []
    for url in urls:
        if url in known:
            print(f"déjà dans site.md : {url}")
            continue
        try:
            final_url, _, text, via_fc = fetch(client, url)
            sections.append(("ajout", final_url, text, via_fc))
        except PageError as exc:
            errors.append(f"{url} : {exc}")
    return sections, errors


def write(path, site, sections, append):
    with open(path, "a" if append else "w", encoding="utf-8") as handle:
        if not append:
            handle.write(f"# Site du client : {site}\n\nLu le {datetime.date.today().isoformat()}.\n")
        for section, url, text, _ in sections:
            handle.write(f"\n## {section} : {url}\n\n{text}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", required=True, help="adresse du site du client")
    parser.add_argument("--dossier", default="/workspace/client", help="dossier du client (défaut : /workspace/client)")
    parser.add_argument("--pages", type=int, default=12, help="pages lues au plus, accueil compris (défaut : 12)")
    parser.add_argument("--cibles", default="", help="mots désignant des pages à lire en priorité, séparés par des virgules")
    parser.add_argument("--urls", default="", help="pages précises à ajouter à site.md, séparées par des virgules")
    args = parser.parse_args()

    os.makedirs(args.dossier, exist_ok=True)
    site_path = os.path.join(args.dossier, "site.md")
    report_path = os.path.join(args.dossier, "lecture.json")
    report = json.load(open(report_path, encoding="utf-8")) if os.path.exists(report_path) else {}
    previous = open(site_path, encoding="utf-8").read() if os.path.exists(site_path) else ""
    known = {url for _, url in SECTION_HEADER.findall(previous)}

    if args.urls:
        urls = [u.strip() for u in args.urls.split(",") if u.strip()]
        sections, errors = add_urls(urls, known)
        write(site_path, args.site, sections, append=bool(previous))
    else:
        sections, errors = read_site(args.site, args.dossier, args.pages,
                                     [t.strip() for t in args.cibles.split(",")])
        write(site_path, args.site, sections, append=False)
        report = {}
    thin = [url for _, url, text, _ in sections if len(text) < THIN_TEXT]
    report.update({
        "site": args.site, "lu_le": datetime.date.today().isoformat(),
        "pages": report.get("pages", []) + [{"rubrique": s, "adresse": u, "caracteres": len(t), "firecrawl": fc}
                                            for s, u, t, fc in sections],
        "pages_maigres": sorted(set(report.get("pages_maigres", []) + thin)),
        "erreurs": report.get("erreurs", []) + errors,
    })
    json.dump(report, open(report_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    for section, url, text, via_fc in sections:
        print(f"{section:14} {len(text):6} car. {url}" + ("  (Firecrawl)" if via_fc else ""))
    print(f"\n{len(sections)} page(s) écrite(s) dans {site_path} ; compte rendu dans {report_path}.")
    if thin:
        print("Pages trop maigres (rendues par script ?), à lire avec web_extract : " + ", ".join(thin))
    for error in errors:
        print(f"ILLISIBLE : {error}")
    if firecrawl_report():
        print(firecrawl_report())


if __name__ == "__main__":
    main()
