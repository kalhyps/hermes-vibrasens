# Banc d'essai web

Compare les services de recherche et de lecture de pages sur des questions de prospection (registres, annuaires, fédérations, sites d'entreprises), depuis le serveur. Son résultat fixe l'ordre des services du routeur web (`plugins/web/routeur`, réglable par `plugins.entries."web/routeur".settings`). Repris du banc d'Axiom (`kalhyps/hermes`, `agents/axiom/workspace/axiom/banc-web/`).

- `bench_web.py` : le banc (Python standard, aucun paquet à installer). Appels en série, espacés, plafonnés en nombre et en dollars ; `--dry-run` montre le plan sans rien appeler.
- `bench_cases.json` : 20 recherches et 13 pages, avec les domaines et les phrases attendus. À compléter avec les recherches réelles des campagnes (sites de PME d'une zone, annuaires d'un pays, fédérations d'un secteur) : c'est sur elles que l'ordre compte.
- Résultats : un dossier par exécution (`results.csv`, une ligne par appel ; `summary.md`, le résumé), à mettre dans `tmp/` ou dans `/workspace/banc-web/`, hors Git.

## Lancer

Les clés sont celles du conteneur (variables d'environnement de `docker-compose.yml`, jamais affichées). Le banc dure une dizaine de minutes : le lancer en dehors d'une campagne, depuis le VPS.

```
docker exec vibrasens-artemis-vibrasens-1 python3 /opt/data/leadgen/outils/banc-web/bench_web.py --dry-run
docker exec vibrasens-artemis-vibrasens-1 python3 /opt/data/leadgen/outils/banc-web/bench_web.py --allow free --out /workspace/banc-web/$(date +%Y%m%d-%H%M)
```

- `--allow free` (défaut) : services gratuits seulement, 0 $.
- `--allow free,credit,paid --max-usd 0.40` : ajoute les crédits offerts (Exa, Firecrawl…) et les payants (Perplexity) ; environ 0,25 $ pour le jeu d'Axiom.
- `--resummarize <dossier>/results.csv` : refaire le résumé sans rappeler personne.

Changer ensuite l'ordre dans `plugins.entries."web/routeur".settings` du profil si un autre service fait mieux sur les questions de prospection.
