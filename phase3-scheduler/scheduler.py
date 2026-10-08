#!/usr/bin/env python3
"""24/7 scheduler — the platform's heartbeat.

Runs forever inside its own container. Every cycle it:
  1. re-crawls every active company's website (company worker)
  2. sweeps news, LinkedIn signals, registry filings, patents, GitHub per company
  3. extracts what generic collection found
  4. gap-filler: targets remaining EMPTY fields with focused searches (not generic)
  5. files analyst-submitted transcripts (intake processor)
  6. extracts again, then refreshes the semantic index (phase 5)

Interval via INTERVAL_HOURS (default 24). Each step is a subprocess; one
failing step never stops the cycle. All collection/extraction audit trails
are written by the workers themselves (worker_runs).
"""
import os
import subprocess
import time
from datetime import datetime, timezone

import psycopg

INTERVAL_HOURS = float(os.environ.get("INTERVAL_HOURS", "24"))
EXTRACT_BATCH = 15
MAX_EXTRACT_PASSES = 20


def pg():
    return psycopg.connect(
        host=os.environ.get("PGHOST", "postgres"),
        dbname=os.environ.get("PGDATABASE", "enterprise_intel"),
        user=os.environ.get("PGUSER", "vaultstack"),
        password=os.environ["PGPASSWORD"],
    )


def log(msg: str) -> None:
    print(f"[scheduler {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}Z] {msg}", flush=True)


def run_step(cmd: list, cwd: str) -> None:
    log("run: " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        log(f"step failed (rc={result.returncode}) — continuing with next step")


def pending_documents(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM documents WHERE status = 'new'")
        return cur.fetchone()[0]


def cycle() -> None:
    conn = pg()
    conn.autocommit = True
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT slug, display_name, website, github_org "
                        "FROM companies WHERE status = 'active' ORDER BY slug")
            companies = cur.fetchall()
        except Exception:
            # github_org migration not applied yet — run without it
            cur.execute("SELECT slug, display_name, website "
                        "FROM companies WHERE status = 'active' ORDER BY slug")
            companies = [(s, n, w, None) for s, n, w in cur.fetchall()]
    log(f"cycle start — {len(companies)} active company(ies)")

    # close ghost runs left behind by crashed/killed workers
    with conn.cursor() as cur:
        cur.execute("""UPDATE worker_runs SET status = 'failed', finished_at = now()
                       WHERE status = 'running' AND started_at < now() - interval '2 hours'""")

    have_tavily = bool(os.environ.get("TAVILY_API_KEY"))
    for slug, display_name, website, github_org in companies:
        if website:
            run_step(["python", "company_worker.py", slug, display_name, website], cwd="/app/phase2")
        if have_tavily:
            run_step(["python", "news_worker.py", slug], cwd="/app/phase3")
            run_step(["python", "linkedin_worker.py", slug], cwd="/app/phase3")
            run_step(["python", "registry_worker.py", slug], cwd="/app/phase3")
            run_step(["python", "patents_worker.py", slug], cwd="/app/phase3")
        if github_org:
            run_step(["python", "github_worker.py", slug, github_org], cwd="/app/phase3")

    # extract what generic crawling found so far, THEN target the remaining gaps
    for _ in range(MAX_EXTRACT_PASSES):
        if pending_documents(conn) == 0:
            break
        run_step(["python", "extractor.py", "--limit", str(EXTRACT_BATCH)], cwd="/app/phase2")

    if have_tavily:
        for slug, *_ in companies:
            run_step(["python", "gap_filler_worker.py", slug, "--max-fields", "30"],
                     cwd="/app/phase3")

    run_step(["python", "transcript_processor.py"], cwd="/app/phase4")

    for _ in range(MAX_EXTRACT_PASSES):
        remaining = pending_documents(conn)
        if remaining == 0:
            break
        log(f"extraction: {remaining} document(s) pending")
        run_step(["python", "extractor.py", "--limit", str(EXTRACT_BATCH)], cwd="/app/phase2")

    # phase 5: refresh the semantic index over newly extracted documents
    if os.environ.get("NVIDIA_API_KEY"):
        run_step(["python", "embedder.py", "--limit", "60"], cwd="/app/phase5")

    conn.close()
    log("cycle complete")


def main() -> None:
    log(f"scheduler up — interval {INTERVAL_HOURS}h")
    while True:
        try:
            cycle()
        except Exception as exc:
            log(f"cycle crashed: {exc} — will retry next interval")
        time.sleep(INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    main()
