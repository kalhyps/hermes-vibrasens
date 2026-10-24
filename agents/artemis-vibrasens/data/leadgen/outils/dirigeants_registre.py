#!/usr/bin/env python3
"""Dirigeants du registre français comme décideurs des petits leads qualifiés (choix de Nico, 20/10/2026).

Dans une entreprise de quelques dizaines de salariés, le décideur est le dirigeant, et le registre le publie
(API Recherche d'entreprises, licence ouverte). Pour chaque lead `qualifié` français dont l'effectif ne dépasse pas
--max-effectif, l'outil lit les dirigeants personnes physiques du registre et les ajoute à linkedin/contacts.csv,
au statut `a_valider`, dans le même format que les décideurs LinkedIn : le client les valide (ou non), puis
export_leads.py les porte dans leads.xlsx et decideurs.xlsx, et Dropcontact ne cherche l'email que des validés.

Règles : après la qualification seulement, pour les seuls leads qualifiés, jamais dans le vivier ; aucune donnée
de naissance ni de nationalité n'est copiée ; la source (fiche « dirigeants » de l'Annuaire des entreprises) est
écrite sur chaque ligne, et le premier message au dirigeant doit la lui dire (RGPD, droit d'opposition).

  python dirigeants_registre.py                       # leads.csv, Statut = qualifié, effectif ≤ 49
  python dirigeants_registre.py --max-effectif 19 --filtre "Statut=qualifié"
"""
import argparse
import csv
import os
import re
from datetime import date

from common import normalize, read_rows
from connector_base import Http, main_guard

API = "https://recherche-entreprises.api.gouv.fr/search"
FICHE = "https://annuaire-entreprises.data.gouv.fr/dirigeants/{siren}"
SOURCE = "registre (Annuaire des entreprises, dirigeants)"
# Mêmes colonnes que les décideurs LinkedIn (linkedin_search.py, PEOPLE_FIELDS) : export_leads.py lit les deux.
FIELDS = ["date_collecte", "source", "requete", "entreprise", "site_web",
          "nom", "intitule", "decideur", "linkedin_url", "extrait", "statut"]


def siren_of(row):
    digits = re.sub(r"\D", "", row.get("Numéro officiel") or "")
    return digits if len(digits) == 9 else ""


def max_headcount(text):
    """Le plus grand nombre d'une cellule « Effectif » (« 6 à 9 salariés (registre) ; 18 (site) » → 18) ; None sans chiffre."""
    numbers = [int(n) for n in re.findall(r"\d+", (text or "").replace(" ", "").replace(" ", ""))]
    return max(numbers) if numbers else None


def matches(row, condition):
    if not condition:
        return True
    key, _, value = condition.partition("=")
    return normalize(row.get(key.strip(), "")) == normalize(value)


def officers(http, siren):
    """Dirigeants personnes physiques de l'entreprise : [(nom complet, qualité)], sans date de naissance ni nationalité."""
    data = http.get_json(API, {"q": siren, "page": 1, "per_page": 1}) or {}
    results = [r for r in data.get("results") or [] if r.get("siren") == siren]
    found = []
    for person in (results[0].get("dirigeants") or []) if results else []:
        if (person.get("type_dirigeant") or "").lower() != "personne physique":
            continue  # une société dirigeante n'est pas un interlocuteur
        name = " ".join(part for part in ((person.get("prenoms") or "").split(",")[0].strip().title(),
                                          (person.get("nom") or "").strip().upper()) if part)
        if name and name not in [n for n, _ in found]:
            found.append((name, (person.get("qualite") or "dirigeant").strip()))
    return found


def existing(path):
    if not os.path.exists(path):
        return set()
    return {(normalize(r.get("entreprise", "")), normalize(r.get("nom", ""))) for r in read_rows(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default="leads.csv", help="livrable de la campagne (défaut : leads.csv)")
    parser.add_argument("--filtre", default="Statut=qualifié", help="colonne=valeur à garder (défaut : Statut=qualifié)")
    parser.add_argument("--max-effectif", type=int, default=49,
                        help="effectif au-delà duquel le dirigeant n'est plus le décideur (défaut : 49)")
    parser.add_argument("--out", default=os.path.join("linkedin", "contacts.csv"),
                        help="fichier des décideurs à valider (défaut : linkedin/contacts.csv)")
    args = parser.parse_args()

    leads = [r for r in read_rows(args.input) if matches(r, args.filtre)]
    seen = existing(args.out)
    http = Http(delay=0.2)  # l'API accepte 7 requêtes par seconde
    rows, skipped = [], {"pays": 0, "sans SIREN": 0, "effectif trop grand": 0, "effectif inconnu": 0, "sans dirigeant": 0}
    for lead in leads:
        if (lead.get("Pays") or "FR").upper() != "FR":
            skipped["pays"] += 1
            continue
        siren = siren_of(lead)
        if not siren:
            skipped["sans SIREN"] += 1
            continue
        headcount = max_headcount(lead.get("Effectif"))
        if headcount is None:
            skipped["effectif inconnu"] += 1
            continue
        if headcount > args.max_effectif:
            skipped["effectif trop grand"] += 1
            continue
        people = officers(http, siren)
        if not people:
            skipped["sans dirigeant"] += 1
            continue
        company = lead.get("Entreprise") or lead.get("Raison sociale") or ""
        for name, role in people:
            if (normalize(company), normalize(name)) in seen:
                continue
            rows.append({"date_collecte": date.today().isoformat(), "source": SOURCE, "requete": siren,
                         "entreprise": company, "site_web": lead.get("Site web", ""), "nom": name,
                         "intitule": role, "decideur": "oui", "linkedin_url": "",
                         "extrait": f"{role} au registre, fiche {FICHE.format(siren=siren)}", "statut": "a_valider"})
    if rows:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        new_file = not os.path.exists(args.out)
        with open(args.out, "a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
            if new_file:
                writer.writeheader()
            writer.writerows(rows)
    print(f"Leads examinés : {len(leads)} ; dirigeants ajoutés à valider : {len(rows)} ({args.out}) ; "
          + " ; ".join(f"{k} : {v}" for k, v in skipped.items() if v))
    print("Rappel : le client valide chaque dirigeant avant tout contact, et le premier message dit d'où vient son nom "
          "(registre public) et qu'il peut s'opposer à tout nouveau contact.")
    return 0


if __name__ == "__main__":
    main_guard(main)
