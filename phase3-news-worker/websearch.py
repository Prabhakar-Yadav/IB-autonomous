"""Open-source web search — SearXNG first, Tavily only as an optional fallback.

SearXNG is a self-hosted metasearch engine (aggregates Google/Bing/DuckDuckGo/etc).
No API key, no rate limits, no quota — it runs as a container next to the database.
Every worker calls search() from here instead of hitting Tavily directly.

Env:
  SEARXNG_URL      default http://searxng:8080
  TAVILY_API_KEY   optional; used only if SearXNG returns nothing
"""
import os
import time

import requests

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")
TIMEOUT = 25


def _searxng(query: str, max_results: int) -> list:
    resp = requests.get(
        f"{SEARXNG_URL}/search",
        params={"q": query, "format": "json", "language": "en",
                "safesearch": 0, "categories": "general,news"},
        timeout=TIMEOUT,
        headers={"User-Agent": "NemoClawWorker/1.0"},
    )
    resp.raise_for_status()
    out = []
    for r in (resp.json().get("results") or [])[:max_results]:
        out.append({
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "content": (r.get("content") or "").strip(),
        })
    return out


def _tavily(query: str, max_results: int) -> list:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return []
    resp = requests.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"query": query, "max_results": max_results, "search_depth": "basic"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return [{"url": r.get("url", ""), "title": r.get("title", ""),
             "content": (r.get("content") or "").strip()}
            for r in resp.json().get("results", [])]


def search(query: str, max_results: int = 5) -> list:
    """Returns [{url, title, content}]. Never raises for a single engine failure."""
    for attempt in range(2):
        try:
            results = _searxng(query, max_results)
            if results:
                return results
        except Exception as exc:
            if attempt == 0:
                time.sleep(2)  # SearXNG may still be warming up
                continue
            print(f"    (searxng unavailable: {type(exc).__name__}; trying fallback)")
    try:
        return _tavily(query, max_results)
    except Exception:
        return []
