#!/usr/bin/env python3
"""Enrichissement des contacts validés via Dropcontact.

Dropcontact retrouve l'email professionnel (et parfois le téléphone) à partir
du nom, de l'entreprise et du site web, sans exploiter les données LinkedIn.
On n'envoie que les lignes dont le statut est « valide » (validées à la main).

Usage :
  python3 enrich_dropcontact.py --input out/linkedin_contacts.csv \
      --out out/linkedin_contacts_enrichis.csv
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.request

API = "https://api.dropcontact.io/batch"
BATCH_SIZE = 250  # maximum accepté par Dropcontact par requête

OUT_EXTRA = ["prenom", "nom_famille", "email", "qualification_email", "telephone", "site_web_dropcontact"]


def split_name(full):
    parts = (full or "").split()
    if len(parts) < 2:
        return full or "", ""
    return parts[0], " ".join(parts[1:])


def _call(req):
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def submit(batch, key):
    data = []
    for row in batch:
        first, last = split_name(row.get("nom"))
        item = {"first_name": first, "last_name": last, "company": row.get("entreprise", "")}
        if row.get("site_web"):
            item["website"] = row["site_web"]
        if row.get("intitule"):
            item["job"] = row["intitule"]
        data.append(item)
    req = urllib.request.Request(
        API, data=json.dumps({"data": data, "siren": False, "language": "fr"}).encode(),
        headers={"X-Access-Token": key, "Content-Type": "application/json"})
    resp = _call(req)
    if resp.get("error") or not resp.get("request_id"):
        sys.exit(f"Dropcontact a refusé la requête : {resp}")
    print(f"lot envoyé ({len(data)} contacts), crédits restants : {resp.get('credits_left')}")
    return resp["request_id"]


def fetch(request_id, key, wait=30, max_wait=900):
    waited = 0
    while waited <= max_wait:
        req = urllib.request.Request(f"{API}/{request_id}", headers={"X-Access-Token": key})
        resp = _call(req)
        if resp.get("success") and "data" in resp:
            return resp["data"]
        if resp.get("error"):
            sys.exit(f"Erreur Dropcontact : {resp}")
        time.sleep(wait)
        waited += wait
    sys.exit(f"Dropcontact n'a pas répondu après {max_wait}s (request_id {request_id}), relancer plus tard.")


def merge(row, result):
    emails = result.get("email") or []
    best = emails[0] if emails else {}
    phones = result.get("phone") or result.get("mobile_phone") or ""
    if isinstance(phones, list):
        phones = phones[0].get("number", "") if phones and isinstance(phones[0], dict) else ", ".join(phones)
    row.update(prenom=result.get("first_name", ""), nom_famille=result.get("last_name", ""),
               email=best.get("email", ""), qualification_email=best.get("qualification", ""),
               telephone=phones, site_web_dropcontact=result.get("website", ""))
    if row["email"]:
        row["statut"] = "enrichi"
    return row


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--statut", default="valide", help="statut des lignes à enrichir (défaut : valide)")
    args = p.parse_args(argv)

    key = os.environ.get("DROPCONTACT_API_KEY")
    if not key:
        sys.exit("DROPCONTACT_API_KEY manquant (voir .env.example)")

    with open(args.input, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames) + [c for c in OUT_EXTRA if c not in reader.fieldnames]
        rows = list(reader)

    todo = [r for r in rows if r.get("statut") == args.statut]
    if not todo:
        print(f"Aucun contact au statut « {args.statut} ». Valide d'abord les contacts dans le CSV ou le Sheet.")
        return
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        results = fetch(submit(batch, key), key)
        for row, res in zip(batch, results):  # Dropcontact renvoie les lignes dans l'ordre
            merge(row, res)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"DROPCONTACT\nenvoyes: {len(todo)}\nemails_trouves: {sum(bool(r.get('email')) for r in todo)}\nfichier: {args.out}")


if __name__ == "__main__":
    main()
