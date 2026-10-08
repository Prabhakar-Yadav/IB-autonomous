#!/usr/bin/env python3
"""News Worker.

Collect only — no reasoning (product-doc rule).
Searches business news about a company via the Tavily search API, fetches the
articles, stores raw copies in MinIO, and catalogs them in the documents table.
The extraction engine (phase 2) processes them later like any other document.

Usage:
    python news_worker.py <slug>
(The company must already exist — created by company_worker.py.)
"""
import hashlib
import json
import sys
import time
from io import BytesIO
from urllib.parse import urlparse

import os

import requests

from common import BUCKET, minio_client, pg_connect

WORKER_ID = "news-worker"
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NemoClawNewsWorker/0.1)"}
MAX_RESULTS_PER_QUERY = 5

# what a PE analyst wants from the press, not from the company itself
QUERY_TEMPLATES = [
    "{name} funding round investment",
    "{name} valuation",
    "{name} acquisition OR partnership",
    "{name} lawsuit OR regulator OR investigation",
    "{name} founder OR CEO OR leadership change",
    # Indian startup financials are reported by Inc42/Entrackr/Moneycontrol when MCA
    # filings drop — this is how revenue/EBITDA legitimately becomes public
    "{name} FY25 OR FY24 revenue profit loss financial results",
    "{name} annual revenue crore EBITDA margin gross profit",
    "{name} headcount employees ESOP buyback",
]


from websearch import search as web_search


def tavily_search(query: str) -> list:
    """Open-source search (SearXNG); name kept for call-site compatibility."""
    return web_search(query, MAX_RESULTS_PER_QUERY)


def safe_name(url: str) -> str:
    path = (urlparse(url).netloc + "_" + urlparse(url).path.strip("/")).replace("/", "_")
    return path[:120] or "article"


def store(conn, minio, company_id, slug, url, title, body: bytes, stats) -> None:
    digest = hashlib.sha256(body).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM documents WHERE company_id = %s AND source_url = %s AND content_hash = %s",
            (company_id, url, digest),
        )
        if cur.fetchone():
            stats["skipped_unchanged"] += 1
            return
    key = f"{slug}/news/{digest[:12]}_{safe_name(url)}"
    minio.put_object(BUCKET, key, BytesIO(body), len(body))
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO documents
                 (company_id, collected_by, doc_type, title, source_url, object_key, content_hash)
               VALUES (%s, %s, 'news', %s, %s, %s, %s)""",
            (company_id, WORKER_ID, (title or safe_name(url))[:300], url, key, digest),
        )
    stats["stored"] += 1
    print(f"  + stored news: {title[:60] if title else url}")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    slug = sys.argv[1]

    conn = pg_connect()
    conn.autocommit = True
    minio = minio_client()

    with conn.cursor() as cur:
        cur.execute("SELECT id, display_name, website FROM companies WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if not row:
            sys.exit(f"company '{slug}' not found — run company_worker.py for it first")
        company_id, display_name, website = row
        own_domain = urlparse(website or "").netloc.removeprefix("www.")
        cur.execute(
            "INSERT INTO workers (id, description) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (WORKER_ID, "Searches and fetches business-news articles; collect only."),
        )
        cur.execute(
            "INSERT INTO worker_runs (worker_id, company_id) VALUES (%s, %s) RETURNING id",
            (WORKER_ID, company_id),
        )
        run_id = cur.fetchone()[0]

    stats = {"searched": 0, "fetched": 0, "stored": 0, "skipped_unchanged": 0,
             "snippet_fallback": 0, "failed": 0}
    seen: set[str] = set()

    for template in QUERY_TEMPLATES:
        query = template.format(name=display_name)
        try:
            results = tavily_search(query)
            stats["searched"] += 1
        except Exception as exc:
            print(f"  ! search failed: {query} ({exc})")
            stats["failed"] += 1
            continue

        for r in results:
            url = (r.get("url") or "").split("#")[0]
            title = r.get("title") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            # skip the company's own site — that's the company worker's beat
            if own_domain and urlparse(url).netloc.removeprefix("www.") == own_domain:
                continue
            try:
                resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
                resp.raise_for_status()
                stats["fetched"] += 1
                store(conn, minio, company_id, slug, url, title, resp.content, stats)
            except Exception:
                # paywalled/blocked article: keep Tavily's text snippet as evidence
                snippet = (r.get("content") or "").strip()
                if snippet:
                    body = f"{title}\n\n{snippet}\n\n(source: {url})".encode()
                    store(conn, minio, company_id, slug, url, title, body, stats)
                    stats["snippet_fallback"] += 1
                else:
                    stats["failed"] += 1
            time.sleep(1)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE worker_runs SET finished_at = now(), status = 'succeeded', stats = %s::jsonb WHERE id = %s",
            (json.dumps(stats), run_id),
        )
    print(f"run complete: {stats}")


if __name__ == "__main__":
    main()
