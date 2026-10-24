"""Recherche web gratuite des outils (find_website.py) : les deux services gratuits et sans clé de
l'aiguilleur des agents (plugins/web/routeur), Exa puis Parallel, puis les moteurs de ddgs.

Les moteurs de ddgs limitent l'adresse du serveur pendant des heures après un usage intensif (vu le 05/10) ;
Exa et Parallel prennent le relais. Un service qui refuse (saturation, panne) est laissé de côté une minute,
pour ne pas le solliciter à chaque entreprise.

search(query, region, max_results) -> [{"href", "title", "body"}], ou None si tous les services ont refusé.
"""
import json
import re
import threading
import time
import uuid

import httpx
from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

TIMEOUT_S = 20
COOLDOWN_S = 60      # pause d'un service après un refus
EXA_PAUSE_S = 3.0    # le refus d'Exa ne dure que quelques secondes : on réessaie une fois
SESSION_ID = uuid.uuid4().hex

_resting_until = {}  # service -> heure (time.monotonic) avant laquelle on ne l'interroge plus
_lock = threading.Lock()


class Refused(Exception):
    """Le service n'a pas pu répondre : saturation, erreur, réponse illisible."""


def _mcp(url, tool, args):
    """Appel d'un outil d'un serveur MCP gratuit, sans session (comme l'aiguilleur)."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    try:
        resp = httpx.post(url, json=body, timeout=TIMEOUT_S, headers={
            "Accept": "application/json, text/event-stream", "User-Agent": "kalhyps-artemis/0.1"})
    except httpx.HTTPError as exc:
        raise Refused(str(exc))
    if resp.status_code >= 400:
        raise Refused(f"HTTP {resp.status_code}")
    text = resp.content.decode("utf-8", "replace")
    candidates = [text.strip()] if text.strip().startswith("{") else []
    candidates += [line[6:] for line in re.split(r"\r\n|\r|\n", text) if line.startswith("data: ")]
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if data.get("error"):
            raise Refused(str(data["error"])[:200])
        result = data.get("result") or {}
        texts = [x.get("text", "") for x in result.get("content") or [] if isinstance(x, dict)]
        if result.get("isError"):
            raise Refused(" ".join(texts)[:200])
        return next((t for t in texts if t), "")
    raise Refused("réponse MCP illisible")


def exa_rows(text, n):
    """Le MCP gratuit d'Exa rend du texte : blocs « Title: / URL: / Highlights: » séparés par « --- »."""
    rows = []
    for block in text.split("\n---\n"):
        title = url = ""
        highlights, in_highlights = [], False
        for line in (raw.strip() for raw in block.splitlines()):
            if line.startswith("Title:"):
                title = line[6:].strip()
            elif line.startswith("URL:"):
                url = line[4:].strip()
            elif in_highlights and line and not line.startswith(("Author:", "Published:")):
                highlights.append(line)
            if line.startswith(("Title:", "URL:", "Highlights:", "Published:", "Author:")):
                in_highlights = line.startswith("Highlights:")
        if url:
            rows.append({"href": url, "title": title, "body": " ".join(highlights)[:600]})
        if len(rows) >= n:
            break
    return rows


def exa(query, region, n):
    for attempt in range(2):
        text = _mcp("https://mcp.exa.ai/mcp", "web_search_exa", {"query": query, "numResults": n})
        if "free MCP rate limit" not in text[:300]:
            return exa_rows(text, n)
        if attempt == 0:
            time.sleep(EXA_PAUSE_S)
    raise Refused("Exa saturé")


def parallel(query, region, n):
    text = _mcp("https://search.parallel.ai/mcp", "web_search",
                {"objective": query, "search_queries": [query], "session_id": SESSION_ID})
    try:
        results = json.loads(text).get("results") or []
    except (ValueError, AttributeError):
        raise Refused("Parallel : réponse illisible")
    return [{"href": r.get("url") or "", "title": r.get("title") or "", "body": " ".join(r.get("excerpts") or [])}
            for r in results[:n]]


def ddgs(query, region, n):
    try:
        return DDGS().text(query, region=region, safesearch="off", max_results=n) or []
    except (RatelimitException, TimeoutException):
        raise Refused("moteurs ddgs saturés")
    except DDGSException:
        return []  # « aucun résultat » : la recherche a bien eu lieu


SERVICES = [("exa", exa), ("parallel", parallel), ("ddgs", ddgs)]


def search(query, region="wt-wt", max_results=10):
    """Le premier service qui répond. Une liste vide d'un service qui a répondu laisse essayer le suivant ;
    None seulement si aucun service n'a pu répondre."""
    answered = False
    for name, service in SERVICES:
        with _lock:
            if _resting_until.get(name, 0) > time.monotonic():
                continue
        try:
            results = service(query, region, max_results)
        except Refused:
            with _lock:
                _resting_until[name] = time.monotonic() + COOLDOWN_S
            continue
        answered = True
        if results:
            return results
    return [] if answered else None
