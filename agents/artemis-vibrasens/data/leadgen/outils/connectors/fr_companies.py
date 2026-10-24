"""Connecteur France : API Recherche d'entreprises (registre officiel, gratuite, sans clé, 7 requêtes/s).

Trois façons d'interroger le registre, combinables :
  --naf 25.62A,25.62B           les entreprises actives dont l'activité principale a ces codes NAF ;
  --mots "décolletage,usinage"  les entreprises actives dont le nom ou l'enseigne contient ces mots
                                (rattrape celles déclarées sous un autre code) ;
  --noms sources/liste.csv      rapprochement : retrouve le SIREN des entreprises d'une liste (nom, ville, code postal,
                                ou directement numero_officiel = SIREN), avec « meme_que » pour que la fusion les relie.
                                La précision du rapprochement est dans « indices » ; les douteux dans les statistiques.
Zone : --departement 74,01, --region 84 (codes INSEE) ou --epci 200093201 (intercommunalité : métropole,
communauté d'agglomération ou de communes, code SIREN de l'EPCI) ; sans zone, toute la France. Chaque requête
est faite département par département (ou EPCI par EPCI) : le plafond de l'API (10 000 résultats par requête)
n'est atteint que dans une zone très dense, auquel cas l'outil l'annonce (PLAFOND) et il faut des codes plus précis.

Aucun filtre de taille : toutes les entreprises sont gardées, effectif connu ou non. L'établissement retenu est
celui de la zone (de préférence avec le code d'activité demandé, le siège, puis le plus grand) : il prouve un site local.

Exemples :
  python fr_companies.py --naf 25.62A,25.62B --departement 74
  python fr_companies.py --mots "decolletage,decolleteur" --departement 74,01
  python fr_companies.py --naf 70.22Z --epci 200093201          (Métropole européenne de Lille)
  python fr_companies.py --noms sources/salon-simodec.csv --nom registre-fr-rapprochement
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import name_tokens, normalize  # noqa: E402
from connector_base import Connector, Http, common_arguments, main_guard, parse_list  # noqa: E402

API = "https://recherche-entreprises.api.gouv.fr/search"
API_CEILING = 10000  # l'API ne renvoie pas plus de 10 000 résultats (400 pages de 25) pour une requête
PER_PAGE = 25

# Tranches d'effectif INSEE : code -> (effectif minimal, libellé). « NN » ou absent : effectif non renseigné.
TRANCHES = {
    "00": (0, "0 salarié"), "01": (1, "1 ou 2"), "02": (3, "3 à 5"), "03": (6, "6 à 9"),
    "11": (10, "10 à 19"), "12": (20, "20 à 49"), "21": (50, "50 à 99"), "22": (100, "100 à 199"),
    "31": (200, "200 à 249"), "32": (250, "250 à 499"), "41": (500, "500 à 999"),
    "42": (1000, "1 000 à 1 999"), "51": (2000, "2 000 à 4 999"), "52": (5000, "5 000 à 9 999"),
    "53": (10000, "10 000 et plus"),
}
# Départements de chaque région (codes INSEE des régions).
REGIONS = {
    "11": "75,77,78,91,92,93,94,95", "24": "18,28,36,37,41,45", "27": "21,25,39,58,70,71,89,90",
    "28": "14,27,50,61,76", "32": "02,59,60,62,80", "44": "08,10,51,52,54,55,57,67,68,88",
    "52": "44,49,53,72,85", "53": "22,29,35,56", "75": "16,17,19,23,24,33,40,47,64,79,86,87",
    "76": "09,11,12,30,31,32,34,46,48,65,66,81,82", "84": "01,03,07,15,26,38,42,43,63,69,73,74",
    "93": "04,05,06,13,83,84", "94": "2A,2B", "01": "971", "02": "972", "03": "973", "04": "974", "06": "976",
}
# Formes juridiques les plus courantes (nature juridique INSEE) ; les autres gardent leur code.
LEGAL_FORMS = {
    "1000": "entrepreneur individuel", "5410": "SARL", "5422": "SARL", "5426": "SARL", "5430": "SARL",
    "5485": "SARL", "5498": "EURL", "5499": "SARL", "5505": "SA", "5510": "SA", "5515": "SA", "5599": "SA",
    "5699": "SA", "5710": "SAS", "5720": "SASU", "5785": "SAS", "6540": "SCI", "6599": "société civile",
}


def departments(args):
    if args.departement:
        return parse_list(args.departement)
    if args.region:
        return [d for region in parse_list(args.region) for d in REGIONS[region.zfill(2)].split(",")]
    if args.epci:
        return [f"epci:{code}" for code in parse_list(args.epci)]
    return [d for codes in REGIONS.values() for d in codes.split(",")]


def department_of(postcode):
    postcode = (postcode or "").strip()
    if len(postcode) != 5 or not postcode.isdigit():
        return ""
    if postcode.startswith("97"):
        return postcode[:3]
    if postcode.startswith("20"):
        return ""  # Corse : 2A ou 2B, indéterminable par le code postal seul
    return postcode[:2]


def in_department(establishment, department):
    if not department:
        return True
    if department.startswith("epci:"):
        return str(establishment.get("epci") or "") == department[5:]
    code = str(establishment.get("departement") or "")
    if code:
        return code.upper() == department.upper()
    return (establishment.get("code_postal") or "").startswith("20" if department.upper() in ("2A", "2B") else department)


def pick_establishment(company, naf, department, postcode=""):
    """Établissement actif de la zone : même code postal (rapprochement), code d'activité demandé, siège, le plus grand."""
    candidates = [e for e in company.get("matching_etablissements") or []
                  if e.get("etat_administratif", "A") == "A" and in_department(e, department)]
    siege = company.get("siege") or {}
    if siege and siege.get("etat_administratif", "A") == "A" and in_department(siege, department):
        candidates.append(siege)
    if not candidates:
        return None
    return max(candidates, key=lambda e: (bool(postcode) and e.get("code_postal") == postcode,
                                          bool(naf) and e.get("activite_principale") == naf, bool(e.get("est_siege")),
                                          TRANCHES.get(e.get("tranche_effectif_salarie"), (-1,))[0]))


def latest_revenue(company):
    years = [(year, values.get("ca")) for year, values in (company.get("finances") or {}).items()
             if isinstance(values, dict) and values.get("ca") is not None]
    return max(years) if years else ("", "")


def to_row(company, establishment, same_as=""):
    siren = company.get("siren", "")
    minimum, label = TRANCHES.get(company.get("tranche_effectif_salarie"), ("", ""))
    year, revenue = latest_revenue(company)
    complements = company.get("complements") or {}
    nature = str(company.get("nature_juridique") or "")
    hints = []
    if complements.get("egapro_renseignee"):
        hints.append("index Egapro publié (obligatoire à partir de 50 salariés)")
    if company.get("annee_tranche_effectif_salarie"):
        hints.append(f"effectif de {company['annee_tranche_effectif_salarie']}")
    if (company.get("nombre_etablissements_ouverts") or 0) > 1:
        hints.append(f"{company['nombre_etablissements_ouverts']} établissements ouverts")
    employer = {"O": "oui", "N": "non"}.get(company.get("caractere_employeur") or "", "")
    return {
        "id": siren, "id_registre": siren, "registre": "siren",
        "nom": company.get("nom_complet") or company.get("nom_raison_sociale") or "", "pays": "FR",
        "region": establishment.get("departement") or "", "ville": establishment.get("libelle_commune") or "",
        "code_postal": establishment.get("code_postal") or "", "adresse": establishment.get("adresse") or "",
        "latitude": establishment.get("latitude") or "", "longitude": establishment.get("longitude") or "",
        "code_activite": establishment.get("activite_principale") or company.get("activite_principale") or "",
        "nomenclature": "NAF rév. 2", "libelle_activite": "",
        "forme_juridique": LEGAL_FORMS.get(nature, nature),
        "effectif": label, "effectif_min": minimum, "employeur": employer,
        "effectif_etablissement": TRANCHES.get(establishment.get("tranche_effectif_salarie"), ("", ""))[1],
        "siege": "oui" if establishment.get("est_siege") else "non", "id_etablissement": establishment.get("siret", ""),
        "nb_etablissements": company.get("nombre_etablissements_ouverts") or "",
        "categorie": company.get("categorie_entreprise") or "", "date_creation": company.get("date_creation") or "",
        "ca": revenue, "annee_ca": year, "indices": " ; ".join(hints),
        "source": f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}",
        "source_etablissement": f"https://annuaire-entreprises.data.gouv.fr/etablissement/{establishment.get('siret', '')}",
        "meme_que": same_as,
    }


def zone_params(zone):
    """Paramètre de zone de l'API pour un élément de departments() : « epci:<code> » ou un département."""
    return {"epci": zone[5:]} if zone.startswith("epci:") else {"departement": zone}


def company_tokens(company):
    names = [company.get("nom_complet"), company.get("nom_raison_sociale"), company.get("sigle")]
    for establishment in (company.get("matching_etablissements") or []) + [company.get("siege") or {}]:
        names += establishment.get("liste_enseignes") or []
        names.append(establishment.get("nom_commercial"))
    return {token for name in names if name for token in name_tokens(name)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--naf", help="codes NAF séparés par des virgules (ex. 25.62A,25.62B)")
    parser.add_argument("--mots", help="mots du nom ou de l'enseigne, séparés par des virgules (ex. decolletage)")
    parser.add_argument("--noms", help="CSV à rapprocher du registre (colonnes id, nom, et si possible ville, code_postal)")
    zone = parser.add_mutually_exclusive_group()
    zone.add_argument("--departement", help="codes de département séparés par des virgules (ex. 74,01)")
    zone.add_argument("--region", help="codes région INSEE séparés par des virgules (ex. 84)")
    zone.add_argument("--epci", help="codes EPCI (SIREN de l'intercommunalité) séparés par des virgules (ex. 200093201)")
    common_arguments(parser, "registre-fr")
    args = parser.parse_args()
    if not (args.naf or args.mots or args.noms):
        parser.error("indiquez --naf, --mots ou --noms")

    connector = Connector(args, "Registre français des entreprises (API Recherche d'entreprises)", Http(delay=0.15))
    queries = []
    for department in departments(args) if (args.naf or args.mots) else []:
        zone = zone_params(department)
        queries += [(f"naf:{naf}|dep:{department}", {"activite_principale": naf, **zone, "_zone": department})
                    for naf in parse_list(args.naf)]
        queries += [(f"mot:{normalize(word)}|dep:{department}", {"q": word, **zone, "_zone": department})
                    for word in parse_list(args.mots)]
    if args.noms:
        with open(args.noms, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("nom") and (row.get("pays") or "FR").upper() == "FR":
                    siren = "".join(c for c in (row.get("numero_officiel") or "") if c.isdigit())[:9]
                    if len(siren) == 9:  # SIREN donné : on le cherche lui, sans zone
                        params = {"q": siren, "_liste": row, "_siren": siren}
                    else:
                        params = {"q": row["nom"], "departement": department_of(row.get("code_postal")),
                                  "_liste": row}
                    queries.append((f"nom:{row.get('id') or normalize(row['nom'])}", params))
    connector.stats.setdefault("sans_etablissement_actif_dans_la_zone", 0)
    connector.stats.setdefault("rapprochements", {"trouves": 0, "introuvables": [], "douteux": []})
    connector.stats["rapprochements"].setdefault("douteux", [])

    def read_page(http, params, cursor):
        page = cursor or 1
        query = {k: v for k, v in params.items() if not k.startswith("_") and v}
        if "_liste" in params:  # rapprochement : une seule page des meilleurs résultats suffit
            data = http.get_json(API, {**query, "etat_administratif": "A", "per_page": 10, "page": 1}) or {}
            return [data], None
        data = http.get_json(API, {**query, "etat_administratif": "A", "per_page": PER_PAGE, "page": page}) or {}
        total = data.get("total_results") or 0
        if page == 1 and total >= API_CEILING:
            connector.ceiling(f"{query} : {total} résultats, l'API n'en renvoie que {API_CEILING}")
        last_page = min(data.get("total_pages") or 1, API_CEILING // PER_PAGE)
        return data.get("results") or [], (page + 1 if page < last_page else None)

    def to_rows(item, params):
        if "_liste" in params:
            return match_from_list(item, params)
        establishment = pick_establishment(item, params.get("activite_principale"), params.get("_zone"))
        if establishment is None:
            connector.stats["sans_etablissement_actif_dans_la_zone"] += 1
            return None
        return to_row(item, establishment)

    def match_from_list(data, params):
        listed = params["_liste"]
        wanted = set(name_tokens(listed["nom"]))
        postcode, town = (listed.get("code_postal") or "").strip(), normalize(listed.get("ville"))
        best, homonyms = None, 0
        for company in data.get("results") or []:
            if params.get("_siren"):
                if company.get("siren") != params["_siren"]:
                    continue
            elif not wanted or not wanted <= company_tokens(company):
                continue  # tous les mots distinctifs du nom de la liste doivent être dans le nom au registre
            establishment = pick_establishment(company, None, params.get("departement"), postcode)
            if establishment is None:
                continue
            homonyms += 1
            same_place = (postcode and establishment.get("code_postal") == postcode) or \
                         (town and normalize(establishment.get("libelle_commune")) == town)
            rank = (bool(same_place), bool(establishment.get("est_siege")))
            if best is None or rank > best[0]:
                best = (rank, company, establishment)
        if best is None:
            connector.stats["rapprochements"]["introuvables"].append(listed["nom"])
            return None
        connector.stats["rapprochements"]["trouves"] += 1
        row = to_row(best[1], best[2], same_as=listed.get("id", ""))
        if params.get("_siren"):
            how = "rapprochement sûr : SIREN donné"
        elif best[0][0]:
            how = "rapprochement par le nom et le lieu"
        else:
            # Nom seul : rien ne dit que c'est la bonne entreprise (le 20/10, Babolat au Beausset au lieu de Lyon).
            how = f"rapprochement DOUTEUX : nom seul, {homonyms} entreprise(s) de ce nom au registre"
            connector.stats["rapprochements"]["douteux"].append(
                f"{listed['nom']} → {row['nom']} ({row['ville']}), {homonyms} homonyme(s)")
        row["indices"] = " ; ".join(part for part in (how, row["indices"]) if part)
        return row

    return connector.run(queries, read_page, to_rows)


if __name__ == "__main__":
    main_guard(main)
