"""Supprimer une campagne : ses cartes Kanban (archivées puis effacées du tableau) et son dossier.

Les cartes d'une campagne sont celles dont l'espace de travail est /workspace/campagnes/<campagne>.
Sans --confirmer, l'outil montre seulement ce qu'il supprimerait. Une carte en cours (running) bloque la suppression :
l'arrêter d'abord.

Exemples :
  python delete_campaign.py vibrasens-industriel              # aperçu
  python delete_campaign.py vibrasens-industriel --confirmer  # suppression
  python delete_campaign.py vibrasens-industriel --confirmer --garder-dossier
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = "/workspace/campagnes"


def hermes(*args):
    result = subprocess.run(["hermes", "kanban", *args], capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"Échec de « hermes kanban {' '.join(args)} » : {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def campaign_tasks(path):
    tasks = json.loads(hermes("list", "--archived", "--json") or "[]")
    if isinstance(tasks, dict):
        tasks = tasks.get("tasks", [])
    return [t for t in tasks if (t.get("workspace_path") or "").rstrip("/") == path]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("campagne", help="nom du dossier de la campagne, dans /workspace/campagnes")
    parser.add_argument("--confirmer", action="store_true", help="supprimer pour de bon (sinon : aperçu)")
    parser.add_argument("--garder-dossier", action="store_true", help="ne supprimer que les cartes Kanban")
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.campagne):
        sys.exit("Nom de campagne invalide : seulement le nom du dossier, sans chemin.")
    path = os.path.join(ROOT, args.campagne)
    tasks = campaign_tasks(path)
    exists = os.path.isdir(path)
    if not tasks and not exists:
        sys.exit(f"Aucune campagne « {args.campagne} » : ni dossier, ni carte Kanban.")

    print(f"Campagne {args.campagne}")
    for t in tasks:
        print(f"  carte {t['id']}  {t.get('status')}  {t.get('title')}")
    print(f"  dossier {path} : {'présent' if exists else 'absent'}{' (gardé)' if args.garder_dossier else ''}")

    running = [t["id"] for t in tasks if t.get("status") == "running"]
    if running:
        sys.exit(f"Cartes en cours : {', '.join(running)}. Arrête-les avant de supprimer la campagne.")
    if not args.confirmer:
        print("Aperçu seulement : relance avec --confirmer pour supprimer.")
        return

    to_archive = [t["id"] for t in tasks if t.get("status") != "archived"]
    if to_archive:
        hermes("archive", *to_archive)
    if tasks:
        hermes("archive", "--rm", *[t["id"] for t in tasks])
    if exists and not args.garder_dossier:
        shutil.rmtree(path)
    left = campaign_tasks(path)
    if left or (os.path.isdir(path) and not args.garder_dossier):
        sys.exit(f"Suppression incomplète : {len(left)} carte(s) restante(s), "
                 f"dossier {'présent' if os.path.isdir(path) else 'supprimé'}.")
    print(f"Supprimée : {len(tasks)} carte(s){'' if args.garder_dossier else ' et le dossier'}.")


if __name__ == "__main__":
    main()
