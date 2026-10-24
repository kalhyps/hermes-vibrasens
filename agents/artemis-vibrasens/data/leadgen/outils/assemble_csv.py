"""Assemble les fiches JSON écrites par un agent en un seul CSV.

Chaque fichier <dossier>/*.json contient une fiche (un objet avec au moins la clé "id", par exemple
<id>.json) ou un lot de fiches (une liste d'objets, par exemple vague-1-lot-1.json). Si une entreprise a
plusieurs fiches, elles sont fusionnées clé par clé : les lots d'abord, puis les fiches <id>.json, chacun du plus
ancien au plus récent. Une fiche de complément ({"id": …, "accroche": …}) ne remplace que les clés qu'elle contient,
et ses « criteres » ne remplacent que les critères qu'elle donne. Avec --base, les colonnes
du CSV de base (par exemple 1-collecte.csv) sont ajoutées devant ; une clé présente dans la fiche l'emporte sur la
colonne de même nom, même vide (un site écarté par l'enrichisseur ne revient pas depuis la collecte).

Exemples :
  python assemble_csv.py --dossier enrichissement --base 1-collecte.csv --sortie 2-enrichissement.csv
  python assemble_csv.py --dossier qualification --sortie 3-qualification.csv --tri score
"""
import argparse
import csv
import glob
import json
import os

from common import read_rows
from waves import refresh as refresh_waves


def flatten(value):
    """Listes et objets deviennent du texte lisible dans une cellule."""
    if isinstance(value, list):
        return " ; ".join(flatten(v) for v in value)
    if isinstance(value, dict):
        return " ; ".join(f"{k} : {flatten(v)}" for k, v in value.items())
    return "" if value is None else str(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dossier", required=True, help="dossier des fichiers <id>.json")
    parser.add_argument("--sortie", required=True)
    parser.add_argument("--base", help="CSV dont les colonnes sont reprises devant, jointure sur id")
    parser.add_argument("--tri", help="colonne numérique de tri, décroissant (ex. score)")
    args = parser.parse_args()

    by_id, errors = {}, []
    # Les lots d'abord, puis les fiches d'une seule entreprise (corrections, compléments), chacun par date : une fiche
    # <id>.json l'emporte toujours sur le lot, même si le lot a été réenregistré après elle.
    paths = sorted(glob.glob(os.path.join(args.dossier, "*.json")),
                   key=lambda p: (not os.path.basename(p).startswith(("lot-", "vague-")), os.path.getmtime(p)))
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                content = json.load(handle)
            records = content if isinstance(content, list) else [content]
            if not all(isinstance(r, dict) and r.get("id") not in (None, "") for r in records):
                raise ValueError("une fiche n'a pas d'« id »")
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{os.path.basename(path)} : {exc}")  # fichier entier ignoré, rien n'est fusionné
            continue
        for record in records:
            record["id"] = str(record["id"]).strip()
            merged = by_id.setdefault(record["id"], {})
            for key, value in record.items():
                # fusion clé par clé ; les critères aussi, critère par critère
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
    by_id = {i: {k: flatten(v) for k, v in r.items()} for i, r in by_id.items()}
    records = sorted(by_id.values(), key=lambda r: r["id"])

    base_fields, base = [], {}
    if args.base:
        base_rows = read_rows(args.base)
        base_fields = list(base_rows[0].keys()) if base_rows else []
        base = {row["id"]: row for row in base_rows}
        order = {row_id: n for n, row_id in enumerate(base)}  # lignes dans l'ordre du fichier de base
        records.sort(key=lambda r: order.get(r["id"], len(order)))
    fields = list(base_fields)
    for record in records:
        fields += [k for k in record if k not in fields]

    rows = [{**base.get(r["id"], {}), **r} for r in records]
    if args.tri:
        def sort_key(row):
            try:
                return float(row.get(args.tri) or 0)
            except ValueError:
                return 0.0
        rows.sort(key=sort_key, reverse=True)

    with open(args.sortie, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} lignes écrites dans {args.sortie}" + (f" ; base : {len(base)} lignes" if args.base else ""))
    missing = [i for i in base if i not in {r["id"] for r in records}] if args.base else []
    if missing:
        print(f"{len(missing)} entreprise(s) de la base sans fichier JSON : {', '.join(missing[:10])}")
    reference = set(base) if args.base else ({r["id"] for r in read_rows("1-collecte.csv")}
                                             if os.path.exists("1-collecte.csv") else set())
    unknown = [r["id"] for r in records if reference and r["id"] not in reference]
    if unknown:
        print(f"ATTENTION : {len(unknown)} fiche(s) dont l'identifiant n'existe pas dans "
              f"{args.base or '1-collecte.csv'} (zéro initial perdu, faute de frappe ?) : {', '.join(unknown[:10])}")
    for error in errors:
        print(f"Fichier ignoré, JSON invalide : {error}")
    state = refresh_waves()
    if state:
        print("Point des vagues mis à jour (vagues.json) : " + " ; ".join(
            f"vague {w['numero']} : {w['envoyees']} envoyées, {w['gardees']} gardées, {w['qualifiees']} qualifiées"
            for w in state.get("vagues", [])))


if __name__ == "__main__":
    main()
