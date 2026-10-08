#!/usr/bin/env python3
"""Reconcile existing conflicted facts using the equivalence judge + source tiers.

For every current fact with status 'conflicted', compare it against its most
recent non-rejected previous version:
  judge says SAME / one-is-more-precise -> the agreeing (more precise) value wins,
                                           marked 'verified' (two sources agreed)
  genuinely DIFFERENT                   -> better source tier wins ('unverified');
                                           equal tiers stay 'conflicted' for a human
Run once after upgrading the validator; safe to re-run anytime.

Usage:  python reconcile.py [--dry-run]
"""
import argparse
import os
from urllib.parse import urlparse

from openai import OpenAI
from psycopg.rows import dict_row

from common import pg_connect
from quality import judge_equivalence, norm, source_tier


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = pg_connect(row_factory=dict_row)
    conn.autocommit = True  # each transaction() block commits on exit
    client = OpenAI(
        base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        api_key=os.environ["NVIDIA_API_KEY"],
    )
    model = os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")

    with conn.cursor() as cur:
        cur.execute(
            """SELECT fv.id, fv.company_id, fv.field_id, fv.value_text, fv.source_url,
                      fv.confidence, sf.label, c.website, c.slug,
                      prev.id AS prev_id, prev.value_text AS prev_value,
                      prev.source_url AS prev_source, prev.confidence AS prev_confidence
               FROM field_values fv
               JOIN schema_fields sf ON sf.id = fv.field_id
               JOIN companies c ON c.id = fv.company_id
               LEFT JOIN LATERAL (
                   SELECT id, value_text, source_url, confidence FROM field_values p
                   WHERE p.company_id = fv.company_id AND p.field_id = fv.field_id
                     AND p.version < fv.version AND p.status <> 'rejected'
                   ORDER BY p.version DESC LIMIT 1
               ) prev ON true
               WHERE fv.is_current AND fv.status = 'conflicted'
               ORDER BY c.slug, sf.sort_order"""
        )
        items = cur.fetchall()

    print(f"reconciling {len(items)} conflicted fact(s){' (dry run)' if args.dry_run else ''}")
    tally = {}

    for it in items:
        own = urlparse(it["website"] or "").netloc.lower().removeprefix("www.")

        if it["prev_id"] is None:
            outcome = "orphan->unverified"
            if not args.dry_run:
                with conn.transaction(), conn.cursor() as cur:
                    cur.execute("UPDATE field_values SET status='unverified' WHERE id=%s",
                                (it["id"],))
        else:
            if norm(it["value_text"]) == norm(it["prev_value"]):
                verdict = "SAME"
            else:
                # A = current, B = previous
                verdict = judge_equivalence(client, model, it["label"],
                                            it["value_text"], it["prev_value"])
            if verdict in ("SAME", "A_MORE_PRECISE"):
                outcome = "agree->current verified"
                if not args.dry_run:
                    with conn.transaction(), conn.cursor() as cur:
                        cur.execute(
                            """UPDATE field_values SET status='verified',
                               verified_by='cross-validator-reconcile', verified_at=now(),
                               confidence=GREATEST(confidence,%s) WHERE id=%s""",
                            (it["prev_confidence"] or 0, it["id"]))
            elif verdict == "B_MORE_PRECISE":
                outcome = "agree->previous (more precise) restored verified"
                if not args.dry_run:
                    with conn.transaction(), conn.cursor() as cur:
                        cur.execute("UPDATE field_values SET is_current=false, "
                                    "status='unverified' WHERE id=%s", (it["id"],))
                        cur.execute(
                            """UPDATE field_values SET is_current=true, status='verified',
                               verified_by='cross-validator-reconcile', verified_at=now()
                               WHERE id=%s""", (it["prev_id"],))
            else:  # DIFFERENT — source quality decides
                t_cur = source_tier(it["source_url"] or "", own)
                t_prev = source_tier(it["prev_source"] or "", own)
                if t_cur < t_prev:
                    outcome = "differ->current wins (better source)"
                    if not args.dry_run:
                        with conn.transaction(), conn.cursor() as cur:
                            cur.execute("UPDATE field_values SET status='unverified' "
                                        "WHERE id=%s", (it["id"],))
                elif t_prev < t_cur:
                    outcome = "differ->previous restored (better source)"
                    if not args.dry_run:
                        with conn.transaction(), conn.cursor() as cur:
                            cur.execute("UPDATE field_values SET is_current=false, "
                                        "status='conflicted' WHERE id=%s", (it["id"],))
                            cur.execute("UPDATE field_values SET is_current=true, "
                                        "status='unverified' WHERE id=%s", (it["prev_id"],))
                else:
                    outcome = "differ->left for analyst (equal tiers)"

        tally[outcome] = tally.get(outcome, 0) + 1
        print(f"  [{it['slug']}] {it['label'][:45]}: {outcome}")

    print("\nsummary:")
    for k, v in sorted(tally.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
