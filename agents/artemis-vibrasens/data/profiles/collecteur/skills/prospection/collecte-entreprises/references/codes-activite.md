# Codes d'activité : traduire une cible dans la nomenclature d'un pays

Une activité se décrit d'abord en mots : le métier, les produits, les clients. Elle se traduit ensuite dans la nomenclature de chaque source. Les sources d'un même pays n'utilisent pas toujours la même ; une base de lieux a ses propres catégories.

## Les grandes familles
| Nomenclature | Où | Forme | Lien avec les autres |
|---|---|---|---|
| CITI / ISIC (ONU) | référence mondiale | 4 chiffres (rév. 4 ; rév. 5 adoptée en 2023) | NACE en dérive |
| NACE (UE) | Union européenne et pays associés | `25.62` (rév. 2 ; rév. 2.1 appliquée depuis 2025) | identique à la CITI jusqu'à 4 chiffres, ou presque |
| NAF (France) | registre français | `25.62A` : NACE + une lettre | NAF 2025 (basée sur NACE rév. 2.1) en cours de déploiement : l'API donne aussi `activite_principale_naf25` |
| WZ (Allemagne), ATECO (Italie), CNAE (Espagne), NACEBEL (Belgique), SBI (Pays-Bas)… | registres européens | NACE + 1 ou 2 chiffres | partent de la NACE à 4 chiffres |
| UK SIC 2007 (Royaume-Uni) | Companies House | `25620` : NACE + un chiffre | NACE 25.62 → SIC 25620 |
| NAICS | États-Unis, Canada, Mexique | 6 chiffres, hiérarchique (`332710`) | correspondance officielle NAICS ↔ CITI publiée par le Census Bureau |
| SIC (États-Unis, ancien) | encore utilisé par la SEC (EDGAR) et des bases privées | 4 chiffres (`3599`) | ≠ UK SIC ; table NAICS ↔ SIC publiée |
| ANZSIC | Australie, Nouvelle-Zélande | lettre + 4 chiffres | |
| JSIC, KSIC, GB/T 4754… | Japon, Corée, Chine… | | souvent dérivées de la CITI |
| Catégories Overture | base de lieux mondiale | `machine_shop`, `metal_fabricator` | sans lien avec les codes officiels : à lister zone par zone (`--lister-categories`) |

## Méthode
1. **Décris la cible en mots**, dans la langue du pays et en anglais : le métier (« décolletage », « screw machining », « Drehteile »), les produits, les procédés.
2. **Trouve le code de référence** : NACE ou CITI à 4 chiffres. Cherche la nomenclature officielle du pays : sa note explicative, son moteur de recherche par mot-clé, ses tables de correspondance. Tu n'as pas besoin de connaître les codes par cœur.
3. **Descends dans la nomenclature du pays** : le code exact (par exemple NAICS 332721, « Precision Turned Product Manufacturing ») et ses voisins.
4. **Prends large** : les entreprises se déclarent souvent sous un code voisin, celui de leur premier métier ou de leurs clients. Exemples :
   - un décolleteur peut être déclaré sous 25.62 (usinage), mais aussi 25.94 (vis et boulons) ou 33.11 (réparation d'ouvrages en métaux) ;
   - la maintenance industrielle se trouve sous 33.12 (réparation de machines) et 33.20 (installation de machines).
   Collecte avec le code exact et les codes voisins plausibles ; c'est le classement qui donnera plus de points au code exact.
5. **Complète par les mots du nom** (`--mots`, quand le connecteur le permet) : c'est la meilleure parade contre les erreurs de code.
6. **Vérifie** : un échantillon par code. Un code voisin qui n'apporte que du hors cible se retire, et tu notes pourquoi dans `fiche-sources.md`.

## Pièges
- Les mêmes chiffres n'ont pas le même sens d'une nomenclature à l'autre : le SIC américain 3599 désigne des machines industrielles, alors qu'au Royaume-Uni la division 35 du SIC est l'énergie.
- Changements de version : NACE rév. 2 → rév. 2.1, NAICS 2017 → 2022. Une source peut encore utiliser l'ancienne version : vérifie-la dans sa documentation.
- Le code d'un registre est celui de l'activité principale déclarée, parfois ancienne : un code ne prouve pas l'activité, il la rend probable. C'est l'enrichissement qui confirme.
