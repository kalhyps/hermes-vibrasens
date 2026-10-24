"""Contrôles de la collecte : à lancer après chaque fusion et avant d'envoyer une vague.

Par défaut (vivier.csv et sources/ du dossier courant) :
  - BLOQUANT : colonnes obligatoires absentes, identifiant, nom, pays ou source vide, identifiants en double,
    source incomplète (connecteur interrompu ou lancé en essai), pays inattendu (avec --pays) ;
  - À VÉRIFIER : doublons probables restants (même domaine, ou même nom distinctif dans la même ville),
    taux de remplissage des colonnes, part des effectifs inconnus, apport de chaque source ;
  - ÉCHANTILLON : quelques lignes tirées au hasard dans chaque source, avec leur référence, à ouvrir pour vérifier
    que l'entreprise existe, est active, est dans la zone et fait l'activité annoncée.
--chercher "Nom 1;Nom 2" : contrôle de rappel, cherche des entreprises connues dans le vivier (et dans les exclues).
--references [fichier] : même contrôle avec les vrais clients du dossier client (/workspace/client/references.csv par
  défaut) qui sont dans les pays de la campagne : où sortent-ils dans le classement ? Un vrai client exclu ou loin
  dans le classement signale une règle de classement.json à revoir.
--fichier sources/<source>.csv : contrôle une seule source (essai d'un nouveau connecteur).
Code de sortie : 1 s'il reste un problème bloquant, 0 sinon.

Exemples :
  python check_collection.py --pays FR
  python check_collection.py --chercher "Franck Pignard;Bouverat Pernat"
  python check_collection.py --references
  python check_collection.py --fichier essai/us-epa-echo.csv --echantillon 5
"""
import argparse
import csv
import glob
import json
import os
import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import domain_of, name_tokens, normalize, read_rows, root_domain  # noqa: E402
from connector_base import FIELDS, REQUIRED  # noqa: E402
from merge_sources import SHARED_DOMAINS  # noqa: E402

csv.field_size_limit(10_000_000)


def check_rows(rows, label, countries, problems, warnings):
    if not rows:
        problems.append(f"{label} : aucune ligne")
        return
    absent = [f for f in FIELDS if f not in rows[0] and f != "meme_que"]
    if absent:
        problems.append(f"{label} : colonnes absentes du format commun : {', '.join(absent)}")
    for field in REQUIRED:
        empty = sum(1 for r in rows if not (r.get(field) or "").strip())
        if empty:
            problems.append(f"{label} : {empty} lignes sans « {field} »")
    duplicates = [i for i, n in Counter(r.get("id") for r in rows).items() if n > 1]
    if duplicates:
        problems.append(f"{label} : {len(duplicates)} identifiants en double, ex. {', '.join(duplicates[:5])}")
    if countries:
        foreign = Counter(r.get("pays", "") for r in rows if r.get("pays", "").upper() not in countries)
        if foreign:
            problems.append(f"{label} : pays inattendus : " + ", ".join(f"{c or 'vide'} ({n})" for c, n in foreign.items()))


def fill_rates(rows):
    columns = ["site_web", "telephone", "email", "effectif", "code_activite", "code_postal", "adresse", "ca"]
    return " ; ".join(f"{c} {100 * sum(1 for r in rows if r.get(c)) // max(len(rows), 1)} %" for c in columns)


def probable_duplicates(rows, links_path="liens.csv"):
    """Groupes de lignes qui se ressemblent, sauf ceux déjà tranchés « different » dans liens.csv."""
    settled = {frozenset((l["id_a"].strip(), l["id_b"].strip())) for l in read_rows(links_path)
               if l.get("relation", "").strip() == "different"} if os.path.exists(links_path) else set()
    groups = defaultdict(list)
    for row in rows:
        if row.get("site_web"):
            domain = root_domain(domain_of(row["site_web"]))
            if domain and domain not in SHARED_DOMAINS:
                groups[("domaine", row.get("pays"), domain)].append(row)
        tokens = " ".join(name_tokens(row.get("nom", "")))
        if tokens and row.get("ville"):
            groups[("nom+ville", row.get("pays"), tokens, normalize(row["ville"]))].append(row)
    return [members for members in groups.values() if len(members) > 1 and not all(
        frozenset((a["id"], b["id"])) in settled for n, a in enumerate(members) for b in members[n + 1:])]


def search(rows, names):
    for name in names:
        wanted = set(name_tokens(name)) or set(normalize(name).split())
        found = [r for r in rows if wanted and wanted <= set(name_tokens(r.get("nom", ""))) | set(normalize(r.get("nom", "")).split())]
        if not found:
            print(f"  ABSENTE : {name}")
        for row in found[:3]:
            state = (f"exclue ({row['exclusion']})" if row.get("exclusion")
                     else f"priorité {row.get('priorite') or '?'}" + (f", vague {row['vague']}" if row.get("vague") else ""))
            print(f"  trouvée : {name} -> {row['nom']} ({row.get('ville', '')}), {row['id']}, {state}, "
                  f"sources : {row.get('sources_collecte') or row.get('source_nom', '')}")


def check_references(rows, path):
    """Les vrais clients du dossier client dans le vivier : trouvés ou non, exclus ou non, et leur rang."""
    if not os.path.exists(path):
        print(f"Pas de dossier client : {path} absent (skill cadrage-client de l'orchestrateur).")
        return
    references = [r for r in read_rows(path) if r.get("nom") and (r.get("type") or "client") == "client"]
    countries = {r.get("pays", "").upper() for r in rows}
    local = [r for r in references if (r.get("pays") or "").upper() in countries or not r.get("pays")]
    print(f"Contrôle avec les clients de référence : {len(local)} sur {len(references)} dans les pays de la campagne "
          f"({', '.join(sorted(c for c in countries if c))}).")
    ranked = sorted((r for r in rows if r.get("priorite")), key=lambda r: int(r["priorite"]))
    candidates = len(ranked)
    found, excluded, absent = 0, 0, 0
    for ref in local:
        wanted = set(name_tokens(ref["nom"])) or set(normalize(ref["nom"]).split())
        matches = [r for r in rows if wanted <= set(name_tokens(r.get("nom", ""))) | set(normalize(r.get("nom", "")).split())]
        if ref.get("ville"):
            city = normalize(ref["ville"])
            matches = [r for r in matches if normalize(r.get("ville", "")) == city] or matches
        if not matches:
            absent += 1
            print(f"  ABSENT  : {ref['nom']} ({ref.get('ville', '')}) : pas dans le vivier (source manquante, autre code, hors zone ?)")
            continue
        row = matches[0]
        if row.get("exclusion"):
            excluded += 1
            print(f"  EXCLU   : {ref['nom']} -> {row['nom']} ({row.get('ville', '')}) : « {row['exclusion']} » : règle à revoir")
        else:
            found += 1
            rank = row.get("priorite") or "?"
            print(f"  rang {rank:>5} sur {candidates} : {ref['nom']} -> {row['nom']} ({row.get('ville', '')}), "
                  f"score {row.get('score_collecte', '')}, sources : {row.get('sources_collecte') or row.get('source_nom', '')}")
    print(f"Références : {found} classées, {excluded} exclues, {absent} absentes. "
          "Les vrais clients doivent sortir en tête : sinon, revois classement.json ou les sources.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vivier", default="vivier.csv")
    parser.add_argument("--dossier", default="sources")
    parser.add_argument("--fichier", help="contrôler une seule source")
    parser.add_argument("--pays", default="", help="codes ISO attendus, séparés par des virgules")
    parser.add_argument("--chercher", default="", help="noms d'entreprises connues, séparés par des points-virgules")
    parser.add_argument("--references", nargs="?", const="/workspace/client/references.csv",
                        help="contrôle avec les clients de référence du dossier client (fichier à défaut : "
                             "/workspace/client/references.csv)")
    parser.add_argument("--echantillon", type=int, default=3, help="lignes tirées au hasard par source (défaut : 3)")
    args = parser.parse_args()

    countries = {c.strip().upper() for c in args.pays.split(",") if c.strip()}
    problems, warnings = [], []
    if args.fichier:
        sources = {os.path.basename(args.fichier)[:-4]: read_rows(args.fichier)}
        rows = sources[next(iter(sources))]
    else:
        sources = {os.path.basename(p)[:-4]: read_rows(p) for p in sorted(glob.glob(os.path.join(args.dossier, "*.csv")))}
        if not os.path.exists(args.vivier):
            sys.exit(f"VIVIER ABSENT : {args.vivier} (lancez merge_sources.py)")
        rows = read_rows(args.vivier)

    if args.chercher:
        print("Contrôle de rappel :")
        search(rows, [n.strip() for n in args.chercher.split(";") if n.strip()])
        return
    if args.references:
        check_references(rows, args.references)
        return

    for name, source_rows in sources.items():
        check_rows(source_rows, f"source {name}", countries, problems, warnings)
        stats_path = os.path.join(os.path.dirname(args.fichier) if args.fichier else args.dossier, f"{name}-stats.json")
        stats = json.load(open(stats_path, encoding="utf-8")) if os.path.exists(stats_path) else {}
        if not stats.get("termine"):
            problems.append(f"source {name} : incomplète (relancer la même commande jusqu'à TERMINÉ, "
                            "ou --recommencer sans --limite après un essai)")
        if stats.get("plafonds"):
            warnings.append(f"source {name} : {len(stats['plafonds'])} requêtes au plafond, à découper : "
                            f"{stats['plafonds'][0]}")
    if not args.fichier:
        check_rows(rows, "vivier", countries, problems, warnings)
        if rows and "priorite" not in rows[0]:
            warnings.append("vivier non classé : lancez rank_pool.py")
        if os.path.exists("1-collecte.csv"):
            sent = read_rows("1-collecte.csv")
            duplicates = [i for i, n in Counter(r["id"] for r in sent).items() if n > 1]
            if duplicates:
                problems.append(f"1-collecte.csv : {len(duplicates)} identifiants envoyés deux fois : {', '.join(duplicates[:5])}")
            missing = {r["id"] for r in sent} - {r["id"] for r in rows}
            if missing:
                warnings.append(f"1-collecte.csv : {len(missing)} entreprises envoyées absentes du vivier "
                                f"(une source a été retirée ?) : {', '.join(sorted(missing)[:5])}")

    print(f"{len(rows)} lignes contrôlées" + ("" if args.fichier else f" dans le vivier, {len(sources)} sources"))
    print(f"Remplissage : {fill_rates(rows)}")
    unknown = sum(1 for r in rows if not r.get("effectif"))
    print(f"Effectif inconnu : {unknown} ({100 * unknown // max(len(rows), 1)} %) : à garder, le classement et "
          "l'enrichissement trancheront.")
    duplicates = probable_duplicates(rows)
    if duplicates:
        warnings.append(f"{len(duplicates)} groupes de doublons probables : vérifie-les, puis inscris le verdict "
                        "dans liens.csv (relation meme ou different) et relance merge_sources.py")
        for members in duplicates[:10]:
            print("  doublon probable : " + " | ".join(f"{m['nom']} ({m.get('ville', '')}, {m['id']})" for m in members))
    for warning in warnings:
        print(f"À VÉRIFIER : {warning}")
    print("\nÉchantillon à vérifier (ouvre la référence de chaque ligne) :")
    for name, source_rows in sources.items():
        for row in random.sample(source_rows, min(args.echantillon, len(source_rows))):
            print(f"  [{name}] {row.get('nom')} ({row.get('ville', '')}, {row.get('code_activite', '')}) : "
                  f"{row.get('source')}" + (f" ; site {row['site_web']}" if row.get("site_web") else ""))
    for problem in problems:
        print(f"BLOQUANT : {problem}")
    print("Contrôles : " + ("BLOQUANTS À CORRIGER" if problems else "aucun problème bloquant"))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
