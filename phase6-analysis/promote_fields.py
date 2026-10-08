#!/usr/bin/env python3
"""Promote discovered fields into the real schema, then let the pipeline fill them.

The discovery agent only PROPOSES columns. This script turns proposals into actual
schema_fields rows. Because the extractor reads its catalog from schema_fields at
run time, the very next extraction pass fills the new columns across every company —
no code change needed.

Promotion rule (either qualifies):
  * proposal appears for >= --min-companies companies, or
  * avg confidence >= --min-confidence
  * anything explicitly marked status='accepted' is always promoted
Rejected proposals are never promoted.

Usage:
  python promote_fields.py                    # promote + requeue docs for re-extraction
  python promote_fields.py --dry-run
  python promote_fields.py --no-requeue       # promote only
"""
import argparse

from psycopg.rows import dict_row

from common import pg_connect

SECTION_TITLES = {
    "A": "Interaction Logistics", "B": "Company Snapshot",
    "C": "Business & Product Overview",
    "D": "Technology & Intellectual Property (Deep Tech Focus)",
    "E": "Market & Competitive Landscape", "F": "Financial Overview",
    "G": "Cap Table & Fundraising History", "H": "Current Fundraising Requirement",
    "I": "Team & Organisation", "J": "Traction & Key Metrics",
    "K": "Legal, Regulatory & Compliance", "L": "Customer & Revenue Concentration",
    "N": "Follow-up Actions & Next Steps",
    "M": "Discovered Fields",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-companies", type=int, default=2)
    ap.add_argument("--min-confidence", type=float, default=0.75)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-requeue", action="store_true")
    args = ap.parse_args()

    conn = pg_connect(row_factory=dict_row)
    conn.autocommit = True

    with conn.cursor() as cur:
        cur.execute("SELECT coalesce(max(sort_order), 0) AS m FROM schema_fields")
        next_order = cur.fetchone()["m"] + 10
        cur.execute("SELECT id FROM schema_fields")
        existing = {r["id"] for r in cur.fetchall()}

        cur.execute(
            """SELECT r.field_key, r.label, r.section_hint, r.companies,
                      r.avg_confidence, r.top_rationale, r.accepted
               FROM discovered_field_rollup r
               WHERE NOT EXISTS (
                   SELECT 1 FROM discovered_fields d
                   WHERE d.field_key = r.field_key AND d.status = 'rejected')
               ORDER BY r.companies DESC, r.avg_confidence DESC"""
        )
        proposals = cur.fetchall()

    promoted = 0
    for p in proposals:
        key = p["field_key"]
        if key in existing:
            continue
        qualifies = (p["accepted"]
                     or p["companies"] >= args.min_companies
                     or float(p["avg_confidence"] or 0) >= args.min_confidence)
        if not qualifies:
            continue

        code = (p["section_hint"] or "M").strip().upper()[:1]
        if code not in SECTION_TITLES:
            code = "M"
        title = SECTION_TITLES[code]
        print(f"  + promote [{code}] {p['label']}  "
              f"(companies={p['companies']}, conf={p['avg_confidence']})")
        if args.dry_run:
            promoted += 1
            continue

        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO schema_fields
                     (id, section_code, section_id, section_title, label, data_type,
                      guidance, sources, founder_only, sort_order)
                   VALUES (%s,%s,%s,%s,%s,'text',%s,
                           ARRAY['web','document','transcript'], false, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (key[:60], code, title.lower().replace(" ", "_")[:40], title,
                 p["label"][:120], (p["top_rationale"] or "")[:400], next_order),
            )
            cur.execute(
                "UPDATE discovered_fields SET status='accepted' WHERE field_key=%s", (key,))
        next_order += 10
        promoted += 1

    print(f"\n{promoted} field(s) promoted into the schema"
          f"{' (dry run)' if args.dry_run else ''}")

    if promoted and not args.dry_run and not args.no_requeue:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET status='new', processed_at=NULL WHERE status='extracted'")
            n = cur.rowcount
        print(f"{n} document(s) requeued — the next extraction pass will fill the new columns.")


if __name__ == "__main__":
    main()
