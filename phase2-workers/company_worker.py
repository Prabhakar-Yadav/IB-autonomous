#!/usr/bin/env python3
"""Company Website Worker.

Collect only — no reasoning, no summarizing (product-doc rule).
Visits a company's website, stores raw pages/PDFs in MinIO, and catalogs
them in the documents table for the extraction engine to process later.

Usage:
    python company_worker.py <slug> <display-name> <website-url>
Example:
    python company_worker.py zoho "Zoho" https://www.zoho.com
"""
import hashlib
import json
import sys
import time
from io import BytesIO
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from common import BUCKET, minio_client, pg_connect

MAX_PAGES = 15
MAX_PDFS = 5
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NemoClawCompanyWorker/0.1)"}

# crawl these first — they carry the most schema-relevant facts
PRIORITY_HINTS = ("about", "company", "product", "pricing", "customers", "press",
                  "news", "blog", "careers", "team", "security", "compliance")

WORKER_ID = "company-website-worker"


def classify(url: str) -> str:
    u = url.lower()
    if u.endswith(".pdf"):
        return "pdf"
    if "press" in u or "news" in u:
        return "press_release"
    if "career" in u or "jobs" in u:
        return "careers"
    if "blog" in u:
        return "blog"
    return "webpage"


def safe_name(url: str) -> str:
    path = urlparse(url).path.strip("/").replace("/", "_") or "home"
    return path[:120]


def store(conn, minio, company_id, slug, url, body: bytes, doc_type: str, stats) -> None:
    digest = hashlib.sha256(body).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM documents WHERE company_id = %s AND source_url = %s AND content_hash = %s",
            (company_id, url, digest),
        )
        if cur.fetchone():  # unchanged since last visit
            stats["skipped_unchanged"] += 1
            return
    key = f"{slug}/{digest[:12]}_{safe_name(url)}"
    minio.put_object(BUCKET, key, BytesIO(body), len(body))
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO documents
                 (company_id, collected_by, doc_type, title, source_url, object_key, content_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (company_id, WORKER_ID, doc_type, safe_name(url), url, key, digest),
        )
    stats["stored"] += 1
    print(f"  + stored {doc_type}: {url}")


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    slug, display_name, website = sys.argv[1], sys.argv[2], sys.argv[3]
    domain = urlparse(website).netloc

    conn = pg_connect()
    conn.autocommit = True
    minio = minio_client()

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO companies (slug, display_name, website)
               VALUES (%s, %s, %s)
               ON CONFLICT (slug) DO UPDATE SET website = EXCLUDED.website, updated_at = now()
               RETURNING id""",
            (slug, display_name, website),
        )
        company_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO workers (id, description) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (WORKER_ID, "Fetches company website pages and PDFs; collect only."),
        )
        cur.execute(
            "INSERT INTO worker_runs (worker_id, company_id) VALUES (%s, %s) RETURNING id",
            (WORKER_ID, company_id),
        )
        run_id = cur.fetchone()[0]

    stats = {"fetched": 0, "stored": 0, "skipped_unchanged": 0, "failed": 0}
    seen: set[str] = set()
    queue = [website]
    pdfs: list[str] = []

    pages_done = 0
    while queue and pages_done < MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            print(f"  ! fetch failed: {url} ({exc})")
            stats["failed"] += 1
            continue
        stats["fetched"] += 1
        pages_done += 1
        store(conn, minio, company_id, slug, url, resp.content, classify(url), stats)

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            links: list[str] = []
            for a in soup.find_all("a", href=True):
                nxt = urljoin(url, a["href"]).split("#")[0].rstrip("/")
                if not nxt or urlparse(nxt).netloc != domain or nxt in seen:
                    continue
                if nxt.lower().endswith(".pdf"):
                    if nxt not in pdfs:
                        pdfs.append(nxt)
                elif nxt not in queue:
                    links.append(nxt)
            links.sort(key=lambda u: 0 if any(h in u.lower() for h in PRIORITY_HINTS) else 1)
            queue.extend(links)
        except Exception:
            pass  # link discovery is best-effort
        time.sleep(1)  # be polite to the site

    for url in pdfs[:MAX_PDFS]:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            stats["fetched"] += 1
            store(conn, minio, company_id, slug, url, resp.content, "pdf", stats)
        except Exception as exc:
            print(f"  ! pdf failed: {url} ({exc})")
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
