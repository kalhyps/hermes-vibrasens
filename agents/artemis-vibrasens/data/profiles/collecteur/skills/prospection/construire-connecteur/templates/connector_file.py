"""Connecteur <PAYS> : <SOURCE> (fichier en masse), <ce qu'il collecte>.                     # À ADAPTER

Source : <adresse de la page du fichier> ; licence : <licence et conditions de réutilisation>. # À ADAPTER
Mise à jour du fichier : <fréquence> ; clé : <aucune, ou VARIABLE>.                         # À ADAPTER
Écrit le <AAAA-MM-JJ> pour la campagne <campagne> ; statut : à relire.                      # À ADAPTER

Le fichier est téléchargé une fois dans telechargements/, puis filtré avec DuckDB (rapide même sur plusieurs Go :
CSV, CSV compressé .gz, Parquet). Un fichier .zip est d'abord décompressé.

Exemple :
  /opt/leadgen/venv/bin/python /workspace/connecteurs/<pays>_<source>.py --activites <codes> --zones "<zones>"
"""
import argparse
import os
import sys
import zipfile

import duckdb

sys.path.insert(0, "/opt/data/leadgen/outils")
from connector_base import Connector, Http, common_arguments, main_guard, make_id, parse_list  # noqa: E402

# À ADAPTER, d'après la page de la source.
URL = "https://data.example.org/companies.csv.gz"
DOWNLOAD = "telechargements/<pays>-<source>.csv.gz"
COUNTRY = "XX"
# Colonnes du fichier utilisées pour filtrer : activité et zone, rien d'autre (aucun seuil de taille).
ACTIVITY_COLUMN, ZONE_COLUMN, STATUS_COLUMN, ACTIVE_VALUE = "activity_code", "region", "status", "active"


def data_file(path):
    """Chemin lisible par DuckDB : décompresse un .zip (le plus gros fichier de données qu'il contient)."""
    if not path.endswith(".zip"):
        return path
    with zipfile.ZipFile(path) as archive:
        name = max((i for i in archive.infolist() if i.filename.lower().endswith((".csv", ".parquet", ".txt"))),
                   key=lambda i: i.file_size).filename
        target = os.path.join(os.path.dirname(path), os.path.basename(name))
        if not os.path.exists(target):
            archive.extract(name, os.path.dirname(path))
            os.replace(os.path.join(os.path.dirname(path), name), target)
    return target


def select_rows(path, activities, zones):
    """Lignes de l'activité et de la zone demandées, sous forme de dictionnaires (toutes les valeurs en texte)."""
    con = duckdb.connect()
    reader = (f"read_parquet('{path}')" if path.endswith(".parquet")
              else f"read_csv('{path}', all_varchar=true, header=true)")
    conditions, params = [], []
    if activities:  # préfixe : un code court prend tous ses sous-codes
        conditions.append("(" + " OR ".join(f"{ACTIVITY_COLUMN} LIKE ?" for _ in activities) + ")")
        params += [f"{a}%" for a in activities]
    if zones:
        conditions.append(f"{ZONE_COLUMN} IN ({', '.join('?' for _ in zones)})")
        params += zones
    if STATUS_COLUMN:
        conditions.append(f"{STATUS_COLUMN} = ?")
        params.append(ACTIVE_VALUE)
    cursor = con.execute(f"SELECT * FROM {reader}" + (" WHERE " + " AND ".join(conditions) if conditions else ""), params)
    columns = [d[0] for d in cursor.description]
    while batch := cursor.fetchmany(10000):
        for values in batch:
            yield dict(zip(columns, values))


def to_row(record):
    """Une ligne du fichier -> une ligne au format commun. Ce que le fichier ne donne pas reste vide."""
    # À ADAPTER : chaque champ d'après les colonnes réelles (regarde l'en-tête avec head).
    number = str(record.get("registration_number") or "")
    return {
        "id": number or make_id(record.get("name"), record.get("city"), COUNTRY, prefix=COUNTRY.lower()),
        "id_registre": number, "registre": "<nom court du registre>" if number else "",
        "nom": record.get("name"), "pays": COUNTRY, "region": record.get(ZONE_COLUMN),
        "ville": record.get("city"), "code_postal": record.get("postal_code"), "adresse": record.get("street"),
        "code_activite": record.get(ACTIVITY_COLUMN), "nomenclature": "<nomenclature>",
        "effectif": record.get("employees"), "effectif_min": record.get("employees_min"),
        "date_creation": record.get("incorporation_date"),
        "source": f"https://register.example.org/company/{number}" if number else URL,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--activites", help="codes d'activité (préfixes), séparés par des virgules")
    parser.add_argument("--zones", help="zones, telles qu'écrites dans le fichier, séparées par des virgules")
    common_arguments(parser, "<pays>-<source>")   # À ADAPTER : nom par défaut de la source
    args = parser.parse_args()

    connector = Connector(args, "<description de la source>", Http(delay=1.0))  # À ADAPTER
    key = f"fichier|{args.activites}|{args.zones}"
    if connector.is_done(key):
        return connector.finish(complete=True)
    path = data_file(connector.http.download(URL, DOWNLOAD))
    for record in select_rows(path, parse_list(args.activites), parse_list(args.zones)):
        connector.write(to_row(record))
        if connector.limit_reached():
            return connector.finish(complete=False)
    connector.mark_done(key)
    return connector.finish(complete=True)


if __name__ == "__main__":
    main_guard(main)
