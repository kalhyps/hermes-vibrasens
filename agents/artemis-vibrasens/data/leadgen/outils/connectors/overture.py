"""Connecteur monde : Overture Maps, base ouverte et gratuite des lieux de tous les pays (usines, ateliers, bureaux,
commerces), alimentée notamment par Meta, Microsoft et Amazon. Interrogée avec DuckDB, sans clé.

Elle donne souvent le site web et le téléphone, et la position exacte, mais ni effectif ni identifiant officiel :
c'est un complément aux registres, et la source de base dans les pays sans registre ouvert.

Étapes :
  1. Zone : --zone « Liège » avec --pays BE (nom local d'une région, province, comté ou ville), ou --bbox.
  2. Catégories présentes dans la zone : --lister-categories, avec un filtre facultatif --filtre « machin,metal ».
  3. Collecte : --categories machine_shop,metal_fabricator et/ou --mots « precision,decolletage » (mots du nom).
     Tous les lieux correspondants sont écrits, sans maximum ; les doublons (plusieurs lieux d'une même
     entreprise) sont réunis à la fusion des sources.
Les lieux de la zone ne sont lus qu'une fois sur Overture (lecture distante de 1 à 3 minutes, lourde pour le réseau :
éviter pendant qu'une autre campagne lit des sites), puis gardés dans la campagne :
overture-<pays>-<zone>-<version>.parquet.

Exemples :
  python overture.py --pays BE --zone "Liège" --lister-categories --filtre "machin,metal,industr"
  python overture.py --pays BE --zone "Liège" --categories machine_shop,metal_fabricator
  python overture.py --pays US --zone "Ohio" --categories machine_shop --mots "screw machine,precision" --nom overture-ohio
"""
import argparse
import json
import os
import re
import sys

import duckdb
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import is_generic_email, is_social_site, normalize  # noqa: E402
from connector_base import Connector, common_arguments, main_guard, parse_list  # noqa: E402

BUCKET = "https://overturemaps-us-west-2.s3.amazonaws.com"
DEFAULT_RELEASE = "2026-09-23.1"
CLOSED = ("permanently_closed", "temporarily_closed", "closed")
# Ordre de préférence quand un nom de zone correspond à plusieurs niveaux (région avant commune, etc.).
ZONE_ORDER = {"country": 0, "region": 1, "macroregion": 1, "county": 2, "macrocounty": 2, "localadmin": 3, "locality": 4}


def latest_release():
    """Dernière publication d'Overture Maps, ou la version connue si le listing échoue."""
    try:
        listing = httpx.get(f"{BUCKET}/?list-type=2&prefix=release/&delimiter=/", timeout=20).text
        releases = re.findall(r"<Prefix>release/([^/<]+)/</Prefix>", listing)
        return max(releases) if releases else DEFAULT_RELEASE
    except httpx.HTTPError:
        return DEFAULT_RELEASE


def connect():
    con = duckdb.connect()
    con.execute("SET extension_directory='/opt/leadgen/duckdb-ext'")  # extensions préinstallées dans l'image
    con.execute("LOAD httpfs")
    con.execute("LOAD spatial")
    con.execute("SET s3_region='us-west-2'")
    con.execute("SET enable_progress_bar=false")
    return con


def remote(release, theme, kind):
    return (f"read_parquet('s3://overturemaps-us-west-2/release/{release}/theme={theme}/type={kind}/*', "
            "hive_partitioning=1)")


def find_zone(con, release, name, country):
    """(emprise xmin, ymin, xmax, ymax ; contour WKB) de la zone nommée dans le pays, et les correspondances."""
    rows = con.execute(
        f"SELECT names.primary, subtype, bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax, ST_AsWKB(geometry) "
        f"FROM {remote(release, 'divisions', 'division_area')} "
        f"WHERE country = ? AND lower(names.primary) LIKE lower(?) "
        # Le nom exact en tête : « Ain » ne doit pas rester derrière Ainhoa, Aincille… quand la liste est tronquée.
        f"ORDER BY (strip_accents(lower(names.primary)) = strip_accents(lower(?))) DESC LIMIT 20",
        [country, f"{name}%", name]).fetchall()
    exact = [r for r in rows if normalize(r[0]) == normalize(name)] or rows
    exact.sort(key=lambda r: ZONE_ORDER.get(r[1], 9))
    chosen = exact[0] if exact else None
    return (chosen[2:6] if chosen else None), (chosen[6] if chosen else None), chosen, rows


def load_zone(con, args, country, release):
    """Lit les lieux ouverts de la zone sur Overture, une seule fois, et les garde dans la campagne."""
    key = normalize(args.zone).replace(" ", "-") if args.zone else "bbox-" + args.bbox.replace(",", "_")
    cache = f"overture-{country}-{key}-{release}.parquet"
    if os.path.exists(cache) and os.path.exists(cache + ".json"):
        print(f"Lieux de la zone déjà lus sur Overture : {cache}")
        return cache
    outline = None
    if args.zone:
        box, outline, chosen, matches = find_zone(con, release, args.zone, country)
        if not box:
            sys.exit(f"ZONE INTROUVABLE : « {args.zone} » dans le pays {country}. Essayez le nom local, un autre niveau "
                     "(région, comté, ville) ou --bbox.")
        # La zone retenue est dite clairement : la liste des autres noms proches (Ainhoa pour « Ain »…) n'est qu'une aide.
        others = [m for m in matches if m is not chosen][:5]
        print(f"Zone retenue : {chosen[0]} ({chosen[1]})"
              + (" ; autres noms proches, non retenus : " + " ; ".join(f"{m[0]} ({m[1]})" for m in others) if others else ""))
    else:
        box = [float(v) for v in args.bbox.split(",")]
    xmin, ymin, xmax, ymax = box
    where = ("bbox.xmin >= ? AND bbox.xmax <= ? AND bbox.ymin >= ? AND bbox.ymax <= ? "
             "AND coalesce(addresses[1].country, '') = ? AND NOT list_contains(?, coalesce(operating_status, 'open'))")
    params = [xmin, xmax, ymin, ymax, country, list(CLOSED)]
    if outline is not None:  # l'emprise est un rectangle : on ne garde que les lieux à l'intérieur du vrai contour
        where += " AND ST_Within(geometry, ST_GeomFromWKB(?))"
        params.append(outline)
    con.execute(
        f"CREATE TABLE zone AS SELECT id, names.primary AS name, taxonomy.primary AS category, basic_category, "
        f"coalesce(taxonomy.alternates, []::VARCHAR[]) AS alternates, websites, phones, emails, "
        f"addresses[1].freeform AS street, addresses[1].locality AS city, addresses[1].postcode AS postcode, "
        f"addresses[1].region AS region, ST_Y(geometry) AS lat, ST_X(geometry) AS lon, confidence "
        f"FROM {remote(release, 'places', 'place')} WHERE {where}", params)
    con.execute(f"COPY zone TO '{cache}' (FORMAT PARQUET)")
    with open(cache + ".json", "w", encoding="utf-8") as handle:
        json.dump({"zone": args.zone or args.bbox, "pays": country, "release": release, "emprise": list(box)},
                  handle, ensure_ascii=False)
    return cache


def column(con, places, name):
    """Colonne du cache, ou NULL si le cache vient d'une ancienne version de l'outil."""
    names = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {places}").fetchall()}
    return name if name in names else "NULL"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pays", required=True, help="code ISO à deux lettres (BE, DE, US, JP…)")
    zone = parser.add_mutually_exclusive_group(required=True)
    zone.add_argument("--zone", help="nom local d'une région, province, comté ou ville du pays")
    zone.add_argument("--bbox", help="emprise xmin,ymin,xmax,ymax (longitudes, latitudes)")
    parser.add_argument("--lister-categories", action="store_true", help="afficher les catégories présentes et s'arrêter")
    parser.add_argument("--filtre", default="", help="avec --lister-categories : mots à chercher dans les catégories")
    parser.add_argument("--categories", default="", help="catégories Overture à collecter, séparées par des virgules")
    parser.add_argument("--mots", default="", help="mots du nom du lieu, séparés par des virgules")
    parser.add_argument("--release", default="", help="version d'Overture Maps (par défaut : la dernière)")
    common_arguments(parser, "overture")
    args = parser.parse_args()

    country = args.pays.upper()
    release = args.release or latest_release()
    con = connect()
    places = f"read_parquet('{load_zone(con, args, country, release)}')"

    if args.lister_categories:
        words = [w.lower() for w in parse_list(args.filtre)]
        rows = con.execute(f"SELECT category, basic_category, count(*) AS n FROM {places} "
                           "GROUP BY 1, 2 ORDER BY n DESC").fetchall()
        rows = [r for r in rows if not words or any(w in f"{r[0]} {r[1]}".lower() for w in words)]
        print(f"Catégories dans la zone ({release}) : catégorie précise | catégorie générale | nombre de lieux")
        for primary, basic, count in rows:
            print(f"  {primary} | {basic} | {count}")
        return 0

    categories, words = parse_list(args.categories), [normalize(w) for w in parse_list(args.mots)]
    if not categories and not words:
        parser.error("indiquez --categories et/ou --mots (voir --lister-categories)")
    conditions, params = [], []
    if categories:
        conditions.append("list_contains(?, category) OR list_contains(?, basic_category) OR list_has_any(alternates, ?)")
        params += [categories] * 3
    for word in words:
        conditions.append("regexp_replace(lower(strip_accents(name)), '[^a-z0-9]+', ' ', 'g') LIKE ?")
        params.append(f"%{word}%")
    region, lat, lon = (column(con, places, c) for c in ("region", "lat", "lon"))
    rows = con.execute(
        f"SELECT id, name, category, basic_category, websites, phones, emails, street, city, postcode, "
        f"{region}, {lat}, {lon} FROM {places} WHERE name IS NOT NULL AND ({' OR '.join(conditions)})", params).fetchall()

    connector = Connector(args, f"Overture Maps {release} : lieux de {args.zone or args.bbox} ({country})")
    connector.stats.update({"version": release, "zone": args.zone or args.bbox, "lieux_trouves": len(rows)})
    for (place_id, name, primary, basic, websites, phones, emails, street, city, postcode, area, y, x) in rows:
        connector.write({
            "id": place_id, "nom": name, "pays": country, "region": area or "", "ville": city or "",
            "code_postal": postcode or "", "adresse": street or "", "latitude": y or "", "longitude": x or "",
            "code_activite": primary or "", "nomenclature": "catégories Overture", "libelle_activite": basic or "",
            # Le site de l'entreprise, pas sa page de réseau social ; seulement un email générique (RGPD).
            "categorie": basic or "", "site_web": next((w for w in websites or [] if w and not is_social_site(w)), ""),
            "telephone": next((p for p in phones or [] if p and "*" not in p), ""),  # numéros masqués écartés
            "email": next((e for e in emails or [] if is_generic_email(e)), ""),
            "source": f"overture:{release}:{place_id}",
        })
    return connector.finish(complete=True)


if __name__ == "__main__":
    main_guard(main)
