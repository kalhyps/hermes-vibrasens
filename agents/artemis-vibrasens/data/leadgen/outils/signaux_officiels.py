"""Signaux datés tirés de sources officielles, gratuites et sans clé, pour les entreprises françaises d'une campagne.

Deux sources, interrogées par SIREN :
  - BODACC (annonces légales, API opendatasoft de la DILA) : création ou immatriculation d'un établissement, changement
    dans la direction, transfert d'adresse, changement d'activité, achat ou cession de fonds ; et, en alerte,
    procédures collectives, conciliation, radiation ;
  - annuaire RGE de l'ADEME (data.ademe.fr) : les qualifications RGE de l'entreprise, avec leur date de début ; une
    qualification obtenue dans la période est un signal, une qualification en cours de validité une certification.
Les dépôts de comptes du BODACC sont ignorés (annuels, ils ne disent rien de récent). Aucune donnée de personne n'est
écrite : ni nom de dirigeant, ni email, ni téléphone nominatif.

Sortie dans le dossier de la campagne : signaux-officiels.csv (une ligne par fait : id, siren, date, type, fait, lien,
source) et signaux-officiels-stats.json. `type` vaut `signal` (fait daté de la période), `alerte` (difficulté publiée,
quelle que soit sa date dans la période) ou `certification` (qualification RGE valide aujourd'hui, même ancienne).
Une entreprise déjà traitée n'est pas réinterrogée ; --recommencer efface et repart de zéro.

Exemples (dans le dossier de la campagne) :
  python signaux_officiels.py --vague 1                       entreprises gardées de la vague 1 (2-enrichissement.csv)
  python signaux_officiels.py --base 1-collecte.csv --vague 2 --depuis 2025-10-20
  python signaux_officiels.py --ids 332440809,303433932
"""
import argparse
import csv
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import read_rows  # noqa: E402
from connector_base import Http  # noqa: E402

BODACC = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records"
RGE = "https://data.ademe.fr/data-fair/api/v1/datasets/eo1n335dwa-ul7glxa7lm1ho/lines"
OUTPUT, STATS = "signaux-officiels.csv", "signaux-officiels-stats.json"
FIELDS = ["id", "siren", "date", "type", "fait", "lien", "source"]
ALERTS = {"collective": "procédure collective", "conciliation": "procédure de conciliation",
          "retablissement_professionnel": "rétablissement professionnel", "radiation": "radiation du registre"}
# Ce que dit le descriptif d'une modification, en clair pour le livrable.
CHANGES = [("administration", "changement dans la direction"), ("adresse", "transfert d'adresse"),
           ("activit", "changement d'activité"), ("capital", "modification du capital"),
           ("dénomination", "changement de nom"), ("forme juridique", "changement de forme juridique")]


def parse_json(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}


def establishments(record):
    """Établissements cités par l'annonce (un objet ou une liste)."""
    found = parse_json(record.get("listeetablissements")).get("etablissement") or []
    return found if isinstance(found, list) else [found]


def place_of(establishment):
    address = establishment.get("adresse") or {}
    return " ".join(str(address.get(k) or "") for k in ("codePostal", "ville")).strip()


def bodacc_fact(record):
    """(type, fait) d'une annonce, ou None si elle ne dit rien d'utile."""
    family = record.get("familleavis") or ""
    if family in ALERTS:
        judgment = parse_json(record.get("jugement"))
        nature = judgment.get("nature") or record.get("familleavis_lib") or ALERTS[family]
        return "alerte", f"{ALERTS[family]} : {nature}"
    if family in ("creation", "immatriculation"):
        parts = []
        for item in establishments(record):
            what = "ouverture d'un établissement" if item.get("qualiteEtablissement", "").lower() != "etablissement principal" \
                else "immatriculation de l'établissement principal"
            origin = item.get("origineFonds") or ""
            detail = ", ".join(x for x in (item.get("activite") or "", place_of(item),
                                           origin.lower() if origin and origin != "Création" else "") if x)
            parts.append(f"{what} ({detail})" if detail else what)
        return "signal", " ; ".join(parts) or (record.get("familleavis_lib") or "création").lower()
    if family == "modification":
        text = (parse_json(record.get("modificationsgenerales")).get("descriptif") or "").lower()
        changes = [label for word, label in CHANGES if word in text]
        if not changes:
            return None
        return "signal", ", ".join(dict.fromkeys(changes))
    if family == "vente":
        items = [", ".join(x for x in (i.get("activite") or "", place_of(i)) if x) for i in establishments(record)]
        return "signal", "achat ou cession de fonds de commerce" + (f" ({' ; '.join(i for i in items if i)})"
                                                                     if any(items) else "") + " : voir l'annonce"
    return None


def bodacc_rows(http, company_id, siren, since):
    data = http.get_json(BODACC, {
        "where": f'registre like "{siren}" and dateparution >= date\'{since}\' and familleavis != "dpc"',
        "order_by": "dateparution desc", "limit": 50}) or {}
    rows = []
    for record in data.get("results") or []:
        found = bodacc_fact(record)
        if found:
            rows.append({"id": company_id, "siren": siren, "date": record.get("dateparution", ""), "type": found[0],
                         "fait": found[1], "lien": record.get("url_complete", ""), "source": "BODACC"})
    return rows


def rge_rows(http, company_id, siren, since):
    data = http.get_json(RGE, {"qs": f"siret:{siren}*", "size": 100,
                               "select": "siret,nom_qualification,domaine,organisme,nom_certificat,"
                                         "url_qualification,lien_date_debut,lien_date_fin"}) or {}
    today = date.today().isoformat()
    # Une ligne par domaine : on regroupe par certificat (même organisme, même date, même document).
    started, valid = {}, {}
    for line in data.get("results") or []:
        start, end = line.get("lien_date_debut") or "", line.get("lien_date_fin") or ""
        if end and end < today:
            continue  # qualification échue
        label = line.get("domaine") or line.get("nom_qualification") or "qualification RGE"
        certificate = line.get("nom_certificat") or line.get("organisme") or "RGE"
        link = line.get("url_qualification") or "https://france-renov.gouv.fr/annuaire-rge"
        if start >= since:
            started.setdefault((start, certificate, link), []).append(label)
        entry = valid.setdefault((certificate, link), {"labels": [], "start": start, "end": end})
        entry["labels"].append(label)
        entry["start"], entry["end"] = min(entry["start"] or start, start), max(entry["end"], end)
    rows = []
    for (start, certificate, link), labels in sorted(started.items()):
        rows.append({"id": company_id, "siren": siren, "date": start, "type": "signal",
                     "fait": f"qualification RGE obtenue ou renouvelée ({certificate}) : "
                             + ", ".join(dict.fromkeys(labels)), "lien": link, "source": "annuaire RGE (ADEME)"})
    for (certificate, link), entry in valid.items():
        rows.append({"id": company_id, "siren": siren, "date": entry["start"], "type": "certification",
                     "fait": f"RGE ({certificate}) : {', '.join(dict.fromkeys(entry['labels']))}, valide jusqu'au "
                             f"{entry['end'] or 'date non publiée'}", "lien": link, "source": "annuaire RGE (ADEME)"})
    return rows


def siren_of(row):
    if (row.get("registre") or "siren").lower() != "siren" or (row.get("pays") or "FR").upper() != "FR":
        return ""
    digits = "".join(c for c in (row.get("id_registre") or row.get("id") or "") if c.isdigit())
    return digits if len(digits) == 9 else ""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="2-enrichissement.csv", help="fichier des entreprises (défaut : 2-enrichissement.csv)")
    parser.add_argument("--vague", help="seulement cette vague (colonne vague)")
    parser.add_argument("--statut", default="gardée", help="seulement ce statut d'enrichissement (« tous » pour tout prendre)")
    parser.add_argument("--ids", help="identifiants ou SIREN séparés par des virgules (remplace --vague et --statut)")
    parser.add_argument("--depuis", default=(date.today() - timedelta(days=365)).isoformat(),
                        help="début de la période des signaux, AAAA-MM-JJ (défaut : il y a un an)")
    parser.add_argument("--recommencer", action="store_true", help="efface les résultats et repart de zéro")
    args = parser.parse_args()

    if not os.path.exists(args.base):
        raise SystemExit(f"{args.base} est absent : lancez l'outil dans le dossier de la campagne.")
    rows = read_rows(args.base)
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        rows = [r for r in rows if r.get("id") in wanted or siren_of(r) in wanted]
    else:
        if args.vague:
            rows = [r for r in rows if str(r.get("vague") or "") == str(args.vague)]
        if args.statut != "tous" and rows and "statut" in rows[0]:
            rows = [r for r in rows if r.get("statut") == args.statut]

    if args.recommencer:
        for path in (OUTPUT, STATS):
            if os.path.exists(path):
                os.remove(path)
    done = {r["id"] for r in read_rows(OUTPUT)} if os.path.exists(OUTPUT) else set()
    stats = json.load(open(STATS, encoding="utf-8")) if os.path.exists(STATS) else {}
    stats.setdefault("traitees", [])
    done |= set(stats["traitees"])
    todo = [(r["id"], siren_of(r)) for r in rows if r.get("id") not in done]
    without = [r["id"] for r in rows if not siren_of(r)]
    todo = [(i, s) for i, s in todo if s]

    http = Http(delay=0.3)
    new_file = not os.path.exists(OUTPUT)
    counts = {"signal": 0, "alerte": 0, "certification": 0}
    errors = []
    with open(OUTPUT, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        for company_id, siren in todo:
            try:
                found = bodacc_rows(http, company_id, siren, args.depuis) + rge_rows(http, company_id, siren, args.depuis)
            except Exception as exc:  # une source indisponible n'arrête pas les autres entreprises
                errors.append(f"{company_id} : {str(exc)[:160]}")
                continue
            writer.writerows(found)
            handle.flush()
            for row in found:
                counts[row["type"]] += 1
            stats["traitees"].append(company_id)
    stats.update({"depuis": args.depuis, "mis_a_jour": date.today().isoformat(),
                  "sans_siren": sorted(set(stats.get("sans_siren", [])) | set(without)), "erreurs": errors})
    with open(STATS, "w", encoding="utf-8") as handle:
        json.dump(stats, handle, ensure_ascii=False, indent=2)

    print(f"{len(todo) - len(errors)} entreprise(s) interrogée(s) depuis le {args.depuis} : {counts['signal']} signal(aux), "
          f"{counts['alerte']} alerte(s), {counts['certification']} certification(s) RGE en cours ; résultats dans {OUTPUT}.")
    if without:
        print(f"Sans SIREN (hors France ou numéro absent), non interrogées : {len(without)}.")
    if errors:
        print(f"INCOMPLET : {len(errors)} entreprise(s) en erreur (source indisponible) ; relancez la même commande.")
        sys.exit(3)


if __name__ == "__main__":
    main()
