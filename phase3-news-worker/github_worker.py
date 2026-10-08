#!/usr/bin/env python3
"""GitHub Worker. Collect only.

Reads a company's public GitHub organization via the official API (no scraping):
org profile, top repositories, languages, activity. Stores one text document per
run for the extraction engine (tech-stack, open-source posture, R&D signals).

Usage:
    python github_worker.py <slug> <github-org>
Optional env: GITHUB_TOKEN raises the API rate limit.
"""
import hashlib
import json
import sys
from io import BytesIO

import os

import requests

from common import BUCKET, minio_client, pg_connect

WORKER_ID = "github-worker"
TIMEOUT = 20


def gh(path: str):
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "NemoClawGitHubWorker/0.1"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(f"https://api.github.com{path}", headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    slug, org = sys.argv[1], sys.argv[2]

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
            (WORKER_ID, "Reads public GitHub org via official API; collect only."),
        )
        cur.execute(
            "INSERT INTO worker_runs (worker_id, company_id) VALUES (%s, %s) RETURNING id",
            (WORKER_ID, company_id),
        )
        run_id = cur.fetchone()[0]

    stats = {"repos_seen": 0, "stored": 0, "skipped_unchanged": 0}
    try:
        org_info = gh(f"/orgs/{org}")
        repos = gh(f"/orgs/{org}/repos?sort=pushed&per_page=50&type=public")
        stats["repos_seen"] = len(repos)
        top = sorted(repos, key=lambda r: r.get("stargazers_count", 0), reverse=True)[:15]
        langs: dict = {}
        for r in repos:
            if r.get("language"):
                langs[r["language"]] = langs.get(r["language"], 0) + 1

        lines = [
            f"GitHub organization profile for {display_name} (github.com/{org})",
            f"Org name: {org_info.get('name') or org}. Description: {org_info.get('description') or '—'}",
            f"Public repositories: {org_info.get('public_repos')}. Followers: {org_info.get('followers')}. "
            f"Org created: {org_info.get('created_at', '')[:10]}",
            f"Primary programming languages across public repos: "
            + ", ".join(f"{k} ({v} repos)" for k, v in sorted(langs.items(), key=lambda x: -x[1])[:8]),
            "", "Top public repositories by stars:",
        ]
        for r in top:
            lines.append(
                f"- {r['name']} [{r.get('language') or 'n/a'}, {r.get('stargazers_count', 0)} stars, "
                f"last push {r.get('pushed_at', '')[:10]}]: {(r.get('description') or '').strip()}"
            )
        body = "\n".join(lines).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        source_url = f"https://github.com/{org}"

        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM documents WHERE company_id = %s AND source_url = %s AND content_hash = %s",
                (company_id, source_url, digest),
            )
            if cur.fetchone():
                stats["skipped_unchanged"] += 1
            else:
                key = f"{slug}/github/{digest[:12]}_org-profile.txt"
                minio.put_object(BUCKET, key, BytesIO(body), len(body))
                cur.execute(
                    """INSERT INTO documents
                         (company_id, collected_by, doc_type, title, source_url, object_key, content_hash)
                       VALUES (%s, %s, 'other', %s, %s, %s, %s)""",
                    (company_id, WORKER_ID, f"GitHub org profile: {org}", source_url, key, digest),
                )
                stats["stored"] += 1
                print(f"  + stored GitHub profile for {org} ({len(top)} top repos)")
        status = "succeeded"
    except Exception as exc:
        print(f"  ! github worker failed: {exc}")
        status = "failed"

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE worker_runs SET finished_at = now(), status = %s, stats = %s::jsonb WHERE id = %s",
            (status, json.dumps(stats), run_id),
        )
    print(f"run complete: {stats}")


if __name__ == "__main__":
    main()
