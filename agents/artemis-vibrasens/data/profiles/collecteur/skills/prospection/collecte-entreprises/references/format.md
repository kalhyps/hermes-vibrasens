# Format commun d'une entreprise

Toutes les sources (`sources/*.csv`), le vivier (`vivier.csv`) et `1-collecte.csv` ont ces colonnes. Seules `id`, `nom`, `pays` et `source` sont obligatoires. Une donnée inconnue reste **vide** : n'écris jamais « inconnu », « N/A » ou 0 à la place.

## Identité
| Colonne | Contenu |
|---|---|
| `id` | identifiant unique dans la campagne, utilisable dans un nom de fichier. Le numéro officiel s'il existe ; sinon celui de la source (Overture), ou une empreinte du nom, de la ville et du pays (`make_id`). Il ne change plus une fois l'entreprise envoyée en vague. |
| `id_registre`, `registre` | numéro officiel et nom du registre : `siren`, `companies_house`, `kbo`, `ein`… Deux numéros différents d'un même registre ne sont jamais fusionnés. |
| `nom` | raison sociale ou nom d'usage, tel que la source le donne |
| `pays` | code ISO à deux lettres (FR, GB, US, DE…) |
| `forme_juridique` | SAS, GmbH, LLC, entrepreneur individuel… (ou le code de la source) |
| `date_creation` | AAAA-MM-JJ |

## Lieu
| Colonne | Contenu |
|---|---|
| `region` | subdivision administrative : département, État, province, comté |
| `ville`, `code_postal`, `adresse` | du site retenu dans la zone (établissement, sinon siège) |
| `latitude`, `longitude` | position, quand la source la donne |
| `siege` | `oui` si le site retenu est le siège, `non` si c'est un autre établissement |
| `id_etablissement`, `effectif_etablissement`, `source_etablissement` | identifiant, effectif et fiche de l'établissement retenu (France : SIRET). Un établissement actif de la zone, avec le code d'activité de la cible et des salariés, prouve un site local. |
| `nb_etablissements` | nombre d'établissements ouverts |

## Activité et taille
| Colonne | Contenu |
|---|---|
| `code_activite` | code(s) d'activité de la source ; plusieurs séparés par `;` |
| `nomenclature` | NAF rév. 2, SIC 2007 (Royaume-Uni), NAICS, NACE, catégories Overture… |
| `libelle_activite` | libellé de l'activité, ou catégorie générale de la source |
| `effectif` | effectif ou tranche, en clair (« 20 à 49 ») |
| `effectif_min` | borne basse de l'effectif, en nombre : c'est elle que les règles de classement comparent |
| `employeur` | `oui` ou `non` si la source dit si l'entreprise emploie des salariés |
| `categorie` | catégorie de la source (PME, ETI ; type de société ; catégorie générale d'Overture) |
| `ca`, `annee_ca` | chiffre d'affaires (monnaie du pays) et son année |
| `indices` | autres indices utiles, séparés par ` ; ` : déclarations obligatoires au-delà d'une taille, comptes déposés, établissements… |

## Contact
| Colonne | Contenu |
|---|---|
| `site_web`, `telephone`, `email` | ceux de l'entreprise, jamais ceux d'une personne |

## Traçabilité
| Colonne | Contenu |
|---|---|
| `source` | adresse de la fiche de l'entreprise dans la source ; à défaut, une référence précise (`overture:<version>:<id>`, page et rang d'une liste) |
| `source_nom` | nom de la source (le fichier `sources/<source_nom>.csv`) |
| `meme_que` | identifiant d'une ligne d'une autre source qui désigne la même entreprise (rapprochement) |
| `collecte_le` | date de la collecte |

## Colonnes ajoutées au vivier
| Colonne | Écrite par | Contenu |
|---|---|---|
| `sources_collecte`, `nb_sources` | `merge_sources.py` | sources où l'entreprise a été trouvée, et leur nombre |
| `autres_sources` | `merge_sources.py` | références des autres sources |
| `exclusion` | `rank_pool.py` | raison de l'exclusion ; vide si l'entreprise est candidate |
| `score_collecte`, `raisons_collecte` | `rank_pool.py` | points du pré-classement et leur détail |
| `priorite` | `rank_pool.py` | rang parmi les candidates (1 = à analyser en premier) |
| `vague` | `waves.py` | numéro de la vague où l'entreprise a été envoyée ; vide si elle est en réserve |
