# Connecteurs prêts et outils de collecte

Commande : `/opt/leadgen/venv/bin/python /opt/data/leadgen/outils/<outil>`, lancée dans le dossier de la campagne. Chaque connecteur écrit `sources/<nom>.csv` et `sources/<nom>-stats.json`. Options communes à tous :
- `--nom <source>` : nom du fichier de sortie. Un même connecteur lancé avec d'autres paramètres peut écrire dans le même fichier (les requêtes s'ajoutent) ou dans un autre (`--nom registre-fr-mots`).
- `--duree-max <secondes>` : arrêt propre (défaut 180 s), puis `INCOMPLET` : relancer la même commande.
- `--limite <n>` : essai seulement. La source reste « incomplète » : relance ensuite avec `--recommencer`, sans `--limite`.
- `--recommencer` : efface ce que la source a collecté et repart de zéro.

## France : `connectors/fr_companies.py`
API Recherche d'entreprises : le registre officiel (SIRENE et RNE), gratuit, sans clé.
- `--naf 25.62A,25.62B` : activité principale. La nomenclature NAF rév. 2 a des codes à 4 chiffres et une lettre ; un code NACE à 4 chiffres donne souvent deux codes NAF (25.62 → 25.62A et 25.62B).
- `--mots "decolletage,decolleteur"` : mots du nom ou de l'enseigne, pour les entreprises déclarées sous un autre code.
- `--noms sources/<liste>.csv` : rapprochement d'une liste importée avec le registre (numéro SIREN, effectif, établissement). Une colonne `numero_officiel` (SIREN) donne un rapprochement sûr ; sinon le nom, et si possible `ville` ou `code_postal`. La précision est écrite dans `indices` : « rapprochement DOUTEUX : nom seul » veut dire qu'un homonyme a pu être pris. Les introuvables et les douteux sont dans les statistiques.
- Zone : `--departement 74,01`, `--region 84` (codes INSEE) ou `--epci 200093201` (intercommunalité : métropole, communauté d'agglomération ou de communes ; le code est le SIREN de l'EPCI, donné par `curl -s "https://geo.api.gouv.fr/epcis?nom=<nom>&fields=nom,code"`) ; sans zone, toute la France. L'outil interroge département par département, ou EPCI par EPCI. Pour une métropole ou une agglomération, prends l'EPCI : un département déborde, une liste de villes en oublie.
- Ce qu'il donne :
  - SIREN, établissement de la zone (SIRET, adresse, effectif de l'établissement, siège ou non) ;
  - tranche d'effectif, `employeur` (oui ou non), catégorie (PME, ETI, GE) ;
  - chiffre d'affaires quand il est publié, forme juridique ;
  - `indices` : index Egapro publié (obligatoire à partir de 50 salariés), nombre d'établissements.
- Pièges :
  - l'effectif est souvent vide pour de vraies entreprises : les indices et le chiffre d'affaires comblent une partie du manque ;
  - une entreprise sans aucun établissement ouvert dans la zone n'est pas écrite (compteur `sans_etablissement_actif_dans_la_zone`).

## Royaume-Uni : `connectors/gb_companies_house.py`
Registre Companies House. Il faut la clé gratuite `COMPANIES_HOUSE_API_KEY` ; sans elle, l'outil l'annonce (`CLÉ ABSENTE`).
- `--sic 25620` ou `--nace 25.62` (converti en SIC à 5 chiffres), `--mots`, `--lieu "West Midlands,Coventry"`.
- Le registre ne publie pas d'effectif. L'outil lit le type des derniers comptes déposés, qui donne un ordre de grandeur : micro-entity, small, medium, full. `--sans-comptes` va plus vite, mais sans estimation de taille.
- Comptes « dormant » : l'entreprise n'a pas d'activité. Elle est signalée dans `indices`, pas écartée : exclus-la avec une règle si le brief le justifie.

## Tous pays : `connectors/overture.py`
Overture Maps : lieux du monde entier (usines, ateliers, bureaux, commerces), gratuit, sans clé.
- `--pays US --zone "Ohio"` : la zone est cherchée par son nom local (région, État, province, comté, ville) et filtrée sur son contour exact. `--bbox` permet une emprise libre.
- `--lister-categories --filtre "machin,metal"` : catégories présentes dans la zone, avec leur nombre de lieux. Commence toujours par là.
- `--categories machine_shop,metal_fabricator` et/ou `--mots "precision,cnc"` (mots du nom).
- Ce qu'il donne : nom, adresse, position, catégorie, souvent le site, le téléphone, parfois l'email ; ni effectif ni numéro officiel.
- Pièges :
  - les catégories mêlent fabricants, revendeurs, loueurs et magasins : prends large, puis exclus avec des règles munies d'un `sauf` ;
  - une même entreprise peut avoir plusieurs lieux : la fusion les réunit ;
  - la première lecture d'une zone dure 1 à 3 minutes et charge le réseau. Le fichier `overture-<pays>-<zone>-<version>.parquet` la garde ensuite dans la campagne.

## Listes et fichiers : `import_list.py`
- `--fichier listes/<source>.json` : liste JSON d'objets que tu as écrite après lecture d'une page ; `.csv` et `.jsonl` sont aussi acceptés.
- `--nom <source>`, `--pays <XX>` (pour les lignes sans pays), `--source-url "<page ou fichier>"` (pour les lignes sans `source`), `--description "<ce qu'est la liste>"`.
- `--colonnes "nom=Company Name,ville=City,site_web=Website"` : correspondance avec les colonnes du format commun quand les noms diffèrent.
- `--nomenclature NAICS` : nomenclature des codes d'activité du fichier.
- Un import remplace la source du même nom : pour compléter une liste, réimporte-la en entier.

## Bibliothèque des campagnes : `/workspace/connecteurs/`
Connecteurs écrits par le collecteur lors de campagnes précédentes, et fiches des sources par pays (`fiches/<PAYS>.md`). L'index dit ce que fait chaque connecteur, ce qu'il demande (clé) et s'il a été relu. Un connecteur « à relire » s'utilise, mais vérifie son échantillon avec plus de soin.

## Fusion, contrôle, classement, vagues
- `merge_sources.py` : toutes les sources → `vivier.csv`. Relance-le après chaque nouvelle source, chaque correction ou chaque ligne ajoutée à `liens.csv`.
- `check_collection.py --pays FR` : contrôles et échantillon. `--chercher "Nom 1;Nom 2"` : contrôle de rappel. `--fichier essai/<source>.csv` : contrôle d'une seule source.
- `rank_pool.py --apercu 20` : classement selon `classement.json`. Relance-le après chaque fusion.
- `waves.py --taille <N> --objectif <leads>` : envoi d'une vague ; `waves.py --etat` : point des vagues et de la réserve.
