"""Importe une liste d'entreprises dans les sources de la campagne, au format commun : liste lue sur une page web
(adhérents d'une fédération, exposants d'un salon, membres d'un cluster, classement), ou fichier téléchargé
(CSV, JSON) d'un portail de données ouvertes, d'un registre, d'un annuaire autorisant la réutilisation.

Entrée :
  --fichier listes/<nom>.json : une liste JSON d'objets que tu as écrite après avoir lu la page, par exemple
      [{"nom": "Acme Precision", "ville": "Cluses", "site_web": "acme.fr", "source": "https://…/exposants?p=2"}, …]
  --fichier telechargements/<fichier>.csv : un fichier téléchargé (séparateur détecté) ;
  --colonnes "nom=Company Name,ville=City,code_activite=NAICS" : correspondance avec les colonnes du format commun
      quand les noms diffèrent (les colonnes de même nom sont reprises d'office).
Chaque ligne doit pouvoir citer sa source : colonne « source » de la ligne, sinon --source-url (la page ou le fichier).
Sans identifiant officiel (colonnes id_registre et registre), l'identifiant est tiré du nom, de la ville et du pays.

Exemples :
  python import_list.py --fichier listes/simodec-2026.json --nom salon-simodec-2026 --pays FR \\
      --source-url "https://www.simodec.com/liste-des-exposants"
  python import_list.py --fichier telechargements/members.csv --nom association-members --pays US \\
      --colonnes "nom=Company Name,ville=City,code_postal=Zip,site_web=Website,code_activite=NAICS" \\
      --nomenclature NAICS --source-url "https://example.org/members.csv"
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from connector_base import FIELDS, Connector, clean_id, common_arguments, main_guard, make_id  # noqa: E402

csv.field_size_limit(10_000_000)


def read_records(path):
    if path.lower().endswith((".json", ".jsonl")):
        with open(path, encoding="utf-8-sig") as handle:
            text = handle.read()
        if path.lower().endswith(".jsonl"):
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        data = json.loads(text)
        if isinstance(data, dict):  # {"entreprises": [...]} ou {"results": [...]} : la première liste trouvée
            data = next((v for v in data.values() if isinstance(v, list)), [])
        return [r for r in data if isinstance(r, dict)]
    with open(path, encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(65536)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return list(csv.DictReader(handle, dialect=dialect))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fichier", required=True, help="liste JSON, JSONL ou CSV")
    parser.add_argument("--pays", help="code ISO du pays, pour les lignes qui ne l'indiquent pas")
    parser.add_argument("--colonnes", default="", help="correspondances champ_commun=colonne_du_fichier, séparées par des virgules")
    parser.add_argument("--source-url", default="", help="page ou fichier d'origine, pour les lignes sans colonne source")
    parser.add_argument("--nomenclature", default="", help="nomenclature des codes d'activité du fichier (NAICS, NACE…)")
    parser.add_argument("--description", default="", help="ce qu'est la liste (ex. « exposants du SIMODEC 2026 »)")
    common_arguments(parser, "")
    args = parser.parse_args()
    if not args.nom:
        parser.error("indiquez --nom : le nom de la source (ex. salon-simodec-2026)")
    mapping = {}
    for pair in [p for p in args.colonnes.split(",") if p.strip()]:
        field, _, column = pair.partition("=")
        if field.strip() not in FIELDS:
            parser.error(f"champ inconnu dans --colonnes : {field.strip()} (champs possibles : {', '.join(FIELDS)})")
        mapping[field.strip()] = column.strip()

    records = read_records(args.fichier)
    if not records:
        sys.exit(f"LISTE VIDE : aucune entreprise lue dans {args.fichier}")
    missing = [column for column in mapping.values() if column not in records[0]]
    if missing:
        sys.exit(f"COLONNES ABSENTES du fichier : {', '.join(missing)} ; colonnes présentes : {', '.join(records[0])}")

    args.recommencer = True  # une liste est réimportée en entier : l'import remplace la source
    connector = Connector(args, args.description or f"Liste importée de {os.path.basename(args.fichier)}")
    connector.stats.update({"fichier": args.fichier, "lignes_lues": len(records), "sans_nom": 0, "sans_source": 0})
    for record in records:
        row = {field: record.get(mapping.get(field, field), "") for field in FIELDS}
        row = {k: ("" if v is None else str(v).strip()) for k, v in row.items()}
        if not row["nom"]:
            connector.stats["sans_nom"] += 1
            continue
        row["pays"] = (row["pays"] or args.pays or "").upper()
        row["source"] = row["source"] or args.source_url
        if not row["source"]:
            connector.stats["sans_source"] += 1
            continue
        row["nomenclature"] = row["nomenclature"] or (args.nomenclature if row["code_activite"] else "")
        if row["id_registre"] and row["registre"]:
            row["id"] = clean_id(row["id_registre"])
        elif not row["id"]:
            row["id"] = make_id(row["nom"], row["ville"] or row["code_postal"], row["pays"], prefix="l")
        connector.write(row)
    if connector.stats["sans_source"]:
        print(f"ATTENTION : {connector.stats['sans_source']} lignes sans source ignorées : indiquez --source-url.")
    return connector.finish(complete=True)


if __name__ == "__main__":
    main_guard(main)
