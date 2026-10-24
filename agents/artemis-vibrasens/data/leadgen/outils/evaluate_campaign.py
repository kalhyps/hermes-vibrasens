"""Mesure une campagne : couverture de chaque étape, statuts, scores.

Usage (sur le VPS) : docker exec vibrasens-artemis-vibrasens-1 python3 /opt/data/leadgen/outils/evaluate_campaign.py <campagne>
"""
import collections
import csv
import json
import os
import sys

base = f"/workspace/campagnes/{sys.argv[1]}/"


def rows(name):
    path = base + name
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


collecte = rows("1-collecte.csv")
sites = rows("sites.csv")
lecture = rows("lecture.csv")
enrich = rows("2-enrichissement.csv")
qualif = rows("3-qualification.csv")
found = [s for s in sites if s.get("site_web")]
read_ok = [r for r in lecture if r.get("pages_lues", "0") not in ("", "0")]


def fiche_ids(folder):
    """Identifiants des fiches JSON d'un dossier (un fichier par entreprise ou par lot)."""
    ids = set()
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                content = json.load(handle)
            ids.update(str(f.get("id")) for f in (content if isinstance(content, list) else [content]))
        except (OSError, ValueError, AttributeError):
            pass
    return ids


json_files = len(fiche_ids(base + "enrichissement"))


def load_json(name):
    try:
        with open(base + name, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


pool = rows("vivier.csv")
if pool:
    excluded = collections.Counter(r["exclusion"] for r in pool if r.get("exclusion"))
    sources = load_json("vivier-stats.json").get("sources", {})
    print(f"Vivier                  : {len(pool)} entreprises, {sum(excluded.values())} exclues, "
          f"{sum(1 for r in pool if not r.get('exclusion') and not r.get('vague'))} en réserve")
    for name, counts in sorted(sources.items(), key=lambda item: -item[1].get("entreprises", 0)):
        print(f"  source {name:28} {counts.get('entreprises', 0):5} entreprises, {counts.get('seule_source', 0)} seule")
    for reason, count in excluded.most_common(6):
        print(f"  exclues : {count:4} {reason[:70]}")
waves = load_json("vagues.json")
for wave in waves.get("vagues", []):
    result = (f"{wave['qualifiees']} qualifiées, {wave['a_revoir']} à revoir, rendement {wave['rendement']}"
              if wave.get("qualification_faite") else "qualification pas encore faite")
    print(f"Vague {wave['numero']:<18}: {wave['envoyees']} envoyées, {wave['gardees']} gardées ; {result}")
if waves.get("objectif"):
    print(f"Objectif                : {waves['objectif']} leads qualifiés")

print(f"Collectées (envoyées)   : {len(collecte)}")
print(f"Recherche de site faite : {len(sites)} / {len(collecte)}")
print(f"Sites trouvés           : {len(found)} ({100 * len(found) // max(len(collecte), 1)} %) "
      f"{dict(collections.Counter(s['confiance'] for s in sites))}")
print(f"Sites lus               : {len(read_ok)} / {len(found)}")
print(f"Fiches enrichissement   : {json_files} / {len(collecte)}")
cols = list(enrich[0]) if enrich else []
status_col = "statut" if "statut" in cols else next((c for c in cols if c.startswith("statut")), None)
if status_col:
    print(f"Statuts enrichissement  : {dict(collections.Counter(r[status_col] for r in enrich))}")
statuses = collections.Counter((r.get("statut") or "?").strip() for r in qualif)
print(f"Qualification           : {len(qualif)} lignes, statuts {dict(statuses)}")
scores = []
for r in qualif:
    try:
        scores.append(float(r.get("score") or 0))
    except ValueError:
        pass
if scores:
    print(f"Scores                  : max {max(scores):.0f}, à 100 : {sum(s >= 100 for s in scores)}, "
          f">= 60 : {sum(s >= 60 for s in scores)}, moyenne {sum(scores) / len(scores):.0f}")
if os.path.exists(base + "leads.csv"):
    with open(base + "leads.csv", encoding="utf-8-sig", newline="") as handle:
        leads = list(csv.DictReader(handle, delimiter=";"))
    email = next((k for k in ("Email de l'entreprise", "Email", "email") if leads and k in leads[0]), "Email")
    phone = next((k for k in ("Téléphone (standard)", "Téléphone", "telephone") if leads and k in leads[0]), "Téléphone")
    status = next((k for k in ("Statut", "statut") if leads and k in leads[0]), "Statut")
    print(f"Livrable leads.csv      : {len(leads)} leads ({dict(collections.Counter(r.get(status, '') for r in leads))}), "
          f"dont {sum(bool(r.get(email) or r.get(phone)) for r in leads)} avec email ou téléphone")
    print(f"                          {sum(bool(r.get(email)) for r in leads)} avec email, "
          f"{sum(bool(r.get(phone)) for r in leads)} avec téléphone"
          + (" ; leads.xlsx présent" if os.path.exists(base + "leads.xlsx") else " ; leads.xlsx ABSENT"))
firecrawl = load_json("firecrawl.json")
if firecrawl:
    budget = f" sur un budget de {firecrawl['budget']}" if firecrawl.get("budget") else ""
    print(f"Firecrawl               : environ {firecrawl.get('credits_estimes', 0)} crédits{budget} "
          f"({firecrawl.get('recherches', 0)} recherches, {firecrawl.get('pages', 0)} pages) {firecrawl.get('par_outil', {})}")
print(f"Bilan                   : {'présent' if os.path.exists(base + 'bilan.md') else 'ABSENT'}")
