"""Connecteur <PAYS> : <SOURCE>, <ce qu'il collecte>.                                       # À ADAPTER

Source : <adresse de la documentation> ; licence : <licence et conditions de réutilisation>.  # À ADAPTER
Quota : <requêtes par seconde ou par jour> ; clé : <VARIABLE d'environnement, ou aucune>.   # À ADAPTER
Écrit le <AAAA-MM-JJ> pour la campagne <campagne> ; statut : à relire.                      # À ADAPTER

Exemple :
  /opt/leadgen/venv/bin/python /workspace/connecteurs/<pays>_<source>.py --activites <codes> --zones "<zones>"
"""
import argparse
import os
import sys

sys.path.insert(0, "/opt/data/leadgen/outils")
from connector_base import Connector, Http, common_arguments, main_guard, make_id, parse_list  # noqa: E402

# À ADAPTER, d'après la documentation de la source.
API = "https://api.example.org/v1/companies"
PAGE_SIZE = 100       # taille de page maximale permise par la source
DELAY = 1.0           # secondes entre deux requêtes, pour rester sous le quota
CEILING = None        # nombre maximal de résultats qu'une requête peut renvoyer, si la source en a un
KEY_VARIABLE = None   # nom de la variable d'environnement de la clé, ou None si la source n'en demande pas
COUNTRY = "XX"        # code ISO du pays


def queries(args):
    """Toutes les requêtes à faire : une par couple (activité, zone). La clé, unique, sert à la reprise."""
    for activity in parse_list(args.activites) or [""]:
        for zone in parse_list(args.zones) or [""]:
            # À ADAPTER : noms des paramètres de filtre de la source ; ajouter le filtre « actives seulement » s'il existe.
            yield f"activite:{activity}|zone:{zone}", {"activity_code": activity, "region": zone, "status": "active"}


def make_reader(connector):
    def read_page(http, params, cursor):
        """Une page de résultats. Renvoie (éléments, curseur de la page suivante, ou None à la fin)."""
        page = cursor or 1
        # À ADAPTER : paramètres de pagination (page/size, offset/limit, ou jeton de page suivante).
        data = http.get_json(API, {**{k: v for k, v in params.items() if v}, "page": page, "size": PAGE_SIZE}) or {}
        items = data.get("results") or []          # À ADAPTER : où sont les éléments dans la réponse
        total = data.get("total") or 0             # À ADAPTER : nombre total de résultats, s'il est donné
        if page == 1 and CEILING and total > CEILING:
            connector.ceiling(f"{params} : {total} résultats, la source n'en renvoie que {CEILING}")
        has_next = items and page * PAGE_SIZE < min(total or float("inf"), CEILING or float("inf"))
        return items, (page + 1 if has_next else None)
    return read_page


def to_row(item, params):
    """Un élément de la source -> une ligne au format commun. Ce que la source ne donne pas reste vide."""
    # À ADAPTER : chaque champ d'après la réponse réelle observée avec curl.
    number = str(item.get("registration_number") or "")
    return {
        "id": number or make_id(item.get("name"), item.get("city"), COUNTRY, prefix=COUNTRY.lower()),
        "id_registre": number, "registre": "<nom court du registre>" if number else "",
        "nom": item.get("name"), "pays": COUNTRY,
        "region": item.get("state"), "ville": item.get("city"), "code_postal": item.get("postal_code"),
        "adresse": item.get("street"),
        "code_activite": item.get("activity_code"), "nomenclature": "<nomenclature>",
        "libelle_activite": item.get("activity_label"),
        "forme_juridique": item.get("legal_form"), "date_creation": item.get("incorporation_date"),
        "effectif": item.get("employees"), "effectif_min": item.get("employees_min"),
        "site_web": item.get("website"), "telephone": item.get("phone"),
        "source": f"https://register.example.org/company/{number}",   # adresse de la fiche, ou référence précise
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--activites", help="codes d'activité de la source, séparés par des virgules")
    parser.add_argument("--zones", help="zones de la source (codes ou noms), séparées par des virgules")
    common_arguments(parser, "<pays>-<source>")   # À ADAPTER : nom par défaut de la source
    args = parser.parse_args()

    headers = {}
    if KEY_VARIABLE:
        key = os.environ.get(KEY_VARIABLE)
        if not key:
            sys.exit(f"CLÉ ABSENTE : {KEY_VARIABLE} n'est pas configurée ; l'utilisateur doit l'ajouter (fichier "
                     "de clés de l'agent, puis terminal.env_passthrough des profils). Utilise d'autres sources en attendant.")
        headers = {"Authorization": f"Bearer {key}"}   # À ADAPTER : forme exacte demandée par la source
    connector = Connector(args, "<description de la source>", Http(delay=DELAY, headers=headers))  # À ADAPTER
    return connector.run(list(queries(args)), make_reader(connector), to_row)


if __name__ == "__main__":
    main_guard(main)
