# Règles de pré-classement (`classement.json`)

Le pré-classement ordonne le vivier pour que les vagues envoient d'abord les entreprises les plus probables. Il ne remplace pas la qualification : il trie avec les seules données de la collecte.

## Structure
```json
{
  "exclure": [ {"si": …, "sauf": …, "raison": "…"} ],
  "points":  [ {"si": …, "points": 30, "raison": "…"} ]
}
```
- `exclure` : la première règle qui s'applique donne la raison d'exclusion. L'entreprise reste dans le vivier, mais n'est jamais envoyée en vague.
- `points` : toutes les règles qui s'appliquent s'additionnent. Les points peuvent être négatifs, pour une donnée défavorable mais pas éliminatoire.
- `si` : une condition, ou une liste de conditions qui doivent toutes être vraies.
- `si_une_de` : une liste dont une seule condition suffit.
- `sauf` (exclusions) : une condition ou une liste ; si l'une est vraie, l'entreprise n'est pas exclue.

## Conditions
Une condition porte sur une colonne du vivier (`references/format.md`) et contient un seul test :

| Test | Exemple | Vrai si |
|---|---|---|
| `egal` | `{"champ": "employeur", "egal": "oui"}` | la valeur est égale (sans accents ni casse) |
| `parmi` | `{"champ": "code_activite", "parmi": ["25.62A", "25.62B"]}` | la valeur, ou l'un de ses codes séparés par `;`, est dans la liste |
| `commence_par` | `{"champ": "code_activite", "commence_par": ["3327", "332"]}` | un code commence ainsi (utile pour les nomenclatures hiérarchiques comme NAICS) |
| `contient` | `{"champ": "nom", "contient": ["decollet", "usinage"]}` | un mot du texte commence ainsi, sans accents ni casse |
| `au_moins` | `{"champ": "effectif_min", "au_moins": 20}` | le nombre est supérieur ou égal |
| `inferieur_a` | `{"champ": "ca", "inferieur_a": 500000}` | le nombre est strictement inférieur |
| `renseigne` | `{"champ": "site_web", "renseigne": true}` | la colonne est remplie (`true`) ou vide (`false`) |

Une colonne vide ne remplit aucun test, sauf `renseigne: false`. Une donnée inconnue ne peut donc jamais exclure une entreprise.

## Comment écrire les règles
1. **Exclusions** : seulement ce qui est certain d'après les données et contraire au brief.
   - Effectif connu sous le minimum du brief, à partir de la borne basse : `effectif_min` `inferieur_a` le minimum.
   - Entreprise sans salarié (`employeur` `egal` `non`) quand le brief vise des entreprises avec des salariés.
   - Forme juridique incompatible (entrepreneur individuel pour une offre industrielle, par exemple).
   - Activité clairement étrangère au brief, surtout dans les catégories des bases de lieux (commerces, revendeurs, loueurs, restaurants), avec un `sauf` si le nom ou le code officiel disent le contraire.
   - Exclusions explicites du brief (clients, concurrents, secteurs).
2. **Points** : un poids pour chaque critère du brief mesurable dans le vivier, en proportion de son importance dans le brief.
   - Activité : le code exact vaut plus qu'un code voisin, et un mot du métier dans le nom compte aussi.
   - Taille : l'effectif quand il est connu. Quand il manque, ses indices : `employeur` `oui`, chiffre d'affaires, `indices` (par exemple « index Egapro » en France, publié à partir de 50 salariés, ou comptes de grande taille).
   - Ancrage local : établissement dans la zone, siège ou non selon ce que le brief préfère.
   - Fiabilité : `nb_sources` au moins 2.
   - Faisabilité de l'enrichissement : `site_web` ou `telephone` renseignés.
3. **Contrôle** : lance `rank_pool.py --apercu 20`, puis relis la tête, la frontière de la vague et l'échantillon d'exclues. Une bonne entreprise mal placée ou exclue signale une règle à corriger, pas une exception à faire à la main.

## Exemple : décolletage en Haute-Savoie, entreprises de 10 salariés ou plus, chiffre d'affaires d'au moins 2 M€ apprécié
```json
{
  "exclure": [
    {"si": {"champ": "effectif_min", "inferieur_a": 10}, "raison": "moins de 10 salariés selon le registre"},
    {"si": {"champ": "employeur", "egal": "non"}, "raison": "aucun salarié selon le registre"},
    {"si": {"champ": "forme_juridique", "egal": "entrepreneur individuel"}, "raison": "entrepreneur individuel"},
    {"si_une_de": [{"champ": "libelle_activite", "parmi": ["supplier_or_distributor", "home_service"]},
                   {"champ": "code_activite", "parmi": ["jewelry_and_watches_manufacturer", "textile_manufacturer"]}],
     "sauf": [{"champ": "nom", "contient": ["decollet", "usinage", "mecanique"]},
              {"champ": "sources_collecte", "contient": "registre"}],
     "raison": "activité hors cible d'après la catégorie Overture"}
  ],
  "points": [
    {"si": {"champ": "code_activite", "parmi": ["25.62A", "25.62B"]}, "points": 40, "raison": "décolletage ou usinage (code exact)"},
    {"si": {"champ": "nom", "contient": ["decollet", "usinage"]}, "points": 25, "raison": "métier dans le nom"},
    {"si": {"champ": "code_activite", "parmi": ["machine_shop", "metal_fabricator"]}, "points": 15, "raison": "atelier d'usinage (Overture)"},
    {"si": {"champ": "effectif_min", "au_moins": 10}, "points": 20, "raison": "10 salariés ou plus"},
    {"si": [{"champ": "effectif_min", "renseigne": false}, {"champ": "employeur", "egal": "oui"}], "points": 10, "raison": "employeur, effectif inconnu"},
    {"si": {"champ": "indices", "contient": "egapro"}, "points": 15, "raison": "50 salariés ou plus (index Egapro)"},
    {"si": {"champ": "ca", "au_moins": 2000000}, "points": 15, "raison": "chiffre d'affaires d'au moins 2 M€"},
    {"si": {"champ": "nb_sources", "au_moins": 2}, "points": 5, "raison": "présente dans plusieurs sources"},
    {"si": {"champ": "site_web", "renseigne": true}, "points": 5, "raison": "site connu"}
  ]
}
```
Les nombres de cet exemple viennent de ce brief-là. Pour une autre campagne, repars du brief.

## Exemple : ateliers d'usinage aux États-Unis (NAICS 332710), 20 salariés ou plus
```json
{
  "exclure": [
    {"si": {"champ": "effectif_min", "inferieur_a": 20}, "raison": "moins de 20 salariés selon la source"},
    {"si": {"champ": "libelle_activite", "parmi": ["retail", "eat_and_drink"]},
     "sauf": {"champ": "nom", "contient": ["machin", "precision", "manufactur"]}, "raison": "commerce ou restauration"}
  ],
  "points": [
    {"si": {"champ": "code_activite", "commence_par": "332710"}, "points": 40, "raison": "machine shop (NAICS exact)"},
    {"si": {"champ": "code_activite", "commence_par": ["3327", "3329"]}, "points": 20, "raison": "usinage ou pièces métalliques (NAICS voisin)"},
    {"si": {"champ": "code_activite", "parmi": ["machine_shop"]}, "points": 20, "raison": "machine shop (Overture)"},
    {"si": {"champ": "nom", "contient": ["machin", "precision", "cnc", "screw"]}, "points": 15, "raison": "métier dans le nom"},
    {"si": {"champ": "effectif_min", "au_moins": 20}, "points": 20, "raison": "20 salariés ou plus"},
    {"si": {"champ": "nb_sources", "au_moins": 2}, "points": 10, "raison": "présente dans plusieurs sources"},
    {"si": {"champ": "site_web", "renseigne": true}, "points": 5, "raison": "site connu"}
  ]
}
```
