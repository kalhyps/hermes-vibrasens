"""Pré-classe le vivier selon les règles de la campagne (classement.json), écrites par le collecteur d'après le brief.

Les règles disent :
  - « exclure » : ce qui, d'après les données, est certainement hors cible (effectif connu sous le minimum du brief,
    activité clairement étrangère, exclusion du brief). Une donnée inconnue n'exclut jamais. L'entreprise reste dans
    le vivier avec la raison (colonne exclusion) et n'est jamais envoyée en vague ;
  - « points » : ce qui rend une entreprise plus probable comme lead, avec un poids tiré du brief.
Classement : score décroissant, puis nombre de sources, puis données utiles à l'enrichissement (site, téléphone).
L'outil écrit dans le vivier : exclusion, score_collecte, raisons_collecte, priorite (1 = à analyser en premier).
La vague déjà attribuée à une entreprise ne change pas.

Format de classement.json (détails et exemples : skill collecte-entreprises, references/classement.md) :
  {"exclure": [{"si": {"champ": "effectif_min", "inferieur_a": 10}, "raison": "moins de 10 salariés (registre)"}],
   "points":  [{"si": {"champ": "code_activite", "parmi": ["25.62A", "25.62B"]}, "points": 40, "raison": "activité exacte"},
               {"si": [{"champ": "effectif_min", "renseigne": false}, {"champ": "employeur", "egal": "oui"}],
                "points": 10, "raison": "employeur, effectif inconnu"}]}
Une condition porte sur un champ du vivier, avec un seul test parmi : egal, parmi, commence_par, contient (mots,
sans accents ni casse), au_moins, inferieur_a (nombres), renseigne (true / false). « si » accepte une condition ou une
liste de conditions (toutes vraies) ; « si_une_de » une liste dont une seule suffit. Une exclusion peut avoir
« sauf » (une condition ou une liste, dont une seule suffit) : par exemple, ne pas exclure sur la seule catégorie d'une
base de lieux une entreprise dont le nom ou le code d'activité officiel dit le contraire.

Exemple :
  python rank_pool.py                    (classement.json et vivier.csv du dossier courant)
  python rank_pool.py --apercu 20        (affiche aussi les 20 premières et un échantillon d'exclues)
"""
import argparse
import csv
import hashlib
import json
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import normalize, read_rows  # noqa: E402

csv.field_size_limit(10_000_000)
TESTS = ("egal", "parmi", "commence_par", "contient", "au_moins", "inferieur_a", "renseigne")
RANK_FIELDS = ["exclusion", "score_collecte", "raisons_collecte", "priorite"]


class RuleError(ValueError):
    pass


def number(value):
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def check_condition(condition, fields):
    if not isinstance(condition, dict) or "champ" not in condition:
        raise RuleError(f"condition sans « champ » : {condition}")
    if condition["champ"] not in fields:
        raise RuleError(f"champ inconnu : {condition['champ']} (champs du vivier : {', '.join(fields)})")
    tests = [t for t in condition if t != "champ"]
    if len(tests) != 1 or tests[0] not in TESTS:
        raise RuleError(f"une condition a un seul test parmi {', '.join(TESTS)} : {condition}")


def conditions_of(rule, fields):
    if "si" in rule:
        conditions, mode = rule["si"], all
    elif "si_une_de" in rule:
        conditions, mode = rule["si_une_de"], any
    else:
        raise RuleError(f"règle sans « si » ni « si_une_de » : {rule}")
    conditions = conditions if isinstance(conditions, list) else [conditions]
    for condition in conditions:
        check_condition(condition, fields)
    return conditions, mode


def matches(condition, row):
    value = (row.get(condition["champ"]) or "").strip()
    test = next(t for t in condition if t != "champ")
    expected = condition[test]
    if test == "renseigne":
        return bool(value) == bool(expected)
    if not value:
        return False  # une donnée inconnue ne remplit aucun autre test
    if test == "egal":
        return normalize(value) == normalize(str(expected))
    if test == "parmi":
        return normalize(value) in {normalize(str(e)) for e in expected} or \
            any(normalize(part) in {normalize(str(e)) for e in expected} for part in value.split(";"))
    if test == "commence_par":
        options = expected if isinstance(expected, list) else [expected]
        parts = [normalize(p) for p in value.split(";")]
        return any(p.startswith(normalize(str(o))) for p in parts for o in options)
    if test == "contient":
        options = expected if isinstance(expected, list) else [expected]
        text = f" {normalize(value)} "
        return any(f" {normalize(str(o))}" in text for o in options)
    measured, threshold = number(value), number(expected)
    if measured is None or threshold is None:
        return False
    return measured >= threshold if test == "au_moins" else measured < threshold


def applies(rule, row, fields):
    conditions, mode = conditions_of(rule, fields)
    if not mode(matches(c, row) for c in conditions):
        return False
    exceptions = rule.get("sauf") or []
    return not any(matches(c, row) for c in (exceptions if isinstance(exceptions, list) else [exceptions]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--regles", default="classement.json")
    parser.add_argument("--vivier", default="vivier.csv")
    parser.add_argument("--apercu", type=int, default=0, help="afficher les N premières et un échantillon d'exclues")
    args = parser.parse_args()

    if not os.path.exists(args.regles):
        sys.exit(f"RÈGLES ABSENTES : écrivez {args.regles} (voir la skill collecte-entreprises, references/classement.md)")
    with open(args.regles, encoding="utf-8") as handle:
        try:
            rules = json.load(handle)
        except json.JSONDecodeError as exc:
            sys.exit(f"RÈGLES INVALIDES : {args.regles} n'est pas du JSON valide ({exc})")
    rows = read_rows(args.vivier)
    if not rows:
        sys.exit(f"VIVIER VIDE : {args.vivier} (lancez merge_sources.py)")
    fields = [f for f in rows[0].keys() if f not in RANK_FIELDS]
    try:
        for rule in rules.get("exclure", []) + rules.get("points", []):
            conditions_of(rule, fields)
            exceptions = rule.get("sauf") or []
            for condition in exceptions if isinstance(exceptions, list) else [exceptions]:
                check_condition(condition, fields)
            if rule in rules.get("exclure", []) and not rule.get("raison"):
                raise RuleError(f"exclusion sans « raison » : {rule}")
            if rule in rules.get("points", []) and number(rule.get("points")) is None:
                raise RuleError(f"règle de points sans nombre de « points » : {rule}")
    except RuleError as exc:
        sys.exit(f"RÈGLE INVALIDE : {exc}")

    for row in rows:
        row["exclusion"] = next((rule["raison"] for rule in rules.get("exclure", []) if applies(rule, row, fields)), "")
        earned = [(number(rule["points"]), rule.get("raison", "")) for rule in rules.get("points", [])
                  if applies(rule, row, fields)]
        row["score_collecte"] = f"{sum(p for p, _ in earned):g}"
        row["raisons_collecte"] = " ; ".join(f"{reason} ({points:+g})" for points, reason in earned)

    def order(row):
        useful = sum(1 for f in ("site_web", "telephone", "effectif", "ca", "adresse") if row.get(f))
        # À égalité, un ordre stable mais sans lien avec le nom : une vague ne doit pas se limiter aux noms en A ou B.
        tie = hashlib.sha1(row["id"].encode()).hexdigest()
        return (bool(row["exclusion"]), -float(row["score_collecte"]), -int(row.get("nb_sources") or 1), -useful, tie)

    rows.sort(key=order)
    rank = 0
    for row in rows:
        if row["exclusion"]:
            row["priorite"] = ""
        else:
            rank += 1
            row["priorite"] = rank
    out_fields = [f for f in rows[0].keys() if f not in RANK_FIELDS] + RANK_FIELDS
    with open(args.vivier, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=out_fields)
        writer.writeheader()
        writer.writerows(rows)

    excluded = Counter(r["exclusion"] for r in rows if r["exclusion"])
    kept = [r for r in rows if not r["exclusion"]]
    scores = Counter(r["score_collecte"] for r in kept)
    waiting = sum(1 for r in kept if not r.get("vague"))
    print(f"{len(rows)} entreprises classées : {len(kept)} candidates (dont {waiting} en réserve, sans vague), "
          f"{sum(excluded.values())} exclues.")
    print("Exclusions : " + (" ; ".join(f"{reason} : {n}" for reason, n in excluded.most_common()) or "aucune"))
    print("Candidates par score : " + " ; ".join(f"{s} : {n}" for s, n in
                                                 sorted(scores.items(), key=lambda i: -float(i[0]))))
    if args.apercu:
        print(f"\n{args.apercu} premières :")
        for row in kept[:args.apercu]:
            print(f"  {row['priorite']}. {row['nom']} ({row['ville']}) score {row['score_collecte']} : {row['raisons_collecte']}")
        sample = random.sample([r for r in rows if r["exclusion"]], min(args.apercu, sum(excluded.values())))
        if sample:
            print("\nÉchantillon d'exclues :")
            for row in sample:
                print(f"  {row['nom']} ({row['ville']}) : {row['exclusion']}")


if __name__ == "__main__":
    main()
