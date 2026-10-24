# Agents Hermes de Vibrasens

Les agents de Vibrasens, l'entreprise du père de Nicolas, hébergés sur le serveur de Kalhyps à côté des agents de Nicolas (dépôt `kalhyps/hermes`), mais à part : leur propre dépôt, leurs propres dossiers, leurs propres clés. `agents/<agent>/` est la version de départ de chaque agent : sur le serveur, elle est fusionnée dans son dossier de travail `/srv/hermes/clients/vibrasens/agents/<agent>/`, dont `data/` devient `/opt/data` et `workspace/` devient `/workspace` dans son conteneur.

## Agents

| Agent | Rôle | Messagerie |
|---|---|---|
| **artemis-vibrasens** | Prospection B2B, copie d'Artémis (l'agent de prospection de Nicolas) au 24/10/2026 : dossier client, brief validé, puis une équipe (collecteur, enrichisseur, qualificateur) qui livre des leads avec leurs preuves ; aucune prise de contact | Google Chat du dirigeant (lui seul) |

## Sur le serveur

```
/srv/hermes/clients/vibrasens/
  depot/                      ce dépôt (branche main), lu par le compte hermes avec une clé de déploiement en lecture seule
  agents/<agent>/             dossier de travail de chaque agent (data/, workspace/), historique git local enregistré chaque nuit
```

- Installation (une fois, Nico) : `ops/clients/ajouter-client.sh` du dépôt `kalhyps/hermes` (`cle vibrasens`, puis `installer vibrasens kalhyps/hermes-vibrasens`).
- Déployer : `sudo -u hermes hermes-ctl deployer-client vibrasens` (Nico), ou `ssh hermes-claude deployer-client vibrasens` (Claude). L'image `hermes-kalhyps:v0.22.2` vient de `kalhyps/hermes` : rien n'est construit ici.
- Les autres commandes du menu (`redemarrer`, `journal`, `journal-profil`, `sante`, `lire`, `demander`, `connecter-codex`…) prennent le nom de l'agent, par exemple `redemarrer artemis-vibrasens`.
- Réglages et clés : `ops/installer.sh` (Nico, avec sudo) : Google Chat, clés au nom de Vibrasens, vérification qu'il ne reste aucune clé de Nicolas. Jamais de clé dans ce dépôt (hook `.githooks/pre-commit` : `git config core.hooksPath .githooks`).
- Modèle : abonnement ChatGPT de Nicolas (connexion Codex), choix de Nicolas le 24/10/2026.

## Google Chat (artemis-vibrasens)

Projet Google Cloud `vibrasens-artemis` (organisation vibrasens.com) : API Google Chat et Pub/Sub ; sujet `vibrasens-chat` (Google Chat y publie : `chat-api-push@system.gserviceaccount.com` Pub/Sub Publisher) ; abonnement pull `vibrasens-chat-sub` (compte de service de l'agent : Subscriber et Viewer) ; application Chat connectée en Cloud Pub/Sub, visible du seul dirigeant. Aucun port ouvert sur le serveur. Les pièces jointes (`leads.xlsx`) demandent en plus un client OAuth « Application de bureau » (écran de consentement Interne), posé par `installer.sh google-chat … <client.json>`, puis `/setup-files start` une fois dans la conversation.

## Clés d'artemis-vibrasens

Toutes facultatives ; un service sans clé est sauté. Posées par `sudo bash ops/installer.sh cle <NOM>` dans les quatre profils.

| Clé | Service | Coût |
|---|---|---|
| `TINYFISH_API_KEY`, `KEENABLE_API_KEY` | recherche web | gratuits avec un compte, conseillés |
| `PERPLEXITY_API_KEY`, `FIRECRAWL_API_KEY` | recherche et lecture de pages de dernier recours | payants, plafonnés par jour |
| `LEADGEN_FIRECRAWL_API_KEY` | lecture des pages difficiles par les outils de collecte (peut être la même clé Firecrawl) | payant |
| `COMPANIES_HOUSE_API_KEY` | registre britannique | gratuit |
| `SERPER_API_KEY`, `DROPCONTACT_API_KEY` | décideurs LinkedIn publics, puis emails des contacts validés | payants, seulement avec l'accord du dirigeant |
