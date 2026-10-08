#!/usr/bin/env python3
"""LinkedIn Signals Worker. Collect only.

LinkedIn forbids scraping, so this worker never touches linkedin.com directly.
Instead it collects what public search (Tavily) reports about the company's
LinkedIn presence — headcount range, headline, hiring signals — and stores the
snippets as evidence documents with their result URLs.

Usage:
    python linkedin_worker.py <slug>
"""
import hashlib
import json
import sys
from io import BytesIO

import os

import requests

from common import BUCKET, minio_client, pg_connect

WORKER_ID = "linkedin-worker"
TIMEOUT = 25


from websearch import search as web_search


def tavily(query: str):
    return web_search(query, 5)


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
            (WORKER_ID, "Collects public search snippets about the LinkedIn presence; no scraping."),
        )
        cur.execute(
            "INSERT INTO worker_runs (worker_id, company_id) VALUES (%s, %s) RETURNING id",
            (WORKER_ID, company_id),
        )
        run_id = cur.fetchone()[0]

    stats = {"searched": 0, "snippets": 0, "stored": 0, "skipped_unchanged": 0, "failed": 0}
    queries = [
        f'site:linkedin.com/company "{display_name}"',
        f'"{display_name}" linkedin employees headcount',
        f'"{display_name}" hiring jobs openings',
    ]

    lines = [f"Public search snippets about the LinkedIn presence and hiring of {display_name}.",
             "Each snippet lists its own source URL — treat snippet claims as that source's.", ""]
    for q in queries:
        try:
            results = tavily(q)
            stats["searched"] += 1
        except Exception as exc:
            print(f"  ! search failed: {q} ({exc})")
            stats["failed"] += 1
            continue
        for r in results:
            content = (r.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"[{r.get('title', '')}] ({r.get('url', '')})")
            lines.append(content)
            lines.append("")
            stats["snippets"] += 1

    if stats["snippets"]:
        body = "\n".join(lines).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        source_url = f"search://linkedin-signals/{slug}"
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM documents WHERE company_id = %s AND source_url = %s AND content_hash = %s",
                (company_id, source_url, digest),
            )
            if cur.fetchone():
                stats["skipped_unchanged"] += 1
            else:
                key = f"{slug}/linkedin/{digest[:12]}_signals.txt"
                minio.put_object(BUCKET, key, BytesIO(body), len(body))
                cur.execute(
                    """INSERT INTO documents
                         (company_id, collected_by, doc_type, title, source_url, object_key, content_hash)
                       VALUES (%s, %s, 'other', %s, %s, %s, %s)""",
                    (company_id, WORKER_ID, f"LinkedIn/hiring signals: {display_name}",
                     source_url, key, digest),
                )
                stats["stored"] += 1
                print(f"  + stored {stats['snippets']} LinkedIn/hiring snippets")

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE worker_runs SET finished_at = now(), status = 'succeeded', stats = %s::jsonb WHERE id = %s",
            (json.dumps(stats), run_id),
        )
    print(f"run complete: {stats}")


if __name__ == "__main__":
    main()
