"""Socle des connecteurs de collecte : format commun, client HTTP poli, reprise et statistiques.

Un connecteur lit UNE source et écrit, dans le dossier de la campagne :
  sources/<nom>.csv         les entreprises, au format commun (FIELDS) ;
  sources/<nom>-stats.json  ce qu'il a fait : requêtes, lignes, plafonds atteints, terminé ou non ;
  sources/<nom>-etat.json   où il en est, pour reprendre après une interruption.
Il ne filtre que sur la zone et l'activité demandées : les seuils du brief vont dans le classement (rank_pool.py).

Un connecteur s'arrête de lui-même après --duree-max secondes et affiche « INCOMPLET » (code de sortie 3) :
il suffit de relancer la même commande pour continuer. À la fin, il affiche « TERMINÉ » (code 0).

Utilisation dans un connecteur (voir outils/connectors/ et les modèles de la skill construire-connecteur) :
    import sys
    sys.path.insert(0, "/opt/data/leadgen/outils")
    from connector_base import Connector, Http, common_arguments, make_id
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import USER_AGENT, normalize  # noqa: E402

# Format commun d'une entreprise, quelle que soit la source. Colonnes détaillées dans la skill collecte-entreprises
# (references/format.md). Seuls id, nom, pays et source sont obligatoires ; une donnée inconnue reste vide.
FIELDS = [
    "id", "id_registre", "registre", "nom", "pays", "region", "ville", "code_postal", "adresse", "latitude", "longitude",
    "code_activite", "nomenclature", "libelle_activite", "forme_juridique", "effectif", "effectif_min", "employeur",
    "effectif_etablissement", "siege", "id_etablissement", "nb_etablissements", "categorie", "date_creation", "ca",
    "annee_ca", "indices", "site_web", "telephone", "email", "source", "source_etablissement", "source_nom",
    "meme_que", "collecte_le",
]
REQUIRED = ("id", "nom", "pays", "source")
DONE, INCOMPLETE = 0, 3  # codes de sortie


def make_id(*parts, prefix="x"):
    """Identifiant stable d'une entreprise sans numéro officiel, tiré de son nom, de sa ville, de son pays…"""
    digest = hashlib.sha1("|".join(normalize(str(p)) for p in parts).encode()).hexdigest()[:12]
    return f"{prefix}-{digest}"


def clean_id(value):
    """Identifiant utilisable dans un nom de fichier (pages/<id>.md)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-.")


def read_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    return default


def write_json(path, data):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


class Http:
    """Client HTTP poli : User-Agent, délai minimal entre deux requêtes, reprises avec attente croissante
    sur les erreurs réseau, 429 (en respectant Retry-After) et 5xx."""

    def __init__(self, delay=1.0, headers=None, auth=None, timeout=60, attempts=6):
        self.delay, self.attempts, self._last = delay, attempts, 0.0
        self.client = httpx.Client(timeout=timeout, auth=auth, follow_redirects=True,
                                   headers={"User-Agent": USER_AGENT, **(headers or {})})

    def _wait(self):
        pause = self.delay - (time.monotonic() - self._last)
        if pause > 0:
            time.sleep(pause)
        self._last = time.monotonic()

    def request(self, method, url, **kwargs):
        error = ""
        for attempt in range(self.attempts):
            self._wait()
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__} : {exc}"
                time.sleep(min(60, 2 ** attempt))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After", "")
                time.sleep(float(retry_after) if retry_after.isdigit() else min(120, 2 ** (attempt + 1)))
                error = f"HTTP {response.status_code}"
                continue
            return response
        raise RuntimeError(f"source indisponible après {self.attempts} essais ({error}) : {url}")

    def get_json(self, url, params=None, **kwargs):
        """Réponse JSON ; None si la ressource n'existe pas (404). Les autres erreurs HTTP sont levées."""
        response = self.request("GET", url, params=params, **kwargs)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def download(self, url, path):
        """Télécharge un fichier une seule fois : un fichier complet déjà présent est gardé."""
        if os.path.exists(path):
            return path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        partial = path + ".partiel"
        self._wait()
        with self.client.stream("GET", url) as response:
            response.raise_for_status()
            with open(partial, "wb") as handle:
                for block in response.iter_bytes(1 << 20):
                    handle.write(block)
        os.replace(partial, path)
        return path


def common_arguments(parser, default_name):
    parser.add_argument("--nom", default=default_name, help=f"nom de la source : sources/<nom>.csv (défaut : {default_name})")
    parser.add_argument("--dossier", default="sources", help="dossier des sources, dans la campagne (défaut : sources)")
    parser.add_argument("--duree-max", type=int, default=180,
                        help="secondes avant de s'arrêter proprement ; relancer la même commande pour continuer (0 : sans limite)")
    parser.add_argument("--limite", type=int, default=0,
                        help="essai seulement : s'arrêter après ce nombre de lignes (la source reste marquée incomplète)")
    parser.add_argument("--recommencer", action="store_true", help="effacer ce que la source a collecté et repartir de zéro")


class Connector:
    """Sortie d'une source : lignes écrites au fil de l'eau, état de reprise, statistiques."""

    def __init__(self, args, description, http=None):
        self.args, self.http = args, http
        os.makedirs(args.dossier, exist_ok=True)
        base = os.path.join(args.dossier, args.nom)
        self.path, self.stats_path, self.state_path = base + ".csv", base + "-stats.json", base + "-etat.json"
        if args.recommencer:
            for path in (self.path, self.stats_path, self.state_path):
                if os.path.exists(path):
                    os.remove(path)
        self.state = read_json(self.state_path, {"requetes_faites": [], "curseurs": {}})
        self.stats = read_json(self.stats_path, {"source": args.nom, "description": description, "lignes": 0,
                                                 "doublons": 0, "rejetees": 0, "requetes": 0, "plafonds": [],
                                                 "executions": []})
        self.stats["description"] = description
        self.stats["executions"].append({"debut": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                         "commande": " ".join(sys.argv)})
        exists = os.path.exists(self.path) and os.path.getsize(self.path) > 0
        self.seen = set()
        if exists:
            with open(self.path, encoding="utf-8", newline="") as handle:
                self.seen = {row["id"] for row in csv.DictReader(handle)}
        self._handle = open(self.path, "a", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._handle, fieldnames=FIELDS, extrasaction="ignore")
        if not exists:
            self._writer.writeheader()
        self._start, self._written = time.monotonic(), 0

    # --- arrêt propre -----------------------------------------------------------------------------------------
    def time_is_up(self):
        return bool(self.args.duree_max) and time.monotonic() - self._start >= self.args.duree_max

    def limit_reached(self):
        return bool(self.args.limite) and self._written >= self.args.limite

    def should_stop(self):
        return self.time_is_up() or self.limit_reached()

    # --- lignes ---------------------------------------------------------------------------------------------
    def write(self, row):
        """Ajoute une entreprise. Renvoie False si un champ obligatoire manque ou si elle est déjà dans la source."""
        row = {key: "" if value is None else str(value).strip() for key, value in row.items()}
        row["id"] = clean_id(row.get("id"))
        if any(not row.get(field) for field in REQUIRED):
            self.stats["rejetees"] += 1
            return False
        if row["id"] in self.seen:
            self.stats["doublons"] += 1
            return False
        row["source_nom"] = self.args.nom
        row["collecte_le"] = date.today().isoformat()
        self._writer.writerow(row)
        self.seen.add(row["id"])
        self.stats["lignes"] += 1
        self._written += 1
        return True

    # --- reprise ----------------------------------------------------------------------------------------------
    def is_done(self, key):
        return key in self.state["requetes_faites"]

    def mark_done(self, key):
        if key not in self.state["requetes_faites"]:
            self.state["requetes_faites"].append(key)
        self.state["curseurs"].pop(key, None)
        self.save()

    def cursor(self, key, default=None):
        return self.state["curseurs"].get(key, default)

    def advance(self, key, cursor):
        self.state["curseurs"][key] = cursor
        self.save()

    def ceiling(self, message):
        """Signale qu'une requête n'a pas pu tout ramener (plafond de la source) : à découper."""
        if message not in self.stats["plafonds"]:
            self.stats["plafonds"].append(message)
        print(f"PLAFOND : {message}", file=sys.stderr)

    def save(self):
        self._handle.flush()
        write_json(self.state_path, self.state)
        write_json(self.stats_path, self.stats)

    # --- boucle standard d'une source paginée -----------------------------------------------------------------
    def run(self, queries, read_page, to_rows):
        """queries : liste de (clé unique, paramètres) ;
        read_page(http, paramètres, curseur) -> (éléments, curseur suivant ou None) ; le premier curseur vaut None ;
        to_rows(élément, paramètres) -> une ligne (dict), une liste de lignes ou None."""
        for key, params in queries:
            if self.is_done(key):
                continue
            cursor = self.cursor(key)
            while True:
                if self.should_stop():
                    return self.finish(complete=False)
                items, following = read_page(self.http, params, cursor)
                self.stats["requetes"] += 1
                for item in items:
                    rows = to_rows(item, params)
                    for row in rows if isinstance(rows, list) else [rows] if rows else []:
                        self.write(row)
                if following is None:
                    break
                cursor = following
                self.advance(key, cursor)
            self.mark_done(key)
        return self.finish(complete=True)

    def finish(self, complete):
        """Écrit les statistiques et annonce TERMINÉ, ESSAI ou INCOMPLET. Renvoie le code de sortie."""
        trial = bool(self.args.limite)
        self.stats["termine"] = complete and not trial
        self.stats["mis_a_jour"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.save()
        self._handle.close()
        total = f"{self.stats['lignes']} entreprises dans {self.path}"
        if self.stats["plafonds"]:
            print(f"ATTENTION : {len(self.stats['plafonds'])} requête(s) au plafond de la source, à découper "
                  f"(voir {self.stats_path}).")
        if trial:
            print(f"ESSAI : {total} (--limite {self.args.limite}). Relancez sans --limite et avec --recommencer pour la vraie collecte.")
            return INCOMPLETE
        if complete:
            print(f"TERMINÉ : {total} ; {self.stats['doublons']} doublons et {self.stats['rejetees']} lignes "
                  f"incomplètes ignorés ; statistiques dans {self.stats_path}")
            return DONE
        print(f"INCOMPLET : {total} pour l'instant. Relancez exactement la même commande pour continuer.")
        return INCOMPLETE


def parse_list(value):
    """« a, b ,c » -> ["a", "b", "c"]"""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def main_guard(function):
    """Lance le connecteur ; une erreur inattendue est affichée clairement, avec un code de sortie 1."""
    try:
        sys.exit(function())
    except KeyboardInterrupt:
        sys.exit(INCOMPLETE)
    except Exception as exc:  # message lisible pour l'agent, sans pile d'appels
        print(f"ERREUR : {type(exc).__name__} : {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
