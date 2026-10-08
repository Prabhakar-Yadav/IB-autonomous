#!/usr/bin/env python3
"""Registry Worker (India focus). Collect only.

Finds public corporate-registry mirror pages (ZaubaCorp, Tofler, TheCompanyCheck
etc., which republish MCA data: CIN, incorporation date, directors, paid-up
capital) via Tavily, fetches them, and stores them as 'filing' documents.

Usage:
    python registry_worker.py <slug>
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

WORKER_ID = "registry-worker"
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NemoClawRegistryWorker/0.1)"}
REGISTRY_DOMAINS = ("zaubacorp.com", "tofler.in", "thecompanycheck.com",
                    "falconebiz.com", "instafinancials.com", "companycheck.co.uk",
                    "indiafilings.com", "startupindia.gov.in", "mca.gov.in",
                    "vakilsearch.com", "cleartax.in", "corpseed.com", "legalwiz.in")


from websearch import search as web_search


def tavily(query: str):
    return web_search(query, 6)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    slug = sys.argv[1]

    conn = pg_connect()
    conn.autocommit = True
    minio = minio_client()

    with conn.cursor() as cur:
        cur.execute("SELECT id, display_name FROM companies WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if not row:
            sys.exit(f"company '{slug}' not found")
        company_id, display_name = row
        cur.execute(
            "INSERT INTO workers (id, description) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (WORKER_ID, "Fetches public corporate-registry mirror pages (MCA data); collect only."),
        )
        cur.execute(
            "INSERT INTO worker_runs (worker_id, company_id) VALUES (%s, %s) RETURNING id",
            (WORKER_ID, company_id),
        )
        run_id = cur.fetchone()[0]

    stats = {"searched": 0, "fetched": 0, "stored": 0, "skipped_unchanged": 0,
             "snippet_fallback": 0, "failed": 0}
    seen: set[str] = set()
    queries = [
        f'"{display_name}" CIN company registration zaubacorp OR thecompanycheck OR tofler OR indiafilings',
        f'"{display_name}" private limited directors paid-up capital',
        f'"{display_name}" date of incorporation CIN number',
        # MCA annual filings republished by these mirrors carry statutory financials
        f'"{display_name}" revenue profit financials tofler OR instafinancials OR thecompanycheck',
        f'"{display_name}" annual report EBITDA net worth auditor MCA filing',
    ]

    for q in queries:
        try:
            results = tavily(q)
            stats["searched"] += 1
        except Exception as exc:
            print(f"  ! search failed: {q} ({exc})")
            stats["failed"] += 1
            continue
        for r in results:
            url = (r.get("url") or "").split("#")[0]
            d = urlparse(url).netloc.lower().removeprefix("www.")
            if not url or url in seen or not any(d.endswith(rd) for rd in REGISTRY_DOMAINS):
                continue
            seen.add(url)
            body = None
            try:
                resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
                resp.raise_for_status()
                body = resp.content
                stats["fetched"] += 1
            except Exception:
                snippet = (r.get("content") or "").strip()
                if snippet:
                    body = f"{r.get('title', '')}\n\n{snippet}\n\n(source: {url})".encode()
                    stats["snippet_fallback"] += 1
                else:
                    stats["failed"] += 1
                    continue

            digest = hashlib.sha256(body).hexdigest()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM documents WHERE company_id = %s AND source_url = %s AND content_hash = %s",
                    (company_id, url, digest),
                )
                if cur.fetchone():
                    stats["skipped_unchanged"] += 1
                    continue
                key = f"{slug}/registry/{digest[:12]}_{d.replace('.', '_')}"
                minio.put_object(BUCKET, key, BytesIO(body), len(body))
                cur.execute(
                    """INSERT INTO documents
                         (company_id, collected_by, doc_type, title, source_url, object_key, content_hash)
                       VALUES (%s, %s, 'filing', %s, %s, %s, %s)""",
                    (company_id, WORKER_ID, (r.get("title") or d)[:300], url, key, digest),
                )
                stats["stored"] += 1
                print(f"  + stored registry page: {url}")
            time.sleep(1)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE worker_runs SET finished_at = now(), status = 'succeeded', stats = %s::jsonb WHERE id = %s",
            (json.dumps(stats), run_id),
        )
    print(f"run complete: {stats}")


if __name__ == "__main__":
    main()
