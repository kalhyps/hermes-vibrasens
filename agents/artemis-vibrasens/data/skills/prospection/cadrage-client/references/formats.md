# Formats du dossier client (`/workspace/client/`)

| Fichier | Écrit par | Contenu |
|---|---|---|
| `site.md`, `lecture.json` | `client_site.py` | texte des pages du site du client, une rubrique `## <rubrique> : <adresse>` par page ; pages lues et date |
| `offre.json` | orchestrateur (étape 1) | ce que vend le client, lu sur son site et corrigé par lui |
| `references.csv` | orchestrateur (étape 2) | ses vrais clients, et les affaires perdues s'il en donne |
| `icp.json` | orchestrateur (étape 3) | la cible validée : critères, taille, activités, personas, déclencheurs, exclusions |
| `retours.csv` | orchestrateur (après livraison) | les retours du client sur les leads livrés |
| `contexte.md` | orchestrateur | la version lisible, que le client relit et corrige (plan dans `SOUL.md`) |

Tous les textes en français ; les citations dans la langue de la page, entre guillemets, suivies de l'adresse entre parenthèses.

## `offre.json`
```json
{
  "client": {"nom": "Kalhyps", "site": "https://www.exemple.fr", "pays": "FR"},
  "lu_le": "2026-10-19",
  "prochaine_mise_a_jour": "2027-01-19",
  "offres": [
    {
      "nom": "Maintenance prédictive des presses",
      "description": "Capteurs et analyse des vibrations sur les presses à injection, avec alerte avant la panne.",
      "pour_qui": "Plasturgistes de 20 à 250 salariés exploitant un parc de presses",
      "probleme_resolu": "Arrêts non planifiés des presses et pièces de rechange commandées en urgence",
      "preuve": "« Nos clients réduisent leurs arrêts imprévus de 30 % la première année » (https://www.exemple.fr/offre)",
      "axes": [
        {"nom": "Arrêts imprévus", "probleme": "une presse qui casse en pleine série arrête la livraison", "signes": ["3x8", "production en continu", "délais de livraison courts"], "ce_que_l_offre_change": "l'alerte arrive avant la panne, la réparation se planifie", "a_ne_pas_promettre": "un taux de panne chiffré pour le prospect", "cible": "plasturgistes 20-250 salariés", "origine": "proposé, validé par le client le 2026-10-20"},
        {"nom": "Parc vieillissant", "probleme": "les presses anciennes tombent plus souvent en panne et leurs pièces sont longues à obtenir", "signes": ["presses de plus de 15 ans", "rétrofit", "pièces de rechange"], "ce_que_l_offre_change": "les capteurs se posent sur les presses anciennes et disent laquelle surveiller", "a_ne_pas_promettre": "", "cible": "plasturgistes 20-250 salariés", "origine": "dit par le client, 2026-10-20"}
      ]
    }
  ],
  "references_citees": [
    {"nom": "Plasti-Ain", "secteur": "injection plastique", "ce_qui_est_dit": "12 presses équipées en 2025", "url": "https://www.exemple.fr/references"}
  ],
  "differenciateurs": [
    {"affirmation": "Installation sans arrêt de production", "preuve": "« pose en 2 heures, presse en marche » (https://www.exemple.fr/offre)"}
  ],
  "certifications": ["ISO 9001"],
  "mots_cles_cible": {"fr": ["injection plastique", "presses", "plasturgie"], "de": ["Spritzguss", "Spritzgießmaschinen"], "en": ["injection moulding", "injection molding"]},
  "a_ne_jamais_affirmer": ["que l'on remplace la maintenance préventive du constructeur"],
  "questions_restantes": ["Le site dit « leader en Auvergne-Rhône-Alpes » sans chiffre : à ne pas reprendre tel quel"],
  "corrections_client": [{"date": "2026-10-20", "texte": "Nous équipons aussi les presses à découper, pas seulement l'injection"}]
}
```

## `references.csv`
Séparateur « , » (le format des sources, lu tel quel par les connecteurs), une ligne par client (ou affaire perdue), colonnes :

| Colonne | Contenu |
|---|---|
| `id` | `ref-01`, `ref-02`… (sert au rapprochement avec le registre) |
| `type` | `client` ou `perdu` |
| `nom` | tel que le client le dit |
| `ville`, `pays` | pays en code ISO |
| `site_web` | si connu |
| `ce_quon_lui_vend` | l'offre, en quelques mots (pour une affaire perdue : ce qui était proposé) |
| `depuis` | année du premier contrat (vide pour une affaire perdue) |
| `raison` | pour une affaire perdue : pourquoi, selon le client ; pour un client : ce qui l'a convaincu, si le client le dit |
| `numero_officiel`, `code_activite`, `effectif`, `trouve_dans_registre` | reportés depuis `references-registre.csv` (sortie de `fr_companies.py --noms`, colonne `meme_que` = `id` de la référence) : `trouve_dans_registre` vaut `oui`, `non` ou `douteux` (homonyme : `indices` dit « rapprochement DOUTEUX : nom seul »). `numero_officiel` peut aussi venir du client (SIREN) : il rend le rapprochement sûr |
| `ajoute_le` | date |

## `icp.json`
```json
{
  "valide_le": "2026-10-20",
  "offre": "Maintenance prédictive des presses",
  "indispensables": [
    {"critere": "Exploite un parc de presses (injection ou découpe)", "comment_verifier": "page moyens ou parc machines du site ; mot « presses » avec un nombre", "mots_cles": {"fr": ["presses", "parc machines"], "de": ["Spritzgießmaschinen", "Maschinenpark"]}}
  ],
  "forts": [
    {"critere": "Au moins 10 presses", "poids": 3, "comment_verifier": "nombre sur la page moyens"},
    {"critere": "Travaille en 2x8 ou 3x8", "poids": 2, "comment_verifier": "page recrutement ou entreprise"}
  ],
  "disqualifiants": [
    {"critere": "Négociant ou distributeur sans production", "raison": "pas de parc de machines à surveiller"},
    {"critere": "Filiale d'un groupe qui a sa propre maintenance centralisée", "raison": "dit par le client, 2026-10-20"}
  ],
  "taille": {"effectif_min": 20, "effectif_max": 249, "origine": "14 références sur 17 entre 25 et 180 salariés (registre) ; bornes calées sur les tranches du registre (20-49 … 200-249)"},
  "activites": [
    {"nomenclature": "NAF", "codes": ["22.29A", "22.22Z", "25.50A"], "origine": "codes des références, plus 25.50A (dit par le client)"}
  ],
  "personas": [
    {"role": "economique", "intitules": {"fr": ["dirigeant", "directeur général", "directeur de site", "directeur industriel"], "de": ["Geschäftsführer", "Werksleiter"]}, "ce_qui_le_preoccupe": "coût des arrêts, délais de livraison tenus"},
    {"role": "technique", "intitules": {"fr": ["responsable maintenance", "responsable production", "responsable méthodes"], "de": ["Instandhaltungsleiter", "Produktionsleiter"]}, "ce_qui_le_preoccupe": "pannes à répétition, pièces de rechange, planning de maintenance"}
  ],
  "declencheurs": [
    {"signal": "Recrute en maintenance ou en production", "pourquoi": "le parc grandit ou l'équipe manque", "periode_mois": 12},
    {"signal": "Investit dans une presse neuve ou un atelier", "pourquoi": "le client équipe volontiers les machines neuves", "periode_mois": 18}
  ],
  "exclusions": {"clients_actuels": "references.csv", "concurrents": ["Vibracorp"], "secteurs": ["emballage alimentaire jetable"]},
  "zones_habituelles": ["Auvergne-Rhône-Alpes", "Bourgogne-Franche-Comté"],
  "zones": [
    {"nom": "Auvergne-Rhône-Alpes", "type": "region", "code": "84", "origine": "dit par le client, 2026-10-20"},
    {"nom": "Grenoble-Alpes Métropole", "type": "epci", "code": "200040715", "communes": 49, "origine": "proposé, validé par le client le 2026-10-20"}
  ],
  "leads_par_mois": {"valeur": 15, "origine": "dit par le client, 2026-10-20"}
}
```
Chaque chiffre porte son `origine`. Un critère sans `comment_verifier` ne peut pas être prouvé : ne l'écris pas en indispensable.

`zones` : ce que la collecte interroge, avec le code du registre (`type` : `departement`, `region` ou `epci` pour la France ; pour un autre pays, le nom local et, s'il existe, le code de la source). Les `origine` disent « proposé, à valider » tant que le client n'a pas répondu à la question de validation, puis « proposé, validé par le client le <date> ».

## `retours.csv`
Séparateur « , » : `campagne,entreprise,niveau,verdict,raison,date`. `verdict` vaut `bon` ou `mauvais` ; `raison` reprend les mots du client.
