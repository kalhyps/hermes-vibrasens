"""Connecteur Royaume-Uni : registre Companies House (officiel, gratuit, clé COMPANIES_HOUSE_API_KEY).

La clé se crée gratuitement sur developer.company-information.service.gov.uk ; elle se place dans .env.
Quota de l'API : 600 requêtes par 5 minutes, respecté par l'outil.

Interrogation (combinables) :
  --sic 25620,33120      codes SIC 2007 à 5 chiffres ; --nace 25.62 les déduit des codes NACE (25.62 -> 25620) ;
  --mots "precision"     mots du nom de l'entreprise ;
  --lieu "West Midlands,Coventry"  lieux du siège (ville, comté, région) ; sans lieu, tout le Royaume-Uni.
Le registre ne publie pas les effectifs : l'outil lit le type des derniers comptes déposés, qui donne un ordre de
grandeur (micro-entity : moins de 10 salariés environ ; small : moins de 50 ; medium, full, group : davantage).
Aucune entreprise n'est écartée : les comptes « dormant » sont signalés dans `indices`.

Exemples :
  python gb_companies_house.py --nace 25.62,33.12 --lieu "West Midlands"
  python gb_companies_house.py --mots "precision engineering" --lieu Birmingham --nom registre-gb-mots
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import normalize  # noqa: E402
from connector_base import Connector, Http, common_arguments, main_guard, parse_list  # noqa: E402

API = "https://api.company-information.service.gov.uk"
PER_PAGE = 100  # une page = 100 profils à lire, environ 1 minute au rythme imposé par l'API
# Type des derniers comptes -> (effectif minimal estimé, libellé).
ACCOUNT_SIZES = {
    "micro-entity": (0, "micro-entreprise (moins de 10 salariés environ, d'après ses comptes)"),
    "small": (10, "petite entreprise (moins de 50 salariés, d'après ses comptes)"),
    "total-exemption-small": (10, "petite entreprise (moins de 50 salariés, d'après ses comptes)"),
    "unaudited-abridged": (10, "petite entreprise (moins de 50 salariés, d'après ses comptes)"),
    "total-exemption-full": (10, "petite entreprise (comptes simplifiés)"),
    "medium": (50, "entreprise moyenne (50 salariés ou plus, d'après ses comptes)"),
    "full": (50, "comptes complets (en général 50 salariés ou plus)"),
    "group": (50, "groupe (en général 50 salariés ou plus)"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sic", help="codes SIC 2007 à 5 chiffres, séparés par des virgules")
    parser.add_argument("--nace", help="codes NACE (25.62), convertis en SIC (25620)")
    parser.add_argument("--mots", help="mots du nom de l'entreprise, séparés par des virgules")
    parser.add_argument("--lieu", help="villes, comtés ou régions du siège, séparés par des virgules")
    parser.add_argument("--sans-comptes", action="store_true",
                        help="ne pas lire le type des derniers comptes (plus rapide, mais aucune estimation de taille)")
    common_arguments(parser, "registre-gb")
    args = parser.parse_args()
    if not (args.sic or args.nace or args.mots):
        parser.error("indiquez --sic, --nace ou --mots")
    key = os.environ.get("COMPANIES_HOUSE_API_KEY")
    if not key:
        sys.exit("CLÉ ABSENTE : COMPANIES_HOUSE_API_KEY n'est pas configurée. Elle est gratuite (développeur Companies "
                 "House) et doit être ajoutée à .env par l'utilisateur. En attendant, utilisez d'autres sources.")

    connector = Connector(args, "Registre britannique Companies House (API)", Http(delay=0.5, auth=(key, "")))
    sic_codes = parse_list(args.sic) + [code.replace(".", "") + "0" for code in parse_list(args.nace)]
    places = parse_list(args.lieu) or [""]
    queries = []
    for place in places:
        base = {"company_status": "active", "location": place}
        if sic_codes:
            queries.append((f"sic:{','.join(sic_codes)}|lieu:{normalize(place)}", {**base, "sic_codes": ",".join(sic_codes)}))
        queries += [(f"mot:{normalize(word)}|lieu:{normalize(place)}", {**base, "company_name_includes": word})
                    for word in parse_list(args.mots)]

    def read_page(http, params, cursor):
        start = cursor or 0
        data = http.get_json(f"{API}/advanced-search/companies",
                             {k: v for k, v in params.items() if v} | {"size": PER_PAGE, "start_index": start}) or {}
        items, hits = data.get("items") or [], data.get("hits") or 0
        if not items and start < hits:
            connector.ceiling(f"{params} : {hits} résultats, l'API s'arrête à {start}")
        following = start + len(items)
        return items, (following if items and following < hits else None)

    def to_rows(item, params):
        number = item.get("company_number", "")
        address = item.get("registered_office_address") or {}
        minimum, label, hints = "", "", []
        if not args.sans_comptes and number:
            profile = connector.http.get_json(f"{API}/company/{number}") or {}
            accounts_type = ((profile.get("accounts") or {}).get("last_accounts") or {}).get("type") or ""
            if accounts_type:
                hints.append(f"derniers comptes : {accounts_type}")
            if accounts_type == "dormant":
                hints.append("société dormante d'après ses comptes")
            minimum, label = ACCOUNT_SIZES.get(accounts_type, ("", ""))
        return {
            "id": number, "id_registre": number, "registre": "companies_house", "nom": item.get("company_name", ""),
            "pays": "GB", "region": address.get("region", ""), "ville": address.get("locality", ""),
            "code_postal": address.get("postal_code", ""),
            "adresse": ", ".join(v for v in (address.get("address_line_1"), address.get("address_line_2")) if v),
            "code_activite": ";".join(item.get("sic_codes") or []), "nomenclature": "SIC 2007 (Royaume-Uni)",
            "forme_juridique": item.get("company_type", ""), "effectif": label, "effectif_min": minimum,
            "siege": "oui", "categorie": item.get("company_type", ""), "date_creation": item.get("date_of_creation", ""),
            "indices": " ; ".join(hints),
            "source": f"https://find-and-update.company-information.service.gov.uk/company/{number}",
        }

    return connector.run(queries, read_page, to_rows)


if __name__ == "__main__":
    main_guard(main)
