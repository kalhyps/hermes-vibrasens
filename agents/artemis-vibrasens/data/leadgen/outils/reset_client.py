"""Remettre l'outil à neuf pour un nouveau client : dossier client vidé (contexte au plan vide), mémoire LinkedIn effacée.

Sans --confirmer, l'outil montre seulement ce qu'il ferait. Il refuse s'il reste une campagne dans
/workspace/campagnes (les supprimer d'abord avec delete_campaign.py), sauf avec --garder-campagnes.
La bibliothèque des connecteurs (/workspace/connecteurs : index.md, fiches, connecteurs) est gardée.

Exemples :
  python reset_client.py              # aperçu
  python reset_client.py --confirmer  # remise à neuf
"""
import argparse
import datetime
import json
import os
import sys

WORKSPACE = os.environ.get("LEADGEN_WORKSPACE", "/workspace")
CLIENT_DIR = os.path.join(WORKSPACE, "client")
CONTEXT_FILE = os.path.join(CLIENT_DIR, "contexte.md")
# Dossier client de la skill cadrage-client (data/skills/prospection/cadrage-client/references/formats.md) :
# tout ce qui décrit l'ancien client est supprimé, contexte.md repart du plan vide.
CLIENT_FILES = ["site.md", "lecture.json", "offre.json", "references.csv", "references-registre.csv", "icp.json",
                "retours.csv"]
SEEN_FILE = os.environ.get("LINKEDIN_SEEN_FILE", os.path.join(WORKSPACE, "connecteurs", "linkedin_seen.json"))
CAMPAIGNS_DIR = os.path.join(WORKSPACE, "campagnes")

# Le plan du fichier est celui de la fiche de rôle de l'orchestrateur (data/SOUL.md).
EMPTY_CONTEXT = """# Contexte client
Mis à jour le : {date}
## Entreprise : nom, site, ce qu'elle vend, à qui
## Problèmes qu'elle sait résoudre
## Cibles validées : secteurs, tailles, postes visés, zones
## Concurrents : nom, site, ce qui les distingue
## Clients actuels : nom, site, secteur, taille (à exclure, et modèles à suivre)
## À exclure : entreprises, secteurs
## Retours sur les campagnes : ce qui a plu ou non, avec la date
"""


def count_lines(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def count_seen(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return len(data) if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirmer", action="store_true", help="remettre à neuf pour de bon (sinon : aperçu)")
    parser.add_argument("--garder-campagnes", action="store_true",
                        help="ne pas refuser quand des campagnes existent encore")
    args = parser.parse_args()

    campaigns = sorted(d for d in os.listdir(CAMPAIGNS_DIR)
                       if os.path.isdir(os.path.join(CAMPAIGNS_DIR, d))) if os.path.isdir(CAMPAIGNS_DIR) else []
    if campaigns and not args.garder_campagnes:
        sys.exit("Campagnes encore présentes : " + ", ".join(campaigns)
                 + ". Supprime-les d'abord (delete_campaign.py), ou relance avec --garder-campagnes.")

    print("Remise à neuf pour un nouveau client")
    if os.path.isfile(CONTEXT_FILE):
        print(f"  {CONTEXT_FILE} : {count_lines(CONTEXT_FILE)} lignes -> plan vide")
    else:
        print(f"  {CONTEXT_FILE} : absent -> plan vide créé")
    client_files = [f for f in CLIENT_FILES if os.path.isfile(os.path.join(CLIENT_DIR, f))]
    if client_files:
        print(f"  {CLIENT_DIR} : {', '.join(client_files)} -> supprimés")
    if os.path.isfile(SEEN_FILE):
        n = count_seen(SEEN_FILE)
        print(f"  {SEEN_FILE} : {n if n is not None else '?'} pages mémorisées -> supprimé")
    else:
        print(f"  {SEEN_FILE} : déjà absent")
    if campaigns:
        print(f"  campagnes gardées : {', '.join(campaigns)}")
    print("  gardés : connecteurs, fiches par pays et index.md de /workspace/connecteurs")

    if not args.confirmer:
        print("Aperçu seulement : relance avec --confirmer pour remettre à neuf.")
        return

    os.makedirs(os.path.dirname(CONTEXT_FILE), exist_ok=True)
    with open(CONTEXT_FILE, "w", encoding="utf-8") as f:
        f.write(EMPTY_CONTEXT.format(date=datetime.date.today().isoformat()))
    for f in client_files:
        os.remove(os.path.join(CLIENT_DIR, f))
    if os.path.isfile(SEEN_FILE):
        os.remove(SEEN_FILE)
    print("Remis à neuf : dossier client vidé, contexte au plan vide, mémoire LinkedIn effacée.")


if __name__ == "__main__":
    main()
