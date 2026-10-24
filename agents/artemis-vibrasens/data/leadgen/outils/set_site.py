"""Corrige ou ajoute à la main le site d'une entreprise dans sites.csv (après une vérification par l'agent).

La lecture de ce site est ensuite refaite : la ligne correspondante de lecture.csv est retirée, et
read_website.py la relira au prochain lancement.

Exemples :
  python set_site.py --id 123456789 --site https://www.exemple.fr --preuve "SIREN dans les mentions légales"
  python set_site.py --id 123456789 --aucun --preuve "homonyme sans lien, aucun site trouvé"
"""
import argparse
import csv
import os

from common import domain_of, read_rows, rewrite_csv, root_domain
from find_website import FIELDS as SITE_FIELDS
from read_website import FIELDS as READ_FIELDS, source_blocks


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--id", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--site", help="adresse du site officiel")
    group.add_argument("--aucun", action="store_true", help="l'entreprise n'a pas de site vérifiable")
    parser.add_argument("--preuve", required=True, help="ce qui prouve que c'est le bon site (ou qu'il n'y en a pas)")
    parser.add_argument("--confiance", choices=["haute", "moyenne", "basse"], default="moyenne")
    parser.add_argument("--sites", default="sites.csv")
    parser.add_argument("--lecture", default="lecture.csv")
    args = parser.parse_args()

    if args.site and "://" not in args.site:
        args.site = "https://" + args.site.strip()
    rows = read_rows(args.sites)
    match = next((r for r in rows if r["id"] == args.id), None)
    before = domain_of((match or {}).get("site_web", ""))
    if match is None:
        collecte = {r["id"]: r for r in read_rows("1-collecte.csv")} if os.path.exists("1-collecte.csv") else {}
        source = collecte.get(args.id, {})
        match = {"id": args.id, "nom": source.get("nom", ""), "ville": source.get("ville", "")}
        rows.append(match)
    match.update(site_web="" if args.aucun else args.site.rstrip("/"), confiance="aucune" if args.aucun else args.confiance,
                 score="", preuve=f"vérification manuelle : {args.preuve}", erreur="")
    rewrite_csv(args.sites, SITE_FIELDS, lambda r: r["id"] != args.id)
    with open(args.sites, "a", encoding="utf-8", newline="") as handle:
        csv.DictWriter(handle, fieldnames=SITE_FIELDS, extrasaction="ignore").writerow(match)
    changed = args.aucun or root_domain(domain_of(args.site)) != root_domain(before)
    pages = os.path.join("pages", f"{args.id}.md")
    if changed:
        if os.path.exists(args.lecture):
            rewrite_csv(args.lecture, READ_FIELDS, lambda r: r["id"] != args.id)
        if os.path.exists(pages):
            # Les pages de l'ancien site (un homonyme) ne doivent plus servir de preuves ; les pages de tiers ajoutées
            # aux preuves (--urls) restent.
            kept = source_blocks(open(pages, encoding="utf-8").read())
            os.remove(pages)
            if kept:
                with open(pages, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(block.rstrip() + "\n" for block in kept))
    print(f'{args.id} {match.get("nom", "")[:40]} -> {match["site_web"] or "aucun site"} ({match["confiance"]})'
          + (" ; relancez read_website.py pour lire ce site." if changed and not args.aucun else "."))


if __name__ == "__main__":
    main()
