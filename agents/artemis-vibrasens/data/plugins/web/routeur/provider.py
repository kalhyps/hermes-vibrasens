"""Routeur web : une chaîne de fournisseurs derrière web_search / web_extract, gratuits d’abord, payant en dernier.

Sélection : web.search_backend: routeur, web.extract_backend: routeur ; plugin activé par
plugins.enabled: [web/routeur]. Réglages sous plugins.entries."web/routeur".settings (voir DEFAULTS).
Seule surface Hermes utilisée : l'ABC documentée WebSearchProvider, get_provider_env et ctx
(get_config, state). Aucun import de plugins.web._common ni de tools.* (internes, non garantis).

Contrat Hermes (v0.22.2) :
- search(query, limit) est synchrone ; Hermes demande une taille « seau » (10 pour limit=5) et coupe ensuite.
  Réponse {"success": True, "data": {"web": [{url,title,description,position}], ...}} ; les réponses
  réussies sont mises en cache 20 min sous le nom « routeur ».
- extract(urls, **kw) renvoie une liste de documents {url,title,content,raw_content,metadata[,error]},
  un par URL, dans l'ordre ; Hermes l'appelle dans un thread, coupe à web.extract_timeout (120 s) et
  ne montre au modèle que url/title/content/error.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

import httpx

from agent.web_search_provider import WebSearchProvider, get_provider_env

logger = logging.getLogger("hermes_plugins.web_routeur")

DEFAULTS: dict[str, Any] = {
    # Ordre = priorité. Noms : keenable, keenable_public, tinyfish, parallel_free, exa_free, perplexity_fast,
    # firecrawl. Un fournisseur sans clé est sauté.
    # Ordre tiré du banc d'essai du 19/10/2026 (32 recherches, 21 pages d'Axiom) : bonne source dans le
    # top 3 dans 94 % des recherches et 90 % des pages lues, contre 62 % et 71 % en commençant par Keenable.
    "search_chain": ["exa_free", "parallel_free", "tinyfish", "perplexity_fast"],
    "extract_chain": ["exa_free", "parallel_free", "keenable", "firecrawl"],
    # Appels par jour (heure de Paris) au-delà desquels le fournisseur est sauté. Absent = illimité.
    "daily_budget": {"perplexity_fast.search": 60, "firecrawl.extract": 25,
                     "tinyfish.search": 3000, "tinyfish.extract": 400, "keenable.search": 3000, "keenable.extract": 1000},
    "min_overlap": 0.5,          # part des mots de la requête présents dans le top 3 (titre, extrait, URL)
    "min_chars": 300,            # page lue utilisable à partir de ce nombre de caractères
    "timeout_s": 20,
    "cooldown_s": {"rate_limit": 120, "server": 30, "transport": 30},
    "deny": {"firecrawl": ["x.com", "twitter.com"]},   # x.com coûte 30 crédits Firecrawl la page
    # Adresses lues d'abord par simple téléchargement : un flux RSS doit être frais. Le 19/10, Exa n'a rien
    # rendu pour un flux de chaîne YouTube et Parallel l'a rendu tronqué (14 vidéos, la plus récente du 27/08).
    "direct_first": ["youtube.com/feeds/"],
}
COST_USD = {"perplexity_fast.search": 0.001, "parallel.search": 0.001, "parallel.extract": 0.001}
SESSION_ID = uuid.uuid4().hex
STOP = set("le la les un une des du de d l au aux et ou en dans sur pour par avec sans ce cet cette ces qui que "
           "est sont a comment quel quelle quels quelles plus tout tous pas ne se the of to in on for and or with".split())
BLOCK_MARKERS = ("just a moment", "attention required", "cf-chl", "checking your browser",
                 "enable javascript and cookies", "captcha-delivery", "datadome", "access denied", "acces refuse")


class VendorError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # rate_limit | server | client | transport | payload


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _terms(q: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", _fold(re.sub(r"\bsite:\S+", " ", q)))
    return [t for t in dict.fromkeys(toks) if t not in STOP and (len(t) >= 3 or t.isdigit())]


def overlap(q: str, rows: list[dict], k: int = 3) -> float:
    terms = _terms(q)
    if not terms:
        return 1.0
    hay = _fold(" ".join(f"{r.get('title','')} {r.get('description','')} {r.get('url','')}" for r in rows[:k]))
    return sum(t in hay for t in terms) / len(terms)


def blocked(text: str, title: str = "") -> bool:
    head = _fold(f"{title} {text[:4000]}")
    return len(text) < 6000 and any(m in head for m in BLOCK_MARKERS)


def _row(url, title, desc, pos):
    return {"url": url or "", "title": title or "", "description": (desc or "")[:500], "position": pos}


def _doc(url, title, content, served_by):
    return {"url": url, "title": title or "", "content": content, "raw_content": content,
            "metadata": {"sourceURL": url, "title": title or "", "served_by": served_by}}


# ─── HTTP ─────────────────────────────────────────────────────────────────────

def _request(method: str, url: str, timeout: float, **kw) -> httpx.Response:
    try:
        resp = httpx.request(method, url, timeout=timeout, headers={"User-Agent": "kalhyps-routeur/0.1", **kw.pop("headers", {})}, **kw)
    except httpx.TimeoutException as exc:
        raise VendorError("transport", f"timeout: {exc}")
    except httpx.HTTPError as exc:
        raise VendorError("transport", str(exc))
    if resp.status_code == 429:
        raise VendorError("rate_limit", f"HTTP 429: {resp.text[:200]}")
    if resp.status_code >= 500:
        raise VendorError("server", f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        kind = "rate_limit" if resp.status_code == 402 else "client"  # 402 = crédit épuisé : comme un quota
        raise VendorError(kind, f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp


def _json(method: str, url: str, timeout: float, **kw) -> Any:
    resp = _request(method, url, timeout, **kw)
    try:
        return resp.json()
    except ValueError:
        raise VendorError("payload", "réponse non JSON")


def _mcp(url: str, tool: str, args: dict, timeout: float) -> str:
    """tools/call JSON-RPC sans session : le MCP gratuit de Parallel l'accepte (Hermes fait de même)."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    resp = _request("POST", url, timeout, json=body, headers={"Accept": "application/json, text/event-stream"})
    text = resp.content.decode("utf-8", "replace")
    cands = [text.strip()] if text.strip().startswith("{") else []
    cands += [ln[6:] for ln in re.split(r"\r\n|\r|\n", text) if ln.startswith("data: ")]
    for c in cands:
        try:
            data = json.loads(c)
        except ValueError:
            continue
        if data.get("error"):
            msg = str(data["error"])
            raise VendorError("rate_limit" if "rate" in msg.lower() or "429" in msg else "payload", msg[:200])
        result = data.get("result") or {}
        texts = [x.get("text", "") for x in result.get("content") or [] if isinstance(x, dict)]
        if result.get("isError"):
            msg = " ".join(texts)
            raise VendorError("rate_limit" if "rate" in msg.lower() else "payload", msg[:200])
        return next((t for t in texts if t), "")
    raise VendorError("payload", "réponse MCP illisible")


# ─── Fournisseurs ─────────────────────────────────────────────────────────────
# search(q, n, timeout) -> [rows] ; fetch(url, timeout) -> (title, text, final_url)

def _keenable_headers(key: str) -> dict:
    return {"X-Keenable-Title": "kalhyps", **({"Authorization": f"Bearer {key}"} if key else {})}


def keenable_search(q, n, t, public=False):
    key = "" if public else get_provider_env("KEENABLE_API_KEY")
    path = "/v1/search/public" if public else "/v1/search"
    d = _json("POST", f"https://api.keenable.ai{path}", t, json={"query": q, "max_results": min(n, 20)}, headers=_keenable_headers(key))
    return [_row(r.get("url"), r.get("title"), r.get("snippet") or r.get("description"), i + 1) for i, r in enumerate(d.get("results") or [])]


def keenable_fetch(url, t, public=False):
    key = "" if public else get_provider_env("KEENABLE_API_KEY")
    path = "/v1/fetch/public" if public else "/v1/fetch"
    d = _json("GET", f"https://api.keenable.ai{path}", t, params={"url": url}, headers=_keenable_headers(key))
    return d.get("title") or "", d.get("content") or "", d.get("url") or url


def tinyfish_search(q, n, t):
    params = {"query": q}
    m = re.search(r"\bsite:(\S+)", q)
    if m:  # TinyFish filtre par domaine via un paramètre plutôt que par l'opérateur
        params = {"query": re.sub(r"\bsite:\S+", "", q).strip(), "include_domains": m.group(1)}
    d = _json("GET", "https://api.search.tinyfish.ai", t, params=params, headers={"X-API-Key": get_provider_env("TINYFISH_API_KEY")})
    return [_row(r.get("url"), r.get("title"), r.get("snippet"), i + 1) for i, r in enumerate((d.get("results") or [])[:n])]


def tinyfish_fetch(url, t):
    d = _json("POST", "https://api.fetch.tinyfish.ai", t, json={"urls": [url], "format": "markdown"},
              headers={"X-API-Key": get_provider_env("TINYFISH_API_KEY")})
    res = d.get("results") or []
    if not res:
        err = ((d.get("errors") or [{}])[0].get("error")) or "aucun résultat"
        raise VendorError("client", f"TinyFish: {err}")
    return res[0].get("title") or "", res[0].get("text") or "", res[0].get("final_url") or url


def parallel_free_search(q, n, t):
    d = json.loads(_mcp("https://search.parallel.ai/mcp", "web_search", {"objective": q, "search_queries": [q], "session_id": SESSION_ID}, t))
    return [_row(r.get("url"), r.get("title"), " ".join(r.get("excerpts") or []), i + 1) for i, r in enumerate((d.get("results") or [])[:n])]


def parallel_free_fetch(url, t):
    d = json.loads(_mcp("https://search.parallel.ai/mcp", "web_fetch", {"urls": [url], "objective": "Full page content", "session_id": SESSION_ID}, t))
    res = d.get("results") or []
    if not res:
        raise VendorError("client", "Parallel: " + str(((d.get("errors") or [{}])[0]).get("error_type") or "aucun résultat"))
    r = res[0]
    return r.get("title") or "", r.get("full_content") or r.get("content") or "\n\n".join(r.get("excerpts") or []), r.get("url") or url


def _exa_rows(text: str, n: int) -> list[dict]:
    """Le MCP gratuit d'Exa rend du texte : blocs « Title: / URL: / Highlights: » séparés par « --- »."""
    rows = []
    for block in text.split("\n---\n"):
        title = url = ""
        hl, in_hl = [], False
        for s in (ln.strip() for ln in block.splitlines()):
            if s.startswith("Title:"):
                title = s[6:].strip()
            elif s.startswith("URL:"):
                url = s[4:].strip()
            elif in_hl and s and not s.startswith(("Author:", "Published:")):
                hl.append(s)
            if s.startswith(("Title:", "URL:", "Highlights:", "Published:", "Author:")):
                in_hl = s.startswith("Highlights:")
        if url:
            rows.append(_row(url, title, " ".join(hl)[:600], len(rows) + 1))
        if len(rows) >= n:
            break
    return rows


EXA_PAUSE_S = 3.0  # le refus d'Exa ne dure que quelques secondes (vu le 19/10 : l'appel suivant, 3 s après, passe)


def _exa(tool: str, args: dict, t: float) -> str:
    """Le MCP gratuit d'Exa refuse parfois un appel par un texte ordinaire (« You've hit Exa's free MCP
    rate limit… ») au lieu d'une erreur : on réessaie une fois après une courte pause, puis on le signale
    comme une saturation pour que le routeur passe au suivant et que le journal dise la vraie cause."""
    for essai in range(2):
        text = _mcp("https://mcp.exa.ai/mcp", tool, args, t)
        if "free MCP rate limit" not in text[:300]:
            return text
        if essai == 0:
            time.sleep(EXA_PAUSE_S)
    raise VendorError("rate_limit", text[:200])


def exa_free_search(q, n, t):
    return _exa_rows(_exa("web_search_exa", {"query": q, "numResults": n}, t), n)


def exa_free_fetch(url, t):
    return "", _exa("web_fetch_exa", {"urls": [url]}, t), url


def direct_fetch(url, t):
    resp = _request("GET", url, t, follow_redirects=True,
                    headers={"Accept": "application/atom+xml, application/rss+xml, text/xml;q=0.9, */*;q=0.5"})
    return "", resp.text, str(resp.url)


def perplexity_fast_search(q, n, t):
    body = {"query": q, "max_results": min(n, 20), "search_type": "fast", "search_language_filter": ["fr", "en"]}
    d = _json("POST", "https://api.perplexity.ai/search", t, json=body,
              headers={"Authorization": f"Bearer {get_provider_env('PERPLEXITY_API_KEY')}"})
    return [_row(r.get("url"), r.get("title"), r.get("snippet"), i + 1) for i, r in enumerate(d.get("results") or [])]


def firecrawl_fetch(url, t):
    d = _json("POST", "https://api.firecrawl.dev/v2/scrape", max(t, 45), json={"url": url, "formats": ["markdown"]},
              headers={"Authorization": f"Bearer {get_provider_env('FIRECRAWL_API_KEY')}"})
    data = d.get("data") or {}
    return (data.get("metadata") or {}).get("title") or "", data.get("markdown") or "", url


VENDORS: dict[str, dict[str, Any]] = {
    "keenable":        {"key": "KEENABLE_API_KEY", "search": keenable_search, "fetch": keenable_fetch},
    "keenable_public": {"key": "", "search": lambda q, n, t: keenable_search(q, n, t, True), "fetch": lambda u, t: keenable_fetch(u, t, True)},
    "tinyfish":        {"key": "TINYFISH_API_KEY", "search": tinyfish_search, "fetch": tinyfish_fetch},
    "parallel_free":   {"key": "", "search": parallel_free_search, "fetch": parallel_free_fetch},
    "exa_free":        {"key": "", "search": exa_free_search, "fetch": exa_free_fetch},
    "perplexity_fast": {"key": "PERPLEXITY_API_KEY", "search": perplexity_fast_search},
    "firecrawl":       {"key": "FIRECRAWL_API_KEY", "fetch": firecrawl_fetch},
    "direct":          {"key": "", "fetch": direct_fetch},
}


# ─── Provider ─────────────────────────────────────────────────────────────────

class RouteurWebProvider(WebSearchProvider):
    def __init__(self, ctx: Any = None):
        self._ctx = ctx
        self._lock = threading.Lock()
        self._cool_until: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "routeur"

    @property
    def display_name(self) -> str:
        return "Routeur Kalhyps (gratuit d'abord)"

    # Contrat : vérification bon marché, sans réseau.
    def is_available(self) -> bool:
        return any(self._ready(v) for v in self._cfg("search_chain") + self._cfg("extract_chain"))

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    # ── réglages, budgets, journal ──
    def _cfg(self, key: str) -> Any:
        val = None
        if self._ctx is not None:
            try:
                val = self._ctx.get_config(key)
            except Exception:
                val = None
        return DEFAULTS[key] if val is None else val

    def _ready(self, name: str) -> bool:
        v = VENDORS.get(name)
        return bool(v) and (not v["key"] or bool(get_provider_env(v["key"])))

    def _today(self) -> str:
        try:
            from zoneinfo import ZoneInfo
            return dt.datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
        except Exception:
            return dt.date.today().isoformat()

    def _usage(self) -> dict:
        try:
            return dict(self._ctx.state.get("usage", {})) if self._ctx is not None else {}
        except Exception:
            return {}

    def _reserve(self, name: str, op: str) -> Optional[str]:
        """None si l'appel peut partir (et le compte), sinon la raison du saut."""
        now = time.monotonic()
        if self._cool_until.get(name, 0) > now:
            return "cooldown"
        cap = (self._cfg("daily_budget") or {}).get(f"{name}.{op}")
        with self._lock:
            usage, day = self._usage(), self._today()
            today = dict(usage.get(day, {}))
            used = int(today.get(f"{name}.{op}", 0))
            if cap is not None and used >= int(cap):
                return "budget"
            today[f"{name}.{op}"] = used + 1
            pruned = {d: v for d, v in usage.items() if d >= (dt.date.fromisoformat(day) - dt.timedelta(days=7)).isoformat()}
            pruned[day] = today
            try:
                if self._ctx is not None:
                    self._ctx.state.set("usage", pruned)
            except Exception as exc:
                logger.debug("routeur: budget non enregistré: %s", exc)
        return None

    def _cool(self, name: str, kind: str) -> None:
        secs = (self._cfg("cooldown_s") or {}).get(kind)
        if secs:
            self._cool_until[name] = time.monotonic() + float(secs)

    def _log(self, op: str, vendor: str, outcome: str, ms: int, n: int = 0, chars: int = 0, subject: str = "") -> None:
        cost = COST_USD.get(f"{vendor}.{op}", 0.0) if outcome in ("ok", "empty", "off_topic", "short", "blocked") else 0.0
        logger.info("routeur %s vendor=%s outcome=%s ms=%d n=%d chars=%d cost_usd=%.4f subject=%r",
                    op, vendor, outcome, ms, n, chars, cost, subject[:120])
        try:
            if self._ctx is not None:
                path = self._ctx.state.data_dir / "calls.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists() and path.stat().st_size > 5_000_000:
                    path.replace(path.with_suffix(".jsonl.1"))
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "op": op, "vendor": vendor,
                                         "outcome": outcome, "ms": ms, "n": n, "chars": chars, "cost_usd": cost,
                                         "subject": subject[:200]}, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("routeur: journal non écrit: %s", exc)

    def _attempt(self, op: str, name: str, call: Callable[[], Any], subject: str):
        """(résultat, issue) ; issue = ok | erreur classée. Le journal et la mise en pause sont faits ici."""
        t0 = time.monotonic()
        try:
            return call(), None, t0
        except VendorError as exc:
            self._cool(name, exc.kind)
            self._log(op, name, exc.kind, round((time.monotonic() - t0) * 1000), subject=subject)
            return None, f"{exc.kind}: {str(exc)[:120]}", t0
        except Exception as exc:  # parse inattendu, etc.
            self._log(op, name, "error", round((time.monotonic() - t0) * 1000), subject=subject)
            return None, f"error: {exc!s:.120}", t0

    # ── web_search ──
    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        tried: list[str] = []
        fallback = None
        timeout = float(self._cfg("timeout_s"))
        for name in self._cfg("search_chain"):
            v = VENDORS.get(name)
            if not v or "search" not in v or not self._ready(name):
                continue
            skip = self._reserve(name, "search")
            if skip:
                tried.append(f"{name}:{skip}")
                continue
            rows, err, t0 = self._attempt("search", name, lambda: v["search"](query, limit, timeout), query)
            if err:
                tried.append(f"{name}:{err}")
                continue
            ms = round((time.monotonic() - t0) * 1000)
            chars = len(json.dumps(rows, ensure_ascii=False))
            if not rows:
                outcome = "empty"
            elif overlap(query, rows) < float(self._cfg("min_overlap")):
                outcome = "off_topic"
                fallback = fallback or (name, rows)
            else:
                outcome = "ok"
            self._log("search", name, outcome, ms, len(rows), chars, query)
            tried.append(f"{name}:{outcome}")
            if outcome == "ok":
                return {"success": True, "data": {"web": rows, "served_by": name, "tried": tried}}
        if fallback:
            return {"success": True, "data": {"web": fallback[1], "served_by": fallback[0], "tried": tried,
                    "warning": "Aucun moteur ne semble sur le sujet ; reformuler la requête."}}
        return {"success": False, "error": "Recherche impossible, tous les moteurs ont échoué : " + " ; ".join(tried)}

    # ── web_extract ──
    def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        with ThreadPoolExecutor(max_workers=min(5, max(1, len(urls)))) as pool:
            return list(pool.map(self._extract_one, urls))

    def _extract_one(self, url: str) -> dict[str, Any]:
        tried: list[str] = []
        timeout = float(self._cfg("timeout_s"))
        host = (httpx.URL(url).host or "").lower()
        chain = list(self._cfg("extract_chain"))
        if any(motif in url for motif in self._cfg("direct_first") or []):
            chain = ["direct"] + [n for n in chain if n != "direct"]
        for name in chain:
            v = VENDORS.get(name)
            if not v or "fetch" not in v or not self._ready(name):
                continue
            if any(host == d or host.endswith("." + d) for d in (self._cfg("deny") or {}).get(name, [])):
                tried.append(f"{name}:deny")
                continue
            skip = self._reserve(name, "extract")
            if skip:
                tried.append(f"{name}:{skip}")
                continue
            got, err, t0 = self._attempt("extract", name, lambda: v["fetch"](url, timeout), url)
            if err:
                tried.append(f"{name}:{err}")
                continue
            title, text, final = got
            ms = round((time.monotonic() - t0) * 1000)
            if blocked(text, title):
                outcome = "blocked"
            elif len(text) < int(self._cfg("min_chars")):
                outcome = "short"
            else:
                outcome = "ok"
            self._log("extract", name, outcome, ms, 1, len(text), url)
            tried.append(f"{name}:{outcome}")
            if outcome == "ok":
                doc = _doc(url, title, text, name)
                doc["metadata"]["tried"] = tried
                return doc
        return {"url": url, "title": "", "content": "", "error": "Lecture impossible : " + " ; ".join(tried) +
                ". Essayer le navigateur (browser_navigate).", "metadata": {"sourceURL": url, "tried": tried}}
