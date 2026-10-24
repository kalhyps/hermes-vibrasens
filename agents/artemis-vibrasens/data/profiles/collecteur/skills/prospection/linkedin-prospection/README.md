# Module LinkedIn pour l'agent Hermes de prospection

LinkedIn devient une source de l'agent sans connexion au compte du client :

1. **Recherche élargie** : pages entreprises LinkedIn trouvées via un moteur de recherche (`site:linkedin.com/company`).
2. **Pré-filtre** : Hermes trie selon l'ICP (industrie, mécanique, taille, pays).
3. **Décideurs publics** : profils trouvés via `site:linkedin.com/in`, à valider.
4. **Validation** : l'orchestrateur envoie au client la liste des décideurs et les liens (`linkedin/a_valider.csv`) ; le client les ouvre avec son propre compte Premium ou Sales Navigator et dit lesquels il retient ; l'orchestrateur les passe à `valide`.
5. **Enrichissement** : Dropcontact trouve l'email pro des contacts validés.

Le détail des commandes est dans `SKILL.md`, que Hermes lit.

## Emplacement chez Artémis Vibrasens

Le skill vit dans le dépôt kalhyps/hermes-vibrasens, à `agents/artemis-vibrasens/data/profiles/collecteur/skills/prospection/linkedin-prospection/`, soit `/opt/data/profiles/collecteur/skills/prospection/linkedin-prospection/` dans le conteneur. Le collecteur le charge quand le brief dit `LinkedIn : oui` et écrit ses fichiers dans le sous-dossier `linkedin/` de la campagne. Les clés Serper et Dropcontact, si Nico les ajoute, vont dans `data/.env` de l'agent sur le serveur (jamais dans git) et atteignent les outils du collecteur par `terminal.env_passthrough`. La mémoire des pages déjà vues est `/workspace/connecteurs/linkedin_seen.json`.

Tests hors ligne (aucune clé, aucun appel réseau) :

```bash
docker exec -w /opt/data/profiles/collecteur/skills/prospection/linkedin-prospection vibrasens-artemis-vibrasens-1 /opt/leadgen/venv/bin/python -m unittest discover -s tests
```

Aucune dépendance Python à installer (bibliothèque standard uniquement).

## Clés et coûts

| Service | Usage | Offre |
| --- | --- | --- |
| Serper.dev | résultats Google | 2 500 requêtes offertes, puis env. 1 $ / 1 000 |
| Brave Search API (alternative) | résultats Brave | offre gratuite limitée |
| Dropcontact | email pro + téléphone | payant au crédit, conforme RGPD, hébergé en France |

Compter environ 3 requêtes par entreprise pour l'étape « décideurs ».

## Ce que le module ne fait pas, volontairement

- Se connecter à LinkedIn ou ouvrir des pages linkedin.com (interdit par les CGU, risque de bannissement du compte du client).
- Envoyer des invitations ou des messages LinkedIn.
- Enrichir des contacts non validés.

## RGPD (prospection B2B)

- Base légale : intérêt légitime. Garder `source` et `date_collecte` pour chaque contact.
- Informer la personne au plus tard au premier contact (article 14). Exemple de mention en pied d'email :

  > Vos coordonnées professionnelles ont été obtenues à partir de sources publiques (moteur de recherche, profil LinkedIn public) et de Dropcontact, pour vous présenter nos services. Vous pouvez vous y opposer ou demander leur suppression en répondant à ce message.

- Proposer le désabonnement dans chaque message et supprimer les contacts sans réponse après 3 ans au plus.
