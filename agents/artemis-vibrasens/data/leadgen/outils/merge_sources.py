"""Fusionne les sources de la campagne (sources/*.csv) en un seul vivier : vivier.csv, une ligne par entreprise.

Deux lignes sont la même entreprise si elles ont, dans cet ordre :
  1. le même identifiant, ou un lien explicite (colonne « meme_que » d'une source, ou liens.csv) ;
  2. le même numéro officiel dans le même registre ;
  3. le même domaine web, dans le même pays (sauf hébergeurs et réseaux sociaux partagés) ;
  4. le même nom distinctif (sans forme juridique) et le même code postal, ou la même ville, dans le même pays.
Deux numéros officiels différents d'un même registre ne sont jamais fusionnés, ni deux lignes déclarées
différentes dans liens.csv.

Chaque entreprise du vivier garde : les valeurs les plus fiables (registre officiel d'abord, puis la source la plus
complète), la liste de ses sources (sources_collecte, nb_sources), les autres références (autres_sources), ses indices
réunis, et sa vague si elle a déjà été envoyée en enrichissement (son identifiant ne change plus).

liens.csv (facultatif, écrit par l'agent après vérification) : colonnes id_a, id_b, relation (« meme » ou « different »).

Le vivier est à reclasser après chaque fusion (rank_pool.py).

Exemple :
  python merge_sources.py
"""
import argparse
import csv
import glob
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import domain_of, name_tokens, normalize, read_rows, root_domain  # noqa: E402
from connector_base import FIELDS, write_json  # noqa: E402

csv.field_size_limit(10_000_000)
POOL_FIELDS = [f for f in FIELDS if f != "meme_que"] + ["sources_collecte", "nb_sources", "autres_sources", "vague"]
# Domaines partagés par des entreprises sans lien entre elles : ils ne prouvent pas que deux lignes sont la même.
SHARED_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "google.com",
    "goo.gl", "business.site", "wixsite.com", "wix.com", "jimdo.com", "jimdofree.com", "wordpress.com",
    "blogspot.com", "weebly.com", "squarespace.com", "webnode.fr", "webnode.com", "site123.me", "free.fr",
    "orange.fr", "wanadoo.fr", "gmail.com", "pagesjaunes.fr", "yelp.com", "yelp.fr", "yell.com",
    "societe.com", "kompass.com", "europages.fr", "europages.com", "bit.ly", "linktr.ee", "about.me",
}


class Groups:
    """Union-find des lignes, avec les garde-fous : numéros officiels et liens « different »."""

    def __init__(self, rows, forbidden):
        self.parent = list(range(len(rows)))
        self.registries = [{row["registre"]: row["id_registre"]} if row["registre"] and row["id_registre"] else {}
                           for row in rows]
        self.ids = [{row["id"]} for row in rows]
        self.forbidden = forbidden
        self.conflicts = 0

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if any(self.registries[ra][k] != self.registries[rb][k] for k in self.registries[ra].keys() & self.registries[rb].keys()):
            self.conflicts += 1
            return False
        if any((x in self.ids[ra] and y in self.ids[rb]) or (y in self.ids[ra] and x in self.ids[rb])
               for x, y in self.forbidden):
            return False
        self.parent[rb] = ra
        self.registries[ra].update(self.registries[rb])
        self.ids[ra] |= self.ids[rb]
        return True


def load_sources(folder):
    rows = []
    for path in sorted(glob.glob(os.path.join(folder, "*.csv"))):
        for row in read_rows(path):
            row = {field: (row.get(field) or "").strip() for field in FIELDS}
            row["source_nom"] = row["source_nom"] or os.path.basename(path)[:-4]
            if row["id"] and row["nom"]:
                rows.append(row)
    return rows


def keys_of(row):
    """Clés de rapprochement d'une ligne, de la plus sûre à la moins sûre : (type, valeur)."""
    country = row["pays"].upper()
    keys = [("id", row["id"])]
    if row["registre"] and row["id_registre"]:
        keys.append(("registre", f"{row['registre']}:{row['id_registre']}"))
    if row["site_web"]:
        domain = root_domain(domain_of(row["site_web"]))
        if domain and "." in domain and domain not in SHARED_DOMAINS:
            keys.append(("domaine", f"{country}:{domain}"))
    tokens = " ".join(name_tokens(row["nom"]))
    if tokens:
        if row["code_postal"]:
            keys.append(("nom+code_postal", f"{country}:{tokens}:{normalize(row['code_postal'])}"))
        if row["ville"]:
            keys.append(("nom+ville", f"{country}:{tokens}:{normalize(row['ville'])}"))
    return keys


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dossier", default="sources", help="dossier des sources (défaut : sources)")
    parser.add_argument("--sortie", default="vivier.csv")
    parser.add_argument("--liens", default="liens.csv", help="liens vérifiés entre lignes (défaut : liens.csv, facultatif)")
    args = parser.parse_args()

    rows = load_sources(args.dossier)
    if not rows:
        sys.exit(f"AUCUNE SOURCE : pas de fichier CSV non vide dans {args.dossier}/")
    links = read_rows(args.liens) if os.path.exists(args.liens) else []
    same_pairs = [(l["id_a"].strip(), l["id_b"].strip()) for l in links if l.get("relation", "").strip() == "meme"]
    forbidden = [(l["id_a"].strip(), l["id_b"].strip()) for l in links if l.get("relation", "").strip() == "different"]
    previous = {r["id"]: r for r in read_rows(args.sortie)} if os.path.exists(args.sortie) else {}
    frozen = {r["id"]: r.get("vague", "") for r in read_rows("1-collecte.csv")} if os.path.exists("1-collecte.csv") else {}

    groups = Groups(rows, forbidden)
    by_id = defaultdict(list)
    for index, row in enumerate(rows):
        by_id[row["id"]].append(index)
    merges = Counter()
    unknown_links = []
    # 1. liens explicites : même identifiant, « meme_que », liens.csv
    for index, row in enumerate(rows):
        if row["meme_que"]:
            same_pairs.append((row["id"], row["meme_que"]))
    for a, b in same_pairs:
        if a not in by_id or b not in by_id:
            unknown_links.append(f"{a} = {b}")
            continue
        if groups.union(by_id[a][0], by_id[b][0]):
            merges["lien explicite"] += 1
    # 2 à 4. clés de rapprochement, des plus sûres aux moins sûres
    for kind in ("id", "registre", "domaine", "nom+code_postal", "nom+ville"):
        first = {}
        for index, row in enumerate(rows):
            for key_kind, value in keys_of(row):
                if key_kind != kind:
                    continue
                if value in first:
                    if groups.union(first[value], index):
                        merges[kind] += 1
                else:
                    first[value] = index

    clusters = defaultdict(list)
    for index in range(len(rows)):
        clusters[groups.find(index)].append(index)
    source_order = {name: n for n, name in enumerate(sorted({r["source_nom"] for r in rows}))}

    def reliability(index):
        row = rows[index]
        return (0 if row["id_registre"] else 1, -sum(1 for f in FIELDS if row[f]), source_order[row["source_nom"]])

    pool, contribution = [], defaultdict(Counter)
    for members in clusters.values():
        members = sorted(members, key=reliability)
        member_rows = [rows[i] for i in members]
        entity = {field: next((r[field] for r in member_rows if r[field]), "") for field in FIELDS if field != "meme_que"}
        ids = [r["id"] for r in member_rows]
        entity["id"] = next((i for i in ids if i in frozen), member_rows[0]["id"])
        entity["source"] = member_rows[0]["source"]
        entity["source_nom"] = member_rows[0]["source_nom"]
        hints = []
        for r in member_rows:
            hints += [h.strip() for h in r["indices"].split(" ; ") if h.strip() and h.strip() not in hints]
        entity["indices"] = " ; ".join(hints)
        names = sorted({r["source_nom"] for r in member_rows})
        entity["sources_collecte"] = " ; ".join(names)
        entity["nb_sources"] = len(names)
        entity["autres_sources"] = " ; ".join(dict.fromkeys(r["source"] for r in member_rows[1:]
                                                            if r["source"] != entity["source"]))
        entity["vague"] = frozen.get(entity["id"]) or next(
            (previous[i].get("vague", "") for i in ids if i in previous and previous[i].get("vague")), "")
        pool.append(entity)
        for name in names:
            contribution[name]["entreprises"] += 1
            contribution[name]["seule_source" if len(names) == 1 else "partagees"] += 1
    for row in rows:
        contribution[row["source_nom"]]["lignes"] += 1

    pool.sort(key=lambda e: (e["pays"], normalize(e["nom"])))
    with open(args.sortie, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=POOL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(pool)

    stats = {
        "lignes_lues": len(rows), "entreprises": len(pool), "fusions": dict(merges),
        "conflits_de_numeros_officiels": groups.conflicts, "liens_inconnus": unknown_links,
        "multi_sources": sum(1 for e in pool if e["nb_sources"] > 1), "deja_en_vague": sum(1 for e in pool if e["vague"]),
        "sources": {name: dict(counts) for name, counts in sorted(contribution.items())},
    }
    write_json(os.path.splitext(args.sortie)[0] + "-stats.json", stats)
    print(f"{len(pool)} entreprises dans {args.sortie}, à partir de {len(rows)} lignes ; "
          f"{stats['multi_sources']} trouvées dans plusieurs sources.")
    print("Apport de chaque source : lignes | entreprises | trouvées par elle seule | partagées avec une autre source")
    for name, counts in sorted(contribution.items(), key=lambda item: -item[1]["seule_source"]):
        print(f"  {name} : {counts['lignes']} | {counts['entreprises']} | {counts['seule_source']} | {counts['partagees']}")
    print("Fusions par type de rapprochement : " + (", ".join(f"{k} {v}" for k, v in merges.items()) or "aucune"))
    if groups.conflicts:
        print(f"{groups.conflicts} rapprochements refusés : numéros officiels différents (entreprises distinctes).")
    if unknown_links:
        print(f"ATTENTION : {len(unknown_links)} liens vers des identifiants absents des sources : {', '.join(unknown_links[:5])}")
    print("Étape suivante : classer le vivier (rank_pool.py).")


if __name__ == "__main__":
    main()
