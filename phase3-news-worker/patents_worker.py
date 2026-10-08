#!/usr/bin/env python3
"""Patents Worker. Collect only.

Searches Google Patents (public, no key needed) for patents assigned to the
company, plus a Tavily sweep of the Indian Patent Office public database for
India-based companies. Feeds Section D (Technology & IP) — "Patents Filed / Granted".

Usage:
    python patents_worker.py <slug>
"""
import hashlib
import json
import sys
import time
from io import BytesIO

import os

import requests

from common import BUCKET, minio_client, pg_connect

WORKER_ID = "patents-worker"
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NemoClawPatentsWorker/0.1)"}


def google_patents_search(query: str):
    # public XHR endpoint behind patents.google.com's search UI — no key required
    resp = requests.get(
        "https://patents.google.com/xhr/query",
        params={"url": f"q={query}", "exp": ""},
        headers=HEADERS, timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


from websearch import search as web_search


def tavily(query: str, max_results=4):
    return web_search(query, max_results)


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
            (WORKER_ID, "Searches Google Patents (public) + Indian Patent Office; collect only."),
        )
        cur.execute(
            "INSERT INTO worker_runs (worker_id, company_id) VALUES (%s, %s) RETURNING id",
            (WORKER_ID, company_id),
        )
        run_id = cur.fetchone()[0]

    stats = {"patents_found": 0, "stored": 0, "skipped_unchanged": 0, "failed": 0}
    lines = [f"Patent search results for assignee \"{display_name}\" (Google Patents public search)."]

    # patents are filed under the LEGAL entity, not the brand — try both
    with conn.cursor() as cur:
        cur.execute(
            """SELECT value_text FROM field_values fv JOIN schema_fields sf ON sf.id = fv.field_id
               WHERE fv.company_id = %s AND fv.is_current AND sf.id = 'legal_entity_name'""",
            (company_id,),
        )
        legal_row = cur.fetchone()
    names = [display_name]
    if legal_row and legal_row[0]:
        names.append(legal_row[0].split("(")[0].strip())
    for extra in (f"{display_name} Software", f"{display_name} Technologies",
                  f"{display_name} Private Limited"):
        names.append(extra)

    try:
        patents = []
        seen_pubs = set()
        for name in dict.fromkeys(names):
            data = google_patents_search(f'assignee:"{name}"')
            for cluster in ((data or {}).get("results") or {}).get("cluster", []):
                for item in cluster.get("result", []):
                    p = item.get("patent", {})
                    pub = p.get("publication_number")
                    if p and pub not in seen_pubs:
                        seen_pubs.add(pub)
                        patents.append(p)
            time.sleep(1)
        stats["patents_found"] = len(patents)
        for p in patents[:25]:
            lines.append(
                f"- {p.get('publication_number', '')}: {p.get('title', '')} "
                f"[filed {p.get('filing_date', '?')}, priority {p.get('priority_date', '?')}] "
                f"assignee: {p.get('assignee', '')} "
                f"(https://patents.google.com/patent/{p.get('publication_number', '')})"
            )
    except Exception as exc:
        print(f"  ! Google Patents search failed: {exc}")
        stats["failed"] += 1

    # supplementary: Indian Patent Office coverage via search (site-restricted)
    try:
        for r in tavily(f'"{display_name}" patent application ipindia OR "patent office" India'):
            title, url = r.get("title", ""), r.get("url", "")
            content = (r.get("content") or "").strip()
            if content:
                lines.append(f"\n[IPO reference] {title} ({url})\n{content}")
    except Exception as exc:
        print(f"  ! IPO sweep failed: {exc}")

    if len(lines) > 1:
        body = "\n".join(lines).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        source_url = f"https://patents.google.com/?assignee={display_name.replace(' ', '+')}"
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM documents WHERE company_id = %s AND source_url = %s AND content_hash = %s",
                (company_id, source_url, digest),
            )
            if cur.fetchone():
                stats["skipped_unchanged"] += 1
            else:
                key = f"{slug}/patents/{digest[:12]}_patents.txt"
                minio.put_object(BUCKET, key, BytesIO(body), len(body))
                cur.execute(
                    """INSERT INTO documents
                         (company_id, collected_by, doc_type, title, source_url, object_key, content_hash)
                       VALUES (%s, %s, 'filing', %s, %s, %s, %s)""",
                    (company_id, WORKER_ID, f"Patent search: {display_name}", source_url, key, digest),
                )
                stats["stored"] += 1
                print(f"  + stored patent search results ({stats['patents_found']} patents found)")

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE worker_runs SET finished_at = now(), status = 'succeeded', stats = %s::jsonb WHERE id = %s",
            (json.dumps(stats), run_id),
        )
    print(f"run complete: {stats}")


if __name__ == "__main__":
    main()
