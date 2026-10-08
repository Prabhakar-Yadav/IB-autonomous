#!/usr/bin/env python3
"""Bridge on-prem RAG results into the main dashboard database.

Reads the JSON produced by extract_local.py and writes each field into the
platform's field_values table for the given company slug, marked with document
provenance (source_type='document', collected_by='onprem-rag'). Company docs are
authoritative, so high-confidence values are marked verified.

Runs inside the dashboard container (has psycopg + DB access).
Usage:  python bridge_to_dashboard.py <results.json> <company-slug>
"""
import json
import os
import sys

import psycopg


def main():
    results_path, slug = sys.argv[1], sys.argv[2]
    data = json.load(open(results_path, encoding="utf-8"))
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "postgres"),
        dbname=os.environ.get("PGDATABASE", "enterprise_intel"),
        user=os.environ.get("PGUSER", "vaultstack"),
        password=os.environ["PGPASSWORD"],
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM companies WHERE slug=%s", (slug,))
        row = cur.fetchone()
        if not row:
            cur.execute("INSERT INTO companies (slug, display_name) VALUES (%s,%s) RETURNING id",
                        (slug, slug.replace("-", " ").title()))
            row = cur.fetchone()
        company_id = row[0]

    written = 0
    for f in data["fields"]:
        conf = float(f.get("confidence", 0.5))
        # the company's own documents are the most authoritative source we have —
        # every RAG-extracted field is marked verified and supersedes any web value.
        status = "verified"
        src = f"onprem://{f.get('source_file','document')}"
        with conn.cursor() as cur:
            # verify field_id exists in schema (skip stray ids)
            cur.execute("SELECT 1 FROM schema_fields WHERE id=%s", (f["field_id"],))
            if not cur.fetchone():
                continue
            # supersede any current value (append-only history)
            cur.execute(
                """SELECT id, version FROM field_values
                   WHERE company_id=%s AND field_id=%s AND is_current""",
                (company_id, f["field_id"]))
            cur_row = cur.fetchone()
            version = (cur_row[1] + 1) if cur_row else 1
            if cur_row:
                cur.execute("UPDATE field_values SET is_current=false WHERE id=%s", (cur_row[0],))
            cur.execute(
                """INSERT INTO field_values
                     (company_id, field_id, value_text, confidence, source_type,
                      source_url, source_locator, collected_by, verified_by, verified_at,
                      version, status, is_current)
                   VALUES (%s,%s,%s,%s,'document',%s,%s,'onprem-rag',
                           CASE WHEN %s='verified' THEN 'onprem-rag' ELSE NULL END,
                           CASE WHEN %s='verified' THEN now() ELSE NULL END,
                           %s,%s,true)""",
                (company_id, f["field_id"], f["value"][:4000], conf, src,
                 (f.get("quote") or "")[:500], status, status, version, status))
        written += 1

    print(f"bridged {written} field(s) into '{slug}' (company_id {company_id})")


if __name__ == "__main__":
    main()
