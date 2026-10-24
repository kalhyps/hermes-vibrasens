---
name: enrichissement-entreprises
description: "Trouver et vérifier le site officiel de chaque entreprise collectée, lire ses pages clés, écrire sa fiche et la garder ou l'écarter."
version: 1.4.0
metadata:
  hermes:
    tags: [prospection, enrichissement, sites-web, verification]
---

# Enrichir les entreprises d'une campagne

## Vagues
Le collecteur envoie les entreprises par vagues : `1-collecte.csv` les contient toutes, avec leur numéro dans la colonne `vague`. Ta tâche porte sur une vague, donnée par la ligne `Vague : <n>` de la tâche (vague 1 par défaut). Les outils de recherche et de lecture ne traitent que les entreprises qu'ils n'ont pas encore vues : ceux des vagues précédentes restent acquis. Les fiches des vagues précédentes aussi : ne les réécris pas.

## Méthode
1. Lis la tâche, puis `brief.md`. Repère les mots-clés utiles au brief : activité, équipements, signaux recherchés, et les signes de chaque axe d'approche (section « Axes d'approche »), dans la langue du pays de la cible.
2. Trouve les sites officiels :
   `/opt/leadgen/venv/bin/python /opt/data/leadgen/outils/find_website.py`
   L'outil lit `1-collecte.csv` et écrit `sites.csv` : site, confiance (haute si le numéro officiel de l'entreprise, SIREN ou autre, figure sur le site ; sinon moyenne, basse ou aucune) et preuve. Il essaie d'abord le site donné par la collecte, les adresses plausibles et les moteurs gratuits ; Firecrawl (payant) ne sert qu'aux cas qu'ils n'ont pas résolus. Il s'arrête de lui-même au bout de 3 minutes environ : **relance exactement la même commande tant qu'il annonce des entreprises restantes**, jusqu'à « restantes : 0 ». S'il signale ensuite que la recherche web était indisponible ou que le réseau était peut-être saturé, relance-le une fois avec `--reessayer-erreurs`.
3. Seulement quand l'étape 2 est terminée, lis les sites :
   `/opt/leadgen/venv/bin/python /opt/data/leadgen/outils/read_website.py --mots-cles "mot1,mot2,mot3" --cibles "mot1,mot2"`
   `--mots-cles` : les mots à compter (activité, équipements, signaux du brief). `--cibles` : les mots qui désignent les pages à lire en priorité, celles qui prouvent les critères du brief (par exemple `machines,parc,atelier,moyens,production`). L'outil lit jusqu'à 10 pages par site, en s'aidant de son plan, et écrit `pages/<id>.md` (le texte des pages) et `lecture.csv` (pages lues, email générique, téléphone, réseaux, mots-clés, indices de recrutement). Même principe : relance tant qu'il reste des sites, puis une fois avec `--reessayer-erreurs` s'il le demande.
   Si la clé Firecrawl est présente, l'outil l'utilise de lui-même pour les pages qui exigent un navigateur. Firecrawl est payant : les outils tiennent la dépense de la campagne dans `firecrawl.json`, et s'arrêtent de l'utiliser si le brief fixe un budget et qu'il est atteint.

   Ne fixe pas de délai court à ces commandes : les outils gèrent eux-mêmes leur durée.
   **Signaux officiels**, pour les entreprises françaises (SIREN), une fois les sites lus :
   `/opt/leadgen/venv/bin/python /opt/data/leadgen/outils/signaux_officiels.py --base 1-collecte.csv --vague <n> --statut tous --depuis <AAAA-MM-JJ>`
   `--depuis` : le début de la plus longue période des signaux du brief (« depuis le 2025-10-20 »). L'outil interroge le BODACC (ouverture d'établissement, changement de direction, déménagement, achat ou cession de fonds ; en alerte : procédure collective, radiation) et l'annuaire RGE de l'ADEME (qualifications et leur date), gratuits et sans clé, et écrit `signaux-officiels.csv`. Relance la même commande tant qu'il annonce INCOMPLET. Ses lignes s'affichent ensuite d'elles-mêmes dans `extract_passages.py`, entreprise par entreprise.
4. Pour **chaque** entreprise de ta vague, par lots de 10, lis ce que disent ses pages. Ne lis pas les pages entières et n'écris pas de script d'extraction : l'outil donne, entreprise par entreprise, son site, ses contacts, ses données de collecte (numéro officiel, activité, effectif, indices, sources) et les meilleurs passages pour chaque critère, avec l'adresse de leur page :
   `/opt/leadgen/venv/bin/python /opt/data/leadgen/outils/extract_passages.py --fiches enrichissement --vague <n> --critere "activite=usinage,decolletage,tournage" --critere "moyens=machines,parc,atelier,centres d usinage" --critere "effectif=salaries,collaborateurs,employes,personnes" --critere "signaux=recrut,investi,nouvelle machine,nouveau logiciel,lancement,nouvelle offre,actualite"`
   Les critères (`nom=mot1,mot2`) viennent du brief : activité, équipements, taille, signaux, et un critère par axe d'approche avec ses signes (`axe-visite=métré,visite technique,rendez-vous à domicile`), dans la langue des sites (plusieurs langues si le pays en a plusieurs). Un critère `adresse` (ville et code postal de l'entreprise) est ajouté d'office. L'outil donne les 10 entreprises suivantes qui n'ont pas encore de fiche et **le nom du fichier où écrire leurs fiches** ; relance la même commande après chaque lot, jusqu'à « toutes les entreprises ont leur fiche ». Une entreprise précise : `--ids <id>`. Ouvre `pages/<id>.md` seulement pour un cas douteux.
   Écris les fiches du lot dans le fichier indiqué (`enrichissement/vague-<n>-lot-<k>.json`), une liste JSON d'objets (une fiche seule : `enrichissement/<id>.json`, qui complète ou corrige celle du lot : ses clés l'emportent, les autres restent) :
   `[{"id": "…", "site_web": "…", "confiance_site": "…", "nom_commercial": "…", "groupe": "…", "activite": "…", "clients_vises": "…", "moyens": "…", "certifications": "…", "signaux": "…", "alerte": "…", "effectif_site": "…", "axes": "…", "email_generique": "…", "telephone": "…", "statut": "gardée" ou "écartée", "raison": "…", "sources": ["URL", …]}, …]`
   - `statut` ne prend que deux valeurs. `écartée` : l'entreprise ne correspond clairement pas à la cible, ou aucun site n'a été trouvé (raison « aucun site vérifiable »). `gardée` : dans tous les autres cas, **y compris en cas de doute**. Note alors le doute dans `raison` : c'est le qualificateur qui tranchera. Si le brief parle de classer des entreprises « à revoir », ce statut appartient au qualificateur, pas à toi : n'utilise jamais « à revoir ».
   - `raison` : une phrase propre à l'entreprise, qui dit ce qui la fait garder ou écarter (pas une formule commune à toutes).
   - `nom_commercial` : le nom sous lequel l'entreprise se présente, s'il diffère de la raison sociale ; `groupe` : son groupe, s'il en a un.
   - `activite` : ce que fait l'entreprise, en une phrase précise tirée de ses pages (métier, produits, marchés), pas le libellé du code d'activité.
   - `moyens` : ses moyens de production, chiffrés quand les pages le disent (« 33 presses de 50 à 580 t », « flotte de 120 tracteurs ») ; `certifications` : celles qu'elle affiche ; `signaux` : les faits récents utiles au brief, de la période fixée par le brief, en commençant par les « signal officiel » que montre `extract_passages.py` (recopie-les tels quels, avec leur date et leur lien) : recrutements, changements d'outil (nouveau logiciel, nouvelle machine, migration), lancements d'offre ou de produit, et les signaux propres au brief. Chacun s'écrit `<date> : <fait> (<lien>)`, séparés par ` ; ` (« 2026-09 : recrute un responsable maintenance (https://…/carrieres) »). Un fait sans date ou sans lien ne va pas dans `signaux` ; la page qui le prouve va aussi dans `sources`. Regarde les pages d'actualités, de carrières et de nouveautés du site ; `alerte` : une difficulté publiée (procédure collective, cession, plan social, fermeture), sinon vide ; une « alerte officielle » s'y recopie toujours, avec sa date et son lien. Une « certification officielle » (RGE en cours de validité) va dans `certifications`, avec son lien.
   - `axes` : les faits du site qui appuient un axe d'approche du brief, chacun `<axe> : <fait> (<lien>)`, séparés par ` ; ` (« Visite avant devis : métré gratuit à domicile avant chaque devis (https://…/devis) ») ; vide si aucun. Seulement ce que la page dit, pas une déduction.
   - `effectif_site` : l'effectif annoncé sur le site (« 45 collaborateurs »), s'il y en a un, surtout quand le registre ne le donne pas.
   - `email_generique`, `telephone` : ceux de `lecture.csv` (emails au nom de l'entreprise, téléphones fixes du pays) ; à défaut, ceux de la collecte (colonnes `email`, `telephone`). Seulement une boîte générique qui sert au contact commercial (pas `rgpd@`, `privacy@`, `webmaster@`, ni une adresse nominative) ; de préférence le téléphone du site de la zone plutôt que celui d'un siège lointain.
   - **Toute page qui te sert de preuve et qui n'est pas dans `pages/<id>.md`** (fiche d'une fédération ou d'un pôle, offre d'emploi, article, page du site trouvée par une recherche) : ajoute-la aux preuves, pour que le qualificateur puisse la relire et la citer :
     `/opt/leadgen/venv/bin/python /opt/data/leadgen/outils/read_website.py --ids <id> --urls "<adresse1>,<adresse2>"`
   - Pas de site trouvé : une recherche web ciblée (web_search : nom et ville, puis numéro officiel) avant de l'écarter. Si tu trouves et vérifies le site, enregistre-le avec `/opt/leadgen/venv/bin/python /opt/data/leadgen/outils/set_site.py --id <id> --site <url> --preuve "<ce qui le prouve>"`, puis relance la lecture des sites. Utilise aussi `set_site.py` pour corriger un site faux (homonyme), avec `--aucun` s'il n'y a pas de bon site. N'écris pas de script pour ça.
   - Confiance `basse` : vérifie d'abord que le site est bien celui de l'entreprise (nom, ville, activité).
   - Sites en langue étrangère : rédige tes fiches en français, et cite les preuves dans leur langue d'origine.
   - `peu_de_texte` vaut `oui` : le site a sans doute besoin d'un navigateur. Tu peux l'ouvrir avec le navigateur, une page à la fois.

   Si la tâche est relancée, les fichiers déjà écrits restent acquis : l'outil reprend aux entreprises sans fiche.
5. Assemble le fichier final, toutes vagues confondues :
   `/opt/leadgen/venv/bin/python /opt/data/leadgen/outils/assemble_csv.py --dossier enrichissement --base 1-collecte.csv --sortie 2-enrichissement.csv`
   S'il signale des entreprises sans fiche ou un fichier JSON invalide, corrige puis assemble à nouveau. **Ne termine la tâche que lorsque chaque entreprise de `1-collecte.csv` a sa fiche.**
6. Mets à jour le point des vagues : `/opt/leadgen/venv/bin/python /opt/data/leadgen/outils/waves.py --etat`

## Résumé de fin de tâche
Pour ta vague : entreprises traitées, sites trouvés par niveau de confiance, entreprises gardées et écartées, avec les raisons principales ; crédits Firecrawl dépensés (affichés par les outils, total dans `firecrawl.json`).
