#!/usr/bin/env python3
"""Applicability pass.

Some schema fields simply do not apply to a given company: a payments-software
firm has no "Manufacturing / Supply Chain Dependencies (if hardware)" and no
"Technology Readiness Level"; a bootstrapped company has no "Prior Funding Rounds".
Leaving them blank makes a complete profile look incomplete and sends the
gap-filler hunting for facts that cannot exist.

This pass asks the model, per empty field, whether the field is APPLICABLE to
this company given what the database already knows about it. Fields judged not
applicable are recorded as "N/A — <reason>" with source_type 'internal' and
collected_by 'applicability-pass', so they are auditable and reversible.

It never invents facts — it only marks absence as either "still to find" or
"cannot exist".

Usage:
    python applicability.py <slug> [--dry-run]
"""
import argparse
import os
import time

from openai import OpenAI
from psycopg.rows import dict_row

from common import pg_connect

PASS_ID = "applicability-pass"

SYSTEM = """You decide whether a data field is APPLICABLE to a specific company.

Reply with exactly one line:
  NA: <short reason>     -- the field cannot meaningfully apply to this company
  APPLICABLE             -- the field could apply; the data just hasn't been found yet

Be DECISIVE. An experienced analyst writes "N/A" on a form all the time — that is a
real answer, not a gap. Do not hedge: if the field would make no sense to ask about
this specific company, answer NA.

Rules of thumb:
- Hardware / manufacturing / supply-chain fields → NA for pure software or services firms.
- Technology Readiness Level (TRL) → NA for anything that is not deep tech
  (space, biotech, semiconductors, robotics, materials). IT services and SaaS are NOT deep tech.
- Patents / trade secrets / proprietary datasets → NA for services or reseller businesses
  with no R&D output.
- Export control / sanctions / dual-use → NA for domestic services and business software.
- Funding rounds, cap table, investors, ESOP, down rounds, investor rights →
  NA for a bootstrapped company that has never raised outside capital.
- ARR/MRR, NRR, churn, CAC, LTV → NA for project/consulting revenue models (non-subscription).
- Regulatory licences/approvals → NA where the business is unregulated.
- TAM/SAM/SOM and market-sizing basis → NA for a small services firm with no such analysis.
Answer APPLICABLE only when the field genuinely fits this company and the data merely
happens to be unavailable.

FORMAT: think briefly if you must, but your FINAL LINE must be exactly one of:
NA: <short reason>
APPLICABLE"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = pg_connect(row_factory=dict_row)
    conn.autocommit = True
    from nvidia_client import get_client
    client = get_client()
    model = os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")

    with conn.cursor() as cur:
        cur.execute("SELECT id, display_name FROM companies WHERE slug = %s", (args.slug,))
        comp = cur.fetchone()
        if not comp:
            raise SystemExit(f"company '{args.slug}' not found")

        # what we already know — the context for judging applicability
        cur.execute(
            """SELECT sf.label, fv.value_text FROM field_values fv
               JOIN schema_fields sf ON sf.id = fv.field_id
               WHERE fv.company_id = %s AND fv.is_current
                 AND sf.section_code IN ('B','C','D','E')
               ORDER BY sf.sort_order LIMIT 15""",
            (comp["id"],),
        )
        profile = "\n".join(f"- {r['label']}: {r['value_text'][:160]}" for r in cur.fetchall())

        cur.execute(
            """SELECT sf.id, sf.label, sf.guidance, sf.section_title
               FROM schema_fields sf
               WHERE NOT EXISTS (
                   SELECT 1 FROM field_values fv
                   WHERE fv.company_id = %s AND fv.field_id = sf.id AND fv.is_current)
                 AND sf.section_code NOT IN ('A','N')   -- internal process fields: not our call
               ORDER BY sf.sort_order""",
            (comp["id"],),
        )
        gaps = cur.fetchall()

    print(f"{comp['display_name']}: judging applicability of {len(gaps)} empty field(s)")
    na_count = 0

    for g in gaps:
        prompt = (
            f"COMPANY: {comp['display_name']}\n\nWHAT WE KNOW:\n{profile}\n\n"
            f"FIELD: {g['label']} (section: {g['section_title']})\n"
            f"FIELD GUIDANCE: {g['guidance'] or '—'}\n\n"
            "Is this field applicable to this company?"
        )
        answer = None
        for attempt in range(5):                       # backoff on 429 rate limits
            try:
                # Nemotron reasons out loud before answering; 60 tokens truncated the
                # reply before the verdict ever appeared (every run reported 0 N/A).
                resp = client.chat.completions.create(
                    model=model, temperature=0.0, max_tokens=500,
                    messages=[{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": prompt}],
                )
                answer = (resp.choices[0].message.content or "").strip()
                break
            except Exception as exc:
                if "429" in str(exc) or "Too Many Requests" in str(exc):
                    wait = 15 * (attempt + 1)
                    print(f"    rate-limited, waiting {wait}s…", flush=True)
                    time.sleep(wait)
                    continue
                print(f"  ! {g['label']}: judge failed ({exc})")
                break
        if answer is None:
            continue
        time.sleep(1.5)                                # gentle pacing between fields

        # Nemotron often narrates before answering, so don't rely on the first token.
        # Read the LAST non-empty line and look for a clear NA / APPLICABLE signal.
        lines = [l.strip() for l in answer.splitlines() if l.strip()]
        tail = lines[-1] if lines else ""
        up = tail.upper()
        is_na = (up.startswith("NA") or up.startswith("N/A")
                 or "NOT APPLICABLE" in up
                 or (": " in tail and up.split(":", 1)[0].strip() in ("NA", "N/A")))
        if not is_na and "APPLICABLE" not in up:
            # no clear verdict in the last line — scan the whole reply as a fallback
            full = answer.upper()
            is_na = ("NOT APPLICABLE" in full or "\nNA:" in full or full.startswith("NA"))
        if not is_na:
            continue

        reason = tail.split(":", 1)[1].strip() if ":" in tail else "not applicable to this company"
        na_count += 1
        print(f"  N/A  {g['label']} — {reason[:80]}")
        if args.dry_run:
            continue
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO field_values
                     (company_id, field_id, value_text, confidence, source_type,
                      source_url, collected_by, version, status)
                   VALUES (%s, %s, %s, 0.9, 'internal', 'assessment://applicability',
                           %s, 1, 'unverified')""",
                (comp["id"], g["id"], f"N/A — {reason}"[:400], PASS_ID),
            )

    print(f"\n{na_count} field(s) marked N/A{' (dry run)' if args.dry_run else ''}; "
          f"{len(gaps) - na_count} remain genuinely open")


if __name__ == "__main__":
    main()
