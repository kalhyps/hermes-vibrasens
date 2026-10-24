"""Vérifie l'accès aux sources de données, sans rien dépenser : clés présentes et acceptées, services joignables.

À lancer après avoir ajouté ou changé une clé dans .env (puis docker compose up -d) :
  docker exec vibrasens-artemis-vibrasens-1 /opt/leadgen/venv/bin/python /opt/data/leadgen/outils/check_sources.py
Les clés ne sont jamais affichées.
"""
import os
import subprocess

import re

import httpx


def check(label, test):
    try:
        print(f"{label:28} {test()}")
    except Exception as exc:  # chaque source est testée indépendamment des autres
        print(f"{label:28} ERREUR : {str(exc)[:160]}")


def registry_fr():
    response = httpx.get("https://recherche-entreprises.api.gouv.fr/search", params={"q": "renault", "per_page": 1},
                         timeout=20)
    response.raise_for_status()
    return f"OK ({response.json().get('total_results', 0)} résultats pour un essai)"


def firecrawl():
    key = os.environ.get("LEADGEN_FIRECRAWL_API_KEY") or os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        return "clé absente : recherche par les moteurs gratuits, pas de lecture des pages en JavaScript"
    from firecrawl.v2.client import FirecrawlClient
    usage = FirecrawlClient(api_key=key, timeout=30).get_credit_usage()
    plan = f" sur {usage.plan_credits}" if usage.plan_credits else ""
    # Dans les tâches Kanban, les outils ne reçoivent que LEADGEN_FIRECRAWL_API_KEY (voir common.firecrawl_client),
    # et seulement si le profil la laisse passer (terminal.env_passthrough).
    try:
        allowed = subprocess.run(["hermes", "-p", "enrichisseur", "config", "get", "terminal.env_passthrough"],
                                 capture_output=True, text=True).stdout
    except FileNotFoundError:  # lancé par un agent : la clé n'est là que si elle lui est bien transmise
        allowed = "LEADGEN_FIRECRAWL_API_KEY"
    if not os.environ.get("LEADGEN_FIRECRAWL_API_KEY"):
        agents = "PAS transmise aux outils des agents (LEADGEN_FIRECRAWL_API_KEY absente)"
    elif "LEADGEN_FIRECRAWL_API_KEY" not in allowed:
        agents = "PAS transmise aux outils des agents (absente de terminal.env_passthrough)"
    else:
        agents = "transmise aux outils des agents"
    return f"OK, clé acceptée : {usage.remaining_credits}{plan} crédits restants ; {agents}"


def companies_house():
    key = os.environ.get("COMPANIES_HOUSE_API_KEY")
    if not key:
        return "clé absente : le Royaume-Uni passe par Overture"
    response = httpx.get("https://api.company-information.service.gov.uk/company/00000006", auth=(key, ""), timeout=20)
    if response.status_code == 401:
        return "ERREUR : clé refusée par l'API (vérifier .env)"
    response.raise_for_status()
    return "OK, clé acceptée"


def overture():
    listing = httpx.get("https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/",
                        timeout=20)
    listing.raise_for_status()
    releases = re.findall(r"<Prefix>release/([^/<]+)/</Prefix>", listing.text)
    return f"OK (dernière publication : {max(releases)})" if releases else "ERREUR : aucune publication listée"


ROUTEUR_SERVICES = [
    # service, clé (vide = sans clé), hôte joint par le routeur (plugins/web/routeur)
    ("Exa gratuit", "", "https://mcp.exa.ai"),
    ("Parallel gratuit", "", "https://search.parallel.ai"),
    ("TinyFish", "TINYFISH_API_KEY", "https://api.search.tinyfish.ai"),
    ("Keenable", "KEENABLE_API_KEY", "https://api.keenable.ai"),
    ("Perplexity rapide", "PERPLEXITY_API_KEY", "https://api.perplexity.ai"),
    ("Firecrawl (lecture)", "FIRECRAWL_API_KEY", "https://api.firecrawl.dev"),
]


def web_router():
    """Routeur web des agents (web_search, web_extract) : clés présentes et hôtes joignables, sans appel facturé."""
    lines = []
    for name, var, host in ROUTEUR_SERVICES:
        if var and not os.environ.get(var):
            lines.append(f"{name} : clé absente ({var}), service sauté")
            continue
        try:
            # Une réponse HTTP, quel qu'en soit le code, suffit : aucun appel au service, donc rien de dépensé.
            httpx.get(host, timeout=10, follow_redirects=True)
            reach = "joignable"
        except httpx.HTTPError as exc:
            reach = f"INJOIGNABLE ({type(exc).__name__})"
        lines.append(f"{name} : {'clé présente, ' if var else 'sans clé, '}{reach}")
    return "\n" + "\n".join(f"{'':28} {line}" for line in lines)


def free_search():
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException
    try:
        # Mêmes réglages que find_website.py : 10 résultats, donc plusieurs moteurs interrogés à la fois.
        results = DDGS().text("Transports Gelin Fougères", region="fr-fr", max_results=10) or []
    except DDGSException:
        results = []
    return f"OK ({len(results)} résultats)" if results else "aucun résultat : moteurs saturés pour l'instant (Firecrawl prend le relais)"


if __name__ == "__main__":
    check("Registre français", registry_fr)
    check("Firecrawl", firecrawl)
    check("Companies House", companies_house)
    check("Overture Maps", overture)
    check("Moteurs gratuits (ddgs)", free_search)
    check("Routeur web des agents", web_router)
