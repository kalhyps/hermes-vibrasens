#!/usr/bin/env python3
"""Banc d'essai recherche web + lecture de pages pour les agents Hermes (Axiom d'abord).

Stdlib seulement (urllib), Python 3.9+. Clés lues dans l'environnement, jamais affichées ni écrites
(toute valeur de clé est masquée dans les messages d'erreur). Petit volume, appels en série,
espacement minimal par fournisseur, plafond global d'appels et plafond de dépense estimée.

Exemples :
  python3 bench_web.py --list
  python3 bench_web.py --dry-run
  python3 bench_web.py                                   # fournisseurs gratuits disponibles seulement
  python3 bench_web.py --allow credit,paid --max-usd 0.40
  python3 bench_web.py --search keenable,tinyfish --fetch none --only q01,q12
  python3 bench_web.py --chain "search:keenable>tinyfish>parallel_free>perplexity_fast" \
                       --chain "fetch:keenable_fetch>tinyfish_fetch>parallel_free_fetch>firecrawl_scrape"
  python3 bench_web.py --resummarize bench-out-20261017-1030/results.csv   # refaire le résumé sans rappeler personne
  python3 bench_web.py --judge-cmd "hermes -z" --judge-max 40   # juge LLM optionnel (forfait Codex)

Sorties : <out>/results.csv (une ligne par appel) et <out>/summary.md.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shlex
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser

UA = "kalhyps-bench-web/1.0"
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0 Safari/537.36")
TIMEOUT_S = 30
MAX_BYTES = 8 * 1024 * 1024
SESSION_ID = uuid.uuid4().hex
HERMES_CHAR_LIMIT = 15000  # web_extract renvoie au modèle au plus ~15 000 caractères par page (défaut Hermes)
MIN_PAGE_CHARS = 300
KEY_VARS = ("KEENABLE_API_KEY", "TINYFISH_API_KEY", "PERPLEXITY_API_KEY", "PARALLEL_API_KEY", "EXA_API_KEY",
            "BRAVE_SEARCH_API_KEY", "LINKUP_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY", "JINA_API_KEY")


# ─── Secrets ──────────────────────────────────────────────────────────────────

def env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def redact(text: str) -> str:
    text = str(text or "")
    for var in KEY_VARS:
        val = env(var)
        if len(val) >= 6:
            text = text.replace(val, "***")
    return re.sub(r"(?i)(bearer\s+|api[_-]?key[=:]\s*)[A-Za-z0-9._\-]{12,}", r"\1***", text)


def load_env_file(path: str) -> None:
    """Ajoute à l'environnement les clés connues (KEY_VARS) trouvées dans un fichier .env, sans écraser."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].lstrip()
                name, sep, value = line.partition("=")
                name = name.strip()
                if sep and name in KEY_VARS and not env(name):
                    os.environ[name] = value.strip().strip("'\"")
    except OSError as exc:
        sys.exit(f"Fichier de clés illisible : {path} ({exc.strerror})")


# ─── HTTP ─────────────────────────────────────────────────────────────────────

class CallError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def http(method: str, url: str, *, headers=None, json_body=None, params=None, timeout=TIMEOUT_S):
    """Return (status, content_type, body_bytes, final_url). Raise CallError on HTTP >= 400 / transport."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    hdrs = {"User-Agent": UA, "Accept": "*/*"}
    hdrs.update(headers or {})
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_BYTES)
            return resp.status, resp.headers.get("Content-Type", ""), body, resp.geturl()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(1500).decode("utf-8", "replace")
        except Exception:
            detail = ""
        raise CallError(exc.code, f"HTTP {exc.code}: {detail[:300]}")
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        status = -1 if "timed out" in str(reason).lower() else 0
        raise CallError(status, f"transport: {reason}")


def http_json(method: str, url: str, **kw):
    _, _, body, _ = http(method, url, **kw)
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise CallError(200, "réponse non JSON: " + body[:200].decode("utf-8", "replace"))


def mcp_call(url: str, tool: str, arguments: dict) -> str:
    """Stateless JSON-RPC tools/call (même méthode que plugins/web/keyless_mcp.py de Hermes)."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    _, _, body, _ = http("POST", url, json_body=payload, headers={"Accept": "application/json, text/event-stream"})
    text = body.decode("utf-8", "replace")
    candidates = [text.strip()] if text.strip().startswith("{") else []
    candidates += [ln[6:] for ln in re.split(r"\r\n|\r|\n", text) if ln.startswith("data: ")]
    for cand in candidates:
        try:
            data = json.loads(cand)
        except ValueError:
            continue
        if data.get("error"):
            err = data["error"]
            raise CallError(200, "MCP error: " + str(err.get("message") if isinstance(err, dict) else err)[:300])
        result = data.get("result") or {}
        texts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict)]
        if result.get("isError"):
            raise CallError(200, "MCP tool error: " + " ".join(texts)[:300])
        for t in texts:
            if t:
                return t
        return ""
    raise CallError(200, "réponse MCP illisible")


# ─── HTML → texte (référence « lecture directe ») ─────────────────────────────

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "head"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "header",
             "footer", "ul", "ol", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def html_to_text(raw: str) -> tuple[str, str]:
    p = _Text()
    try:
        p.feed(raw)
    except Exception:
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text, p.title.strip()


def pdf_to_text(data: bytes) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "doc.pdf")
        with open(src, "wb") as fh:
            fh.write(data)
        try:
            out = subprocess.run([exe, "-layout", "-l", "30", src, "-"], capture_output=True, timeout=60)
            return out.stdout.decode("utf-8", "replace")
        except Exception:
            return ""


# ─── Fournisseurs : recherche ─────────────────────────────────────────────────
# Chaque fonction renvoie une liste de {url, title, description, date}. Exceptions = échec.

def _row(url, title, desc, date=None):
    return {"url": url or "", "title": title or "", "description": desc or "", "date": date or ""}


def _keenable_headers(key: str) -> dict:
    h = {"X-Keenable-Title": "kalhyps-bench"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def s_keenable(q, n):
    d = http_json("POST", "https://api.keenable.ai/v1/search", json_body={"query": q, "max_results": n},
                  headers=_keenable_headers(env("KEENABLE_API_KEY")))
    return [_row(r.get("url"), r.get("title"), r.get("snippet") or r.get("description")) for r in d.get("results") or []]


def s_keenable_public(q, n):
    d = http_json("POST", "https://api.keenable.ai/v1/search/public", json_body={"query": q, "max_results": n},
                  headers=_keenable_headers(""))
    return [_row(r.get("url"), r.get("title"), r.get("snippet") or r.get("description")) for r in d.get("results") or []]


def _site_filter(q):
    m = re.search(r"\bsite:([^\s]+)", q)
    return (m.group(1), re.sub(r"\bsite:[^\s]+", "", q).strip()) if m else (None, q)


def s_tinyfish(q, n):
    site, rest = _site_filter(q)
    params = {"query": rest if site else q}
    if site:
        params["include_domains"] = site
    d = http_json("GET", "https://api.search.tinyfish.ai", params=params, headers={"X-API-Key": env("TINYFISH_API_KEY")})
    return [_row(r.get("url"), r.get("title"), r.get("snippet"), r.get("date")) for r in (d.get("results") or [])[:n]]


def s_parallel_free(q, n):
    text = mcp_call("https://search.parallel.ai/mcp", "web_search",
                    {"objective": q, "search_queries": [q], "session_id": SESSION_ID})
    d = json.loads(text)
    return [_row(r.get("url"), r.get("title"), " ".join(r.get("excerpts") or [])[:600], r.get("publish_date"))
            for r in (d.get("results") or [])[:n]]


def _parse_exa_text(text, n):
    rows = []
    for block in text.split("\n---\n"):
        title = url = date = ""
        hl, in_hl = [], False
        for s in (ln.strip() for ln in block.splitlines()):
            if s.startswith("Title:"):
                title = s[6:].strip()
            elif s.startswith("URL:"):
                url = s[4:].strip()
            elif s.startswith("Published:"):
                date = s[10:].strip()
            elif in_hl and s and not s.startswith(("Author:", "Highlights:")):
                hl.append(s)
            if s.startswith(("Title:", "URL:", "Highlights:", "Published:", "Author:")):
                in_hl = s.startswith("Highlights:")
        if url:
            rows.append(_row(url, title, " ".join(hl)[:600], date))
        if len(rows) >= n:
            break
    return rows


def s_exa_free(q, n):
    return _parse_exa_text(mcp_call("https://mcp.exa.ai/mcp", "web_search_exa", {"query": q, "numResults": n}), n)


def _pplx(q, n, fast):
    body = {"query": q, "max_results": n}
    if fast:
        body["search_type"] = "fast"
    d = http_json("POST", "https://api.perplexity.ai/search", json_body=body,
                  headers={"Authorization": f"Bearer {env('PERPLEXITY_API_KEY')}"})
    return [_row(r.get("url"), r.get("title"), (r.get("snippet") or "")[:600], r.get("date")) for r in d.get("results") or []]


def s_perplexity_fast(q, n):
    return _pplx(q, n, True)


def s_perplexity(q, n):
    return _pplx(q, n, False)


def s_parallel(q, n):
    d = http_json("POST", "https://api.parallel.ai/v1/search", headers={"x-api-key": env("PARALLEL_API_KEY")},
                  json_body={"search_queries": [q], "objective": q, "mode": "fast", "advanced_settings": {"max_results": n}})
    return [_row(r.get("url"), r.get("title"), " ".join(r.get("excerpts") or [])[:600], r.get("publish_date"))
            for r in d.get("results") or []]


def s_exa(q, n):
    d = http_json("POST", "https://api.exa.ai/search", headers={"x-api-key": env("EXA_API_KEY")},
                  json_body={"query": q, "type": "instant", "numResults": n,
                             "contents": {"highlights": {"maxCharacters": 400}}})
    return [_row(r.get("url"), r.get("title"), " ".join(r.get("highlights") or [])[:600], r.get("publishedDate"))
            for r in d.get("results") or []]


def s_brave(q, n):
    d = http_json("GET", "https://api.search.brave.com/res/v1/web/search",
                  params={"q": q, "count": n, "country": "FR", "search_lang": "fr"},
                  headers={"X-Subscription-Token": env("BRAVE_SEARCH_API_KEY"), "Accept": "application/json"})
    return [_row(r.get("url"), r.get("title"), r.get("description"), r.get("page_age"))
            for r in (d.get("web") or {}).get("results") or []]


def s_linkup(q, n):
    d = http_json("POST", "https://api.linkup.so/v1/search", headers={"Authorization": f"Bearer {env('LINKUP_API_KEY')}"},
                  json_body={"q": q, "depth": "fast", "outputType": "searchResults", "maxResults": n})
    return [_row(r.get("url"), r.get("name"), (r.get("content") or "")[:600])
            for r in d.get("results") or [] if r.get("type", "text") == "text"]


def s_tavily(q, n):
    key = env("TAVILY_API_KEY")
    h = {"Authorization": f"Bearer {key}"} if key else {"X-Tavily-Access-Mode": "keyless"}
    d = http_json("POST", "https://api.tavily.com/search", headers=h,
                  json_body={"query": q, "max_results": n, "search_depth": "basic"})
    return [_row(r.get("url"), r.get("title"), (r.get("content") or "")[:600], r.get("published_date"))
            for r in d.get("results") or []]


def s_searxng(q, n):
    base = env("SEARXNG_URL").rstrip("/")
    d = http_json("GET", f"{base}/search", params={"q": q, "format": "json", "language": "fr"})
    return [_row(r.get("url"), r.get("title"), r.get("content"), r.get("publishedDate"))
            for r in (d.get("results") or [])[:n]]


def s_firecrawl(q, n):
    d = http_json("POST", "https://api.firecrawl.dev/v2/search", headers={"Authorization": f"Bearer {env('FIRECRAWL_API_KEY')}"},
                  json_body={"query": q, "limit": n})
    web = (d.get("data") or {}).get("web") if isinstance(d.get("data"), dict) else d.get("data")
    return [_row(r.get("url"), r.get("title"), r.get("description")) for r in web or []]


# ─── Fournisseurs : lecture ───────────────────────────────────────────────────
# Chaque fonction renvoie {text, title, final_url}. Exceptions = échec.

def f_direct(url):
    _, ctype, body, final = http("GET", url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "fr-FR,fr;q=0.9",
                                                      "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"})
    if "pdf" in ctype.lower() or body[:5] == b"%PDF-":
        return {"text": pdf_to_text(body), "title": "", "final_url": final}
    charset = (re.search(r"charset=([\w-]+)", ctype) or [None, "utf-8"])[1]
    raw = body.decode(charset, "replace")
    text, title = html_to_text(raw)
    return {"text": text, "title": title, "final_url": final}


def _keenable_fetch(url, path, key):
    d = http_json("GET", f"https://api.keenable.ai{path}", params={"url": url}, headers=_keenable_headers(key))
    return {"text": d.get("content") or "", "title": d.get("title") or "", "final_url": d.get("url") or url}


def f_keenable(url):
    return _keenable_fetch(url, "/v1/fetch", env("KEENABLE_API_KEY"))


def f_keenable_public(url):
    return _keenable_fetch(url, "/v1/fetch/public", "")


def f_tinyfish(url):
    d = http_json("POST", "https://api.fetch.tinyfish.ai", headers={"X-API-Key": env("TINYFISH_API_KEY")},
                  json_body={"urls": [url], "format": "markdown"})
    res = d.get("results") or []
    if not res:
        errs = d.get("errors") or [{}]
        raise CallError(200, "TinyFish: " + str(errs[0].get("error") or "aucun résultat")[:200])
    r = res[0]
    return {"text": r.get("text") or "", "title": r.get("title") or "", "final_url": r.get("final_url") or url}


def _parallel_doc(d, url):
    res = d.get("results") or []
    if not res:
        errs = d.get("errors") or [{}]
        e = errs[0]
        raise CallError(200, "Parallel: " + str(e.get("content") or e.get("error_type") or "aucun résultat")[:200])
    r = res[0]
    text = r.get("full_content") or r.get("content") or "\n\n".join(r.get("excerpts") or [])
    return {"text": text, "title": r.get("title") or "", "final_url": r.get("url") or url}


def f_parallel_free(url):
    text = mcp_call("https://search.parallel.ai/mcp", "web_fetch",
                    {"urls": [url], "objective": "Full page content", "session_id": SESSION_ID})
    return _parallel_doc(json.loads(text), url)


def f_parallel(url):
    d = http_json("POST", "https://api.parallel.ai/v1/extract", headers={"x-api-key": env("PARALLEL_API_KEY")},
                  json_body={"urls": [url], "advanced_settings": {"full_content": True}})
    return _parallel_doc(d, url)


def f_exa_free(url):
    text = mcp_call("https://mcp.exa.ai/mcp", "web_fetch_exa", {"urls": [url]})
    return {"text": text, "title": "", "final_url": url}


def f_exa(url):
    d = http_json("POST", "https://api.exa.ai/contents", headers={"x-api-key": env("EXA_API_KEY")},
                  json_body={"urls": [url], "text": True})
    res = d.get("results") or []
    if not res:
        raise CallError(200, "Exa: aucun contenu")
    return {"text": res[0].get("text") or "", "title": res[0].get("title") or "", "final_url": res[0].get("url") or url}


def f_firecrawl(url):
    if urllib.parse.urlparse(url).hostname in ("x.com", "twitter.com", "www.x.com"):
        raise CallError(0, "refusé par le banc : x.com coûte 30 crédits Firecrawl")
    d = http_json("POST", "https://api.firecrawl.dev/v2/scrape", headers={"Authorization": f"Bearer {env('FIRECRAWL_API_KEY')}"},
                  json_body={"url": url, "formats": ["markdown"]}, timeout=60)
    data = d.get("data") or {}
    meta = data.get("metadata") or {}
    return {"text": data.get("markdown") or "", "title": meta.get("title") or "", "final_url": meta.get("sourceURL") or url}


def f_jina(url):
    key = env("JINA_API_KEY")
    h = {"Accept": "text/plain", "X-Return-Format": "markdown"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    _, _, body, _ = http("GET", "https://r.jina.ai/" + url, headers=h, timeout=60)
    return {"text": body.decode("utf-8", "replace"), "title": "", "final_url": url}


def f_linkup(url):
    d = http_json("POST", "https://api.linkup.so/v1/fetch", headers={"Authorization": f"Bearer {env('LINKUP_API_KEY')}"},
                  json_body={"url": url})
    return {"text": d.get("markdown") or "", "title": "", "final_url": url}


def f_tavily(url):
    key = env("TAVILY_API_KEY")
    h = {"Authorization": f"Bearer {key}"} if key else {"X-Tavily-Access-Mode": "keyless"}
    d = http_json("POST", "https://api.tavily.com/extract", headers=h, json_body={"urls": [url]})
    res = d.get("results") or []
    if not res:
        raise CallError(200, "Tavily: aucun contenu")
    return {"text": res[0].get("raw_content") or "", "title": res[0].get("title") or "", "final_url": url}


# ─── Catalogue ────────────────────────────────────────────────────────────────
# tier : free = ni argent ni crédit mensuel consommé (quota gratuit ou sans clé) ; credit = crédit mensuel
# offert ($ ou crédits) ; paid = facturé. cost = $ par appel (prix publics relevés le 17/10/2026, voir rapport).
# units = unités de quota consommées (crédits Firecrawl/Tavily…). interval = secondes minimum entre deux appels.

PROVIDERS = {
    # recherche
    "keenable":         dict(kind="search", fn=s_keenable, needs=["KEENABLE_API_KEY"], tier="free", cost=0.0, units=1, interval=1.0, note="100 000 req/mois gratuites avec clé"),
    "keenable_public":  dict(kind="search", fn=s_keenable_public, needs=[], tier="free", cost=0.0, units=1, interval=1.5, note="sans clé, 1 000 req/h par IP"),
    "tinyfish":         dict(kind="search", fn=s_tinyfish, needs=["TINYFISH_API_KEY"], tier="free", cost=0.0, units=1, interval=2.5, note="30 req/min, 12 000/jour gratuits"),
    "parallel_free":    dict(kind="search", fn=s_parallel_free, needs=[], tier="free", cost=0.0, units=1, interval=2.0, note="MCP gratuit sans clé, limites non publiées"),
    "exa_free":         dict(kind="search", fn=s_exa_free, needs=[], tier="free", cost=0.0, units=1, interval=3.0, note="MCP gratuit sans clé, limité"),
    "searxng":          dict(kind="search", fn=s_searxng, needs=["SEARXNG_URL"], tier="free", cost=0.0, units=0, interval=1.0, note="auto-hébergé"),
    "tavily":           dict(kind="search", fn=s_tavily, needs=[], tier="credit", cost=0.008, units=1, interval=1.5, note="1 crédit (1 000/mois offerts avec clé) ; sans clé : mode keyless"),
    "firecrawl_search": dict(kind="search", fn=s_firecrawl, needs=["FIRECRAWL_API_KEY"], tier="credit", cost=0.0, units=2, interval=1.5, note="2 crédits / 10 résultats sur le quota mensuel"),
    "exa":              dict(kind="search", fn=s_exa, needs=["EXA_API_KEY"], tier="credit", cost=0.009, units=1, interval=1.0, note="instant 4 $/1k + highlights 1 $/1k pages ; 10 $/mois offerts"),
    "brave":            dict(kind="search", fn=s_brave, needs=["BRAVE_SEARCH_API_KEY"], tier="credit", cost=0.005, units=1, interval=1.1, note="5 $/1k ; 5 $/mois offerts (attribution)"),
    "linkup":           dict(kind="search", fn=s_linkup, needs=["LINKUP_API_KEY"], tier="credit", cost=0.005, units=1, interval=1.0, note="standard 5 $/1k (prix fast non publié) ; 20 $/mois offerts"),
    "perplexity_fast":  dict(kind="search", fn=s_perplexity_fast, needs=["PERPLEXITY_API_KEY"], tier="paid", cost=0.001, units=1, interval=1.0, note="Fast Search 1 $/1k"),
    "perplexity":       dict(kind="search", fn=s_perplexity, needs=["PERPLEXITY_API_KEY"], tier="paid", cost=0.005, units=1, interval=1.0, note="standard 5 $/1k"),
    "parallel":         dict(kind="search", fn=s_parallel, needs=["PARALLEL_API_KEY"], tier="paid", cost=0.001, units=1, interval=1.0, note="mode fast 1 $/1k"),
    # lecture
    "direct":              dict(kind="fetch", fn=f_direct, needs=[], tier="free", cost=0.0, units=0, interval=0.5, note="GET + HTML→texte (pdftotext pour les PDF)"),
    "keenable_fetch":      dict(kind="fetch", fn=f_keenable, needs=["KEENABLE_API_KEY"], tier="free", cost=0.0, units=1, interval=1.0, note="même quota que la recherche"),
    "keenable_public_fetch": dict(kind="fetch", fn=f_keenable_public, needs=[], tier="free", cost=0.0, units=1, interval=1.5, note="sans clé, 1 000 req/h par IP"),
    "tinyfish_fetch":      dict(kind="fetch", fn=f_tinyfish, needs=["TINYFISH_API_KEY"], tier="free", cost=0.0, units=1, interval=1.0, note="150 URL/min, 1 000 URL/jour gratuites"),
    "parallel_free_fetch": dict(kind="fetch", fn=f_parallel_free, needs=[], tier="free", cost=0.0, units=1, interval=2.0, note="MCP gratuit sans clé"),
    "exa_free_fetch":      dict(kind="fetch", fn=f_exa_free, needs=[], tier="free", cost=0.0, units=1, interval=3.0, note="MCP gratuit sans clé"),
    "jina_reader":         dict(kind="fetch", fn=f_jina, needs=[], tier="free", cost=0.0, units=0, interval=3.5, note="sans clé 20 req/min ; avec clé : jetons"),
    "firecrawl_scrape":    dict(kind="fetch", fn=f_firecrawl, needs=["FIRECRAWL_API_KEY"], tier="credit", cost=0.0, units=1, interval=1.5, note="1 crédit/page sur le quota mensuel"),
    "tavily_extract":      dict(kind="fetch", fn=f_tavily, needs=[], tier="credit", cost=0.0016, units=0.2, interval=1.5, note="1 crédit / 5 URL (basic)"),
    "exa_contents":        dict(kind="fetch", fn=f_exa, needs=["EXA_API_KEY"], tier="credit", cost=0.001, units=1, interval=1.0, note="1 $/1k pages"),
    "linkup_fetch":        dict(kind="fetch", fn=f_linkup, needs=["LINKUP_API_KEY"], tier="credit", cost=0.001, units=1, interval=1.0, note="1 $/1k sans JS"),
    "parallel_extract":    dict(kind="fetch", fn=f_parallel, needs=["PARALLEL_API_KEY"], tier="paid", cost=0.001, units=1, interval=1.0, note="1 $/1k URL"),
}

DEFAULT_CHAINS = [
    "search:keenable>tinyfish>parallel_free>perplexity_fast",
    "search:keenable>keenable_public>tinyfish>parallel_free>exa_free",
    "fetch:direct>keenable_fetch>tinyfish_fetch>parallel_free_fetch>firecrawl_scrape",
    "fetch:keenable_fetch>tinyfish_fetch>parallel_free_fetch>firecrawl_scrape",
]


def available(name: str) -> bool:
    return all(env(v) for v in PROVIDERS[name]["needs"])


# ─── Notation ─────────────────────────────────────────────────────────────────

STOP = set("""le la les un une des du de d l au aux et ou en dans sur pour par avec sans sous entre son sa ses leur leurs
ce cet cette ces qui que quoi dont est sont etre a comment quel quelle quels quelles plus moins tout tous pas ne se
the a an of to in on for and or with by from is are how what""".split())
BLOCK_MARKERS = ("just a moment", "attention required", "cf-chl", "checking your browser", "enable javascript and cookies",
                 "verifier que vous etes humain", "vous etes un robot", "captcha-delivery", "datadome", "access denied",
                 "acces refuse", "please enable js", "request unsuccessful. incapsula", "pardon our interruption")


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def query_terms(q: str) -> list[str]:
    q = re.sub(r"\bsite:\S+", " ", q)
    toks = re.findall(r"[a-z0-9]+", fold(q))
    return [t for t in dict.fromkeys(toks) if t not in STOP and (len(t) >= 3 or t.isdigit())]


def overlap(q: str, rows: list[dict], k: int = 3) -> float:
    terms = query_terms(q)
    if not terms:
        return 1.0
    hay = fold(" ".join(f"{r['title']} {r['description']} {r['url']}" for r in rows[:k]))
    return round(sum(t in hay for t in terms) / len(terms), 2)


def host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def domain_ok(url: str, expected: list[str]) -> bool:
    h = host(url)
    return any(h == d or h.endswith("." + d) for d in expected)


def fresh(rows: list[dict], days: int = 3) -> bool:
    today = dt.date.today()
    for r in rows[:5]:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", str(r.get("date") or ""))
        if m:
            try:
                if 0 <= (today - dt.date.fromisoformat(m.group(1))).days <= days:
                    return True
            except ValueError:
                pass
    return False


def search_runtime_pass(q: str, rows: list[dict]) -> bool:
    """Ce qu'un routeur peut juger sans vérité terrain : non vide et sur le sujet (même règle que le plugin)."""
    return bool(rows) and overlap(q, rows, 3) >= 0.5


def is_blocked(text: str, title: str = "") -> bool:
    head = fold((title or "") + " " + (text or "")[:4000])
    return len(text or "") < 6000 and any(m in head for m in BLOCK_MARKERS)


def model_search_chars(rows: list[dict]) -> int:
    payload = {"success": True, "data": {"web": [
        {"url": r["url"], "title": r["title"], "description": r["description"], "position": i + 1}
        for i, r in enumerate(rows)]}}
    return len(json.dumps(payload, indent=2, ensure_ascii=False))


# ─── Exécution ────────────────────────────────────────────────────────────────

FIELDS = ["ts", "kind", "case_id", "cat", "provider", "tier", "status", "http_status", "latency_ms", "n_results",
          "hit_at_1", "hit_at_3", "first_rank", "overlap", "fresh", "runtime_pass", "quality_pass", "chars",
          "model_tokens", "facts_expected", "facts_found", "tokens_per_fact", "cost_usd", "quota_units",
          "judge_top3", "error", "top_urls"]

_last_call: dict[str, float] = {}


def pace(name: str):
    wait = PROVIDERS[name]["interval"] - (time.monotonic() - _last_call.get(name, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_call[name] = time.monotonic()


def classify(exc: Exception) -> tuple[str, int]:
    if isinstance(exc, CallError):
        s = exc.status
        if s == 429:
            return "http_429", s
        if s >= 500:
            return "http_5xx", s
        if s >= 400:
            return "http_4xx", s
        if s == -1:
            return "timeout", s
        return ("transport" if s == 0 else "bad_payload"), s
    return "error", 0


def run_search(name, case, n):
    p = PROVIDERS[name]
    pace(name)
    t0 = time.monotonic()
    row = {"kind": "search", "case_id": case["id"], "cat": case.get("cat", ""), "provider": name, "tier": p["tier"],
           "cost_usd": p["cost"], "quota_units": p["units"], "http_status": 200}
    try:
        rows = p["fn"](case["q"], n)[:n]
        row["latency_ms"] = round((time.monotonic() - t0) * 1000)
    except Exception as exc:
        row["latency_ms"] = round((time.monotonic() - t0) * 1000)
        row["status"], row["http_status"] = classify(exc)
        row["error"] = redact(getattr(exc, "message", None) or repr(exc))[:240]
        row.update(n_results=0, runtime_pass=0, quality_pass=0, chars=0, model_tokens=0, cost_usd=0.0, quota_units=0)
        return row, []  # hypothèse : un appel refusé (4xx/429/5xx) n'est pas facturé (à vérifier sur les factures)
    exp = case.get("expected_domains") or []
    ranks = [i + 1 for i, r in enumerate(rows) if exp and domain_ok(r["url"], exp)]
    ov = overlap(case["q"], rows)
    fr = fresh(rows)
    rp = search_runtime_pass(case["q"], rows)
    if exp:
        quality = bool(ranks and ranks[0] <= 3)
    else:
        quality = ov >= 0.6 and (fr or not case.get("recent"))
    chars = model_search_chars(rows)
    row.update(status="ok" if rows else "empty", n_results=len(rows), hit_at_1=int(bool(ranks and ranks[0] == 1)),
               hit_at_3=int(bool(ranks and ranks[0] <= 3)) if exp else "", first_rank=ranks[0] if ranks else "",
               overlap=ov, fresh=int(fr), runtime_pass=int(rp), quality_pass=int(quality and bool(rows)),
               chars=chars, model_tokens=round(chars / 4), top_urls=" | ".join(r["url"] for r in rows[:3]))
    return row, rows


def run_fetch(name, case):
    p = PROVIDERS[name]
    pace(name)
    t0 = time.monotonic()
    row = {"kind": "fetch", "case_id": case["id"], "cat": case.get("cat", ""), "provider": name, "tier": p["tier"],
           "cost_usd": p["cost"], "quota_units": p["units"], "http_status": 200, "facts_expected": len(case.get("expect") or [])}
    try:
        doc = p["fn"](case["url"])
        row["latency_ms"] = round((time.monotonic() - t0) * 1000)
    except Exception as exc:
        row["latency_ms"] = round((time.monotonic() - t0) * 1000)
        row["status"], row["http_status"] = classify(exc)
        row["error"] = redact(getattr(exc, "message", None) or repr(exc))[:240]
        row.update(runtime_pass=0, quality_pass=0, chars=0, model_tokens=0, facts_found=0, cost_usd=0.0, quota_units=0)
        return row  # même hypothèse ; TinyFish l'écrit pour Fetch
    text = doc.get("text") or ""
    blocked = is_blocked(text, doc.get("title", ""))
    hay = fold(text)
    found = sum(fold(s) in hay for s in case.get("expect") or [])
    chars = len(text)
    mchars = min(chars, HERMES_CHAR_LIMIT)
    rp = (chars >= MIN_PAGE_CHARS) and not blocked
    status = "blocked" if blocked else ("empty" if chars < MIN_PAGE_CHARS else "ok")
    row.update(status=status, runtime_pass=int(rp), quality_pass=int(rp and found == row["facts_expected"]),
               chars=chars, model_tokens=round(mchars / 4), facts_found=found,
               tokens_per_fact=round(mchars / 4 / found, 1) if found else "", top_urls=doc.get("final_url") or "")
    return row


# ─── Juge LLM optionnel ───────────────────────────────────────────────────────

def judge(cmd: str, q: str, rows: list[dict]):
    lines = "\n".join(f"{i+1}. {r['title'][:120]} | {r['url'][:160]} | {r['description'][:240]}" for i, r in enumerate(rows[:5]))
    prompt = ("Tu évalues des résultats de recherche web pour un assistant administratif et comptable français. "
              "Pour chaque résultat, 1 s'il aide vraiment à répondre à la requête (source fiable, sujet exact, "
              "à jour si la requête l'exige), sinon 0. N'utilise aucun outil. Réponds uniquement par du JSON "
              '{"pertinents": [0 ou 1, ...]} dans l\'ordre.\n\n' f"Requête : {q}\n\nRésultats :\n{lines}")
    try:
        out = subprocess.run(shlex.split(cmd) + [prompt], capture_output=True, timeout=240, text=True)
        m = re.search(r"\{.*\}", out.stdout, re.S)
        vals = [int(bool(v)) for v in json.loads(m.group(0))["pertinents"]] if m else None
    except Exception:
        vals = None
    return round(sum(vals[:3]) / max(1, min(3, len(vals))), 2) if vals else ""


# ─── Résumé ───────────────────────────────────────────────────────────────────

def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def pct(n, d):
    return f"{100 * n / d:.0f} %" if d else "–"


def med(xs):
    return f"{statistics.median(xs):.0f}" if xs else "–"


def p90(xs):
    if not xs:
        return "–"
    xs = sorted(xs)
    return f"{xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]:.0f}"


def simulate(chain: list[str], rows: list[dict], kind: str):
    by = {}
    for r in rows:
        if r["kind"] == kind:
            by.setdefault(r["case_id"], {})[r["provider"]] = r
    served, qual, lat, cost, units, toks, steps = {}, 0, [], [], [], [], []
    cases = sorted(by)
    for cid in cases:
        tried, total_ms, total_cost, total_units, pick = 0, 0.0, 0.0, 0.0, None
        fallback = None
        for prov in chain:
            r = by[cid].get(prov)
            if r is None:
                continue
            tried += 1
            total_ms += _f(r["latency_ms"])
            total_cost += _f(r["cost_usd"])
            total_units += _f(r["quota_units"])
            if int(_f(r["runtime_pass"])):
                pick = r
                break
            if fallback is None and r["status"] == "ok":
                fallback = r
        pick = pick or fallback
        served[pick["provider"] if pick else "(rien)"] = served.get(pick["provider"] if pick else "(rien)", 0) + 1
        qual += int(_f(pick["quality_pass"])) if pick else 0
        lat.append(total_ms)
        cost.append(total_cost)
        units.append(total_units)
        toks.append(_f(pick["model_tokens"]) if pick else 0)
        steps.append(tried)
    n = len(cases)
    return dict(n=n, quality=pct(qual, n), median_ms=med(lat), p90_ms=p90(lat),
                usd_per_1k=f"{1000 * sum(cost) / n:.2f}" if n else "–", units_per_call=f"{sum(units) / n:.2f}" if n else "–",
                tokens=f"{statistics.mean(toks):.0f}" if toks else "–", steps=f"{statistics.mean(steps):.2f}" if steps else "–",
                served=", ".join(f"{k} {v}" for k, v in sorted(served.items(), key=lambda kv: -kv[1])))


def summarize(rows: list[dict], chains: list[str], out_md: str, meta: str):
    L = [f"# Banc web — résumé", "", meta, ""]
    for kind, title in (("search", "Recherche"), ("fetch", "Lecture de pages")):
        kr = [r for r in rows if r["kind"] == kind]
        if not kr:
            continue
        L += [f"## {title}", ""]
        if kind == "search":
            L += ["| fournisseur | palier | appels | répond | non vide et sur le sujet | qualité (domaine attendu top 3) | rang 1 | juge LLM top 3 | médiane ms | p90 ms | jetons vus par le modèle | $ / 1 000 | $ / 1 000 réussites |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        else:
            L += ["| fournisseur | palier | appels | répond | texte utilisable | qualité (toutes les chaînes attendues) | pages « dures » réussies | médiane ms | p90 ms | jetons vus par le modèle | jetons par fait trouvé | $ / 1 000 | unités de quota / appel |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for prov in sorted({r["provider"] for r in kr}, key=lambda p: (["free", "credit", "paid"].index(PROVIDERS.get(p, {"tier": "paid"})["tier"]), p)):
            pr = [r for r in kr if r["provider"] == prov]
            n = len(pr)
            ok = sum(r["status"] in ("ok", "empty", "blocked") for r in pr)
            rp = sum(int(_f(r["runtime_pass"])) for r in pr)
            q = sum(int(_f(r["quality_pass"])) for r in pr)
            lat = [_f(r["latency_ms"]) for r in pr if r["status"] in ("ok", "empty", "blocked")]
            toks = [_f(r["model_tokens"]) for r in pr if int(_f(r["runtime_pass"]))]
            cost = sum(_f(r["cost_usd"]) for r in pr)
            tier = pr[0]["tier"]
            if kind == "search":
                h1 = sum(int(_f(r["hit_at_1"])) for r in pr)
                judged = [_f(r["judge_top3"]) for r in pr if str(r.get("judge_top3", "")) != ""]
                L.append(f"| {prov} | {tier} | {n} | {pct(ok, n)} | {pct(rp, n)} | {pct(q, n)} | {pct(h1, n)} | "
                         f"{(f'{100*statistics.mean(judged):.0f} %' if judged else '–')} | {med(lat)} | {p90(lat)} | "
                         f"{(f'{statistics.mean(toks):.0f}' if toks else '–')} | {1000*cost/n:.2f} | {(f'{1000*cost/q:.2f}' if q else '–')} |")
            else:
                hard_ids = HARD_IDS
                hard = [r for r in pr if r["case_id"] in hard_ids]
                hq = sum(int(_f(r["runtime_pass"])) for r in hard)
                tpf = [_f(r["tokens_per_fact"]) for r in pr if str(r.get("tokens_per_fact", "")) != ""]
                units = sum(_f(r["quota_units"]) for r in pr) / n
                L.append(f"| {prov} | {tier} | {n} | {pct(ok, n)} | {pct(rp, n)} | {pct(q, n)} | {hq}/{len(hard)} | {med(lat)} | {p90(lat)} | "
                         f"{(f'{statistics.mean(toks):.0f}' if toks else '–')} | {(f'{statistics.median(tpf):.0f}' if tpf else '–')} | "
                         f"{1000*cost/n:.2f} | {units:.2f} |")
        cats = sorted({r["cat"] for r in kr})
        provs = sorted({r["provider"] for r in kr})
        L += ["", f"### Qualité par catégorie ({title.lower()})", "", "| catégorie | " + " | ".join(provs) + " |",
              "|---|" + "---|" * len(provs)]
        for c in cats:
            cells = []
            for p in provs:
                cr = [r for r in kr if r["provider"] == p and r["cat"] == c]
                cells.append(pct(sum(int(_f(r["quality_pass"])) for r in cr), len(cr)))
            L.append(f"| {c} | " + " | ".join(cells) + " |")
        errs = {}
        for r in kr:
            if r["status"] not in ("ok",):
                errs.setdefault(r["provider"], {}).setdefault(r["status"], 0)
                errs[r["provider"]][r["status"]] += 1
        if errs:
            L += ["", "Échecs par type : " + " ; ".join(f"{p} : " + ", ".join(f"{k} {v}" for k, v in e.items()) for p, e in sorted(errs.items()))]
        L.append("")
        kchains = [c.split(":", 1)[1].split(">") for c in chains if c.startswith(kind + ":")]
        if kchains:
            L += [f"### Routage simulé ({title.lower()})", "",
                  "Chaque chaîne rejoue les appels ci-dessus dans l'ordre et s'arrête au premier résultat qu'un routeur jugerait bon "
                  "(recherche : non vide et au moins la moitié des mots de la requête dans le top 3 ; lecture : au moins 300 caractères et pas "
                  "de page de blocage). La qualité est ensuite mesurée sur ce résultat. Les fournisseurs absents du banc sont sautés.", "",
                  "| chaîne | cas | qualité | appels moyens | médiane ms (cumul) | p90 ms | jetons | $ / 1 000 | unités de quota / appel | servi par |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
            for ch in kchains:
                s = simulate(ch, rows, kind)
                L.append(f"| {' → '.join(ch)} | {s['n']} | {s['quality']} | {s['steps']} | {s['median_ms']} | {s['p90_ms']} | "
                         f"{s['tokens']} | {s['usd_per_1k']} | {s['units_per_call']} | {s['served']} |")
            L.append("")
    L += ["## Lecture des colonnes", "",
          "- **répond** : réponse HTTP exploitable (même vide). **non vide et sur le sujet** / **texte utilisable** : ce qu'un routeur voit sans vérité terrain.",
          "- **qualité** : recherche, un domaine attendu dans les 3 premiers (sans domaine attendu : recouvrement ≥ 0,6 et fraîcheur si la requête est d'actualité) ; lecture, toutes les chaînes attendues présentes, sans page de blocage.",
          "- **jetons vus par le modèle** : caractères / 4 du JSON que `web_search` rend au modèle, ou du texte de page plafonné à 15 000 caractères comme `web_extract`.",
          "- **jetons par fait trouvé** : idée reprise de la Web Arena de treg ; plus bas = page plus dense.",
          "- **$** : prix publics au 17/10/2026 ; les paliers « free » et « credit » ne coûtent rien tant que le quota gratuit tient (voir unités de quota).",
          "- Latences prises depuis le serveur, en série, caches des fournisseurs non contrôlés : comparer entre fournisseurs d'un même passage."]
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


HARD_IDS: set = set()


# ─── Main ─────────────────────────────────────────────────────────────────────

def parse_list(value: str, kind: str, allow: set, explicit: bool) -> list[str]:
    if value == "none":
        return []
    names = [n for n in PROVIDERS if PROVIDERS[n]["kind"] == kind] if value in ("", "auto", "all") else \
        [v.strip() for v in value.split(",") if v.strip()]
    unknown = [n for n in names if n not in PROVIDERS or PROVIDERS[n]["kind"] != kind]
    if unknown:
        sys.exit(f"fournisseur(s) inconnu(s) pour {kind} : {', '.join(unknown)} (voir --list)")
    keep = []
    for n in names:
        if PROVIDERS[n]["tier"] not in allow:
            if explicit:
                print(f"  ignoré {n} : palier {PROVIDERS[n]['tier']} non autorisé (--allow)")
            continue
        if not available(n):
            if explicit:
                print(f"  ignoré {n} : variable(s) absente(s) {', '.join(PROVIDERS[n]['needs'])}")
            continue
        keep.append(n)
    return keep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_cases.json"))
    ap.add_argument("--search", default="auto", help="liste séparée par des virgules, 'auto' (tous ceux disponibles) ou 'none'")
    ap.add_argument("--fetch", default="auto")
    ap.add_argument("--allow", default="free", help="paliers autorisés : free,credit,paid (défaut : free)")
    ap.add_argument("--only", default="", help="identifiants de cas, ex. q01,q12,u05")
    ap.add_argument("--limit", type=int, default=5, help="résultats par recherche (Hermes : 5 par défaut)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--max-calls", type=int, default=600)
    ap.add_argument("--max-usd", type=float, default=0.25, help="plafond de dépense estimée avant de refuser de lancer")
    ap.add_argument("--chain", action="append", default=None, help="kind:a>b>c (répétable)")
    ap.add_argument("--judge-cmd", default="", help="commande juge LLM, le prompt est ajouté en dernier argument (ex. 'hermes -z')")
    ap.add_argument("--judge-max", type=int, default=40)
    ap.add_argument("--out", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--resummarize", default="", help="chemin d'un results.csv existant")
    ap.add_argument("--env-file", default="", help="fichier .env d'où lire les clés absentes de l'environnement "
                    "(ex. /opt/data/.env) ; seules les variables connues du banc sont lues, aucune n'est affichée")
    a = ap.parse_args()
    if a.env_file:
        load_env_file(a.env_file)

    with open(a.cases, encoding="utf-8") as fh:
        cases = json.load(fh)
    HARD_IDS.update(u["id"] for u in cases.get("urls", []) if u.get("hard"))
    chains = a.chain or DEFAULT_CHAINS

    if a.list:
        for n, p in PROVIDERS.items():
            print(f"{p['kind']:6} {n:22} {p['tier']:6} {'disponible' if available(n) else 'manque ' + ','.join(p['needs']):34} "
                  f"{p['cost']*1000:6.2f} $/1k  {p['note']}")
        return

    if a.resummarize:
        with open(a.resummarize, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        out_md = os.path.join(os.path.dirname(os.path.abspath(a.resummarize)), "summary.md")
        summarize(rows, chains, out_md, f"Résumé recalculé depuis `{a.resummarize}`.")
        print(out_md)
        return

    if not env("TAVILY_API_KEY"):  # mode keyless : ni crédit ni dollar
        for n in ("tavily", "tavily_extract"):
            PROVIDERS[n].update(tier="free", cost=0.0, units=0)
    allow = {t.strip() for t in a.allow.split(",") if t.strip()}
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    queries = [c for c in cases.get("queries", []) if not only or c["id"] in only]
    urls = [c for c in cases.get("urls", []) if not only or c["id"] in only]
    print("Sélection des fournisseurs :")
    sp = parse_list(a.search, "search", allow, a.search not in ("auto", "all", ""))
    fp = parse_list(a.fetch, "fetch", allow, a.fetch not in ("auto", "all", ""))
    plan = {n: len(queries) * a.repeat for n in sp} | {n: len(urls) * a.repeat for n in fp}
    total = sum(plan.values())
    usd = sum(PROVIDERS[n]["cost"] * k for n, k in plan.items())
    print(f"Plan : {len(queries)} requêtes × {len(sp)} moteurs, {len(urls)} URL × {len(fp)} lecteurs, ×{a.repeat} → {total} appels, "
          f"dépense estimée {usd:.3f} $")
    for n, k in plan.items():
        p = PROVIDERS[n]
        print(f"  {n:22} {p['tier']:6} {k:4} appels  {p['cost']*k:7.3f} $  {p['units']*k:6.1f} unités  ({p['note']})")
    if total > a.max_calls:
        sys.exit(f"refus : {total} appels > --max-calls {a.max_calls}")
    if usd > a.max_usd:
        sys.exit(f"refus : dépense estimée {usd:.3f} $ > --max-usd {a.max_usd}")
    if a.dry_run or total == 0:
        return

    out = a.out or f"bench-out-{dt.datetime.now():%Y%m%d-%H%M}"
    os.makedirs(out, exist_ok=True)
    csv_path = os.path.join(out, "results.csv")
    rows, judged = [], 0
    started = time.monotonic()
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        done = 0
        for rep in range(a.repeat):
            for case in queries:
                for name in sp:
                    row, res = run_search(name, case, a.limit)
                    if a.judge_cmd and res and judged < a.judge_max:
                        row["judge_top3"] = judge(a.judge_cmd, case["q"], res)
                        judged += 1
                    row["ts"] = dt.datetime.now().isoformat(timespec="seconds")
                    w.writerow(row)
                    fh.flush()
                    rows.append({k: row.get(k, "") for k in FIELDS})
                    done += 1
                    print(f"[{done}/{total}] search {case['id']} {name:18} {row['status']:10} {row.get('latency_ms', 0):6} ms "
                          f"q={row.get('quality_pass', 0)} {row.get('error', '')[:80]}")
            for case in urls:
                for name in fp:
                    row = run_fetch(name, case)
                    row["ts"] = dt.datetime.now().isoformat(timespec="seconds")
                    w.writerow(row)
                    fh.flush()
                    rows.append({k: row.get(k, "") for k in FIELDS})
                    done += 1
                    print(f"[{done}/{total}] fetch  {case['id']} {name:22} {row['status']:10} {row.get('latency_ms', 0):6} ms "
                          f"faits={row.get('facts_found', 0)}/{row.get('facts_expected', 0)} {row.get('error', '')[:80]}")
    meta = (f"Passage du {dt.datetime.now():%d/%m/%Y %H:%M}, {total} appels en {time.monotonic() - started:.0f} s, "
            f"hôte `{socket.gethostname()}`, cas `{os.path.basename(a.cases)}`, {a.limit} résultats par recherche.")
    summarize(rows, chains, os.path.join(out, "summary.md"), meta)
    print(f"\n{csv_path}\n{os.path.join(out, 'summary.md')}")


if __name__ == "__main__":
    main()
