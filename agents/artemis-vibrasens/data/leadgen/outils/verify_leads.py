"""Revérifie indépendamment le site de chaque lead qualifié d'une campagne (nom, localisation, SIREN).

Usage (sur le VPS) : docker exec vibrasens-artemis-vibrasens-1 /opt/leadgen/venv/bin/python /opt/data/leadgen/outils/verify_leads.py <campagne>
"""
import csv
import os
import sys

sys.path.insert(0, "/opt/data/leadgen/outils")
from common import PageError, PoliteClient, status_kind  # noqa: E402
from find_website import check_candidate  # noqa: E402

base = f"/workspace/campagnes/{sys.argv[1]}/"
os.chdir(base)  # pour que find_website apprenne les mots courants du vivier de la campagne
collecte = {r["id"]: r for r in csv.DictReader(open(base + "1-collecte.csv", encoding="utf-8-sig"))}
leads = [r for r in csv.DictReader(open(base + "3-qualification.csv", encoding="utf-8-sig")) if status_kind(r.get("statut", "")) == "qualifié"]
client = PoliteClient(delay=1.0)
for lead in leads:
    row = collecte[lead["id"]]
    try:
        score, proof, _ = check_candidate(client, row, lead["site_web"])
    except PageError as exc:
        score, proof = -1, f"illisible : {exc}"
    verdict = "OK" if score >= 60 else "À VÉRIFIER"
    print(f"{verdict:10} {score:4} {row['nom'][:32]:32} {lead['site_web'][:36]:36} {proof[:60]}", flush=True)
