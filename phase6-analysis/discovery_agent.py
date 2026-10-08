#!/usr/bin/env python3
"""Discovery Agent — proposes NEW fields beyond the 103-field intake schema.

Reads what we've actually collected for a company (facts + a sample of raw
document text), and asks: "what materially useful, recurring information is
present here that our schema has no column for?"

It never writes facts. It only writes PROPOSALS to discovered_fields, each with
a rationale, an observed example, and a source — so a human can accept or reject.
Proposals seen across several companies rise to the top (discovered_field_rollup),
which is how the schema should grow: from evidence, not guesswork.

Usage:
    python discovery_agent.py <slug> [--max-docs 12]
    python discovery_agent.py --all
"""
import argparse
import json
import os
import re
import time

from openai import OpenAI
from psycopg.rows import dict_row

from common import BUCKET, minio_client, pg_connect

AGENT_ID = "discovery-agent"
MAX_DOC_CHARS = 4000

SYSTEM = """You are a senior investment-banking analyst reviewing what a data platform
has collected about a company. The platform tracks 103 standard fields. Your job is to
spot MATERIALLY USEFUL information that is present in the evidence but has NO column
in the existing schema — information a deal team would want captured systematically.

Rules:
- Propose ONLY fields supported by the evidence shown. Never invent a field "because
  it would be nice"; every proposal must cite something actually observed.
- Do NOT propose anything that duplicates an existing field (list is given).
- Prefer fields that would recur across many companies, not one-off trivia.
- Think like diligence: unit economics, concentration, dependencies, regulatory
  exposure, operational KPIs, partnerships, capital structure nuances, ESG,
  cyber posture, key contracts, sector-specific metrics.

Output STRICT JSON only — an array, no prose, no markdown fences:
[{"field_key":"snake_case_id","label":"Human Label","section_hint":"B|C|D|E|F|G|H|I|J|K|L|NEW",
  "rationale":"why a deal team needs this, one sentence",
  "example_value":"the value observed for THIS company",
  "confidence":0.0-1.0}]
Return [] if the evidence suggests nothing new."""


def llm():
    from nvidia_client import get_client
    return get_client(timeout=300.0)


def parse_array(raw: str):
    raw = raw or ""
    # reasoning models wrap the JSON in prose and sometimes ```json fences; strip both.
    raw = raw.replace("```json", "```").split("```")[1] if "```" in raw else raw
    # take the LAST bracketed array (the answer usually follows the reasoning)
    matches = re.findall(r"\[.*?\]", raw, re.DOTALL)
    for candidate in reversed(matches or [raw]):
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            continue
    # last resort: greedy match
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            pass
    return []


def analyse_company(conn, minio, client, model, slug: str, max_docs: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id, display_name FROM companies WHERE slug = %s", (slug,))
        comp = cur.fetchone()
        if not comp:
            print(f"  ! company '{slug}' not found")
            return 0

        cur.execute("SELECT label FROM schema_fields ORDER BY sort_order")
        existing = [r["label"] for r in cur.fetchall()]

        cur.execute(
            """SELECT sf.label, fv.value_text FROM field_values fv
               JOIN schema_fields sf ON sf.id = fv.field_id
               WHERE fv.company_id = %s AND fv.is_current
               ORDER BY sf.sort_order""",
            (comp["id"],),
        )
        facts = [f"{r['label']}: {r['value_text'][:200]}" for r in cur.fetchall()]

        # a spread of the richest evidence: prefer filings/news over marketing pages
        cur.execute(
            """SELECT source_url, object_key, doc_type FROM documents
               WHERE company_id = %s AND status = 'extracted'
               ORDER BY CASE doc_type WHEN 'filing' THEN 1 WHEN 'transcript' THEN 2
                                      WHEN 'news' THEN 3 ELSE 4 END, fetched_at DESC
               LIMIT %s""",
            (comp["id"], max_docs),
        )
        docs = cur.fetchall()

    from bs4 import BeautifulSoup
    from io import BytesIO  # noqa: F401  (kept for parity with extractor helpers)

    excerpts = []
    for d in docs:
        try:
            body = minio.get_object(BUCKET, d["object_key"]).read()
            text = re.sub(r"\s+", " ",
                          BeautifulSoup(body, "html.parser").get_text(" ", strip=True))
            if len(text) > 300:
                excerpts.append(f"[{d['doc_type']}] {d['source_url']}\n{text[:MAX_DOC_CHARS]}")
        except Exception:
            continue

    if not facts and not excerpts:
        print(f"  · {slug}: no evidence yet")
        return 0

    user = (
        f"COMPANY: {comp['display_name']}\n\n"
        f"EXISTING SCHEMA FIELDS (do not duplicate these):\n{'; '.join(existing)}\n\n"
        f"FACTS ALREADY CAPTURED:\n" + "\n".join(facts[:60]) + "\n\n"
        f"RAW EVIDENCE EXCERPTS:\n" + "\n\n---\n\n".join(excerpts[:8])
    )

    raw = None
    for attempt in range(5):                       # backoff on 429 rate limits
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0.2, max_tokens=4000,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": user}],
            )
            raw = resp.choices[0].message.content
            break
        except Exception as exc:
            if "429" in str(exc) or "Too Many Requests" in str(exc):
                wait = 20 * (attempt + 1)
                print(f"    rate-limited, waiting {wait}s…", flush=True)
                time.sleep(wait)
                continue
            print(f"  ! {slug}: analysis failed ({exc})")
            return 0
    if raw is None:
        print(f"  ! {slug}: gave up after repeated rate limits")
        return 0
    proposals = parse_array(raw)
    if not proposals:
        print(f"  [debug] {slug}: {len(raw)} chars, no JSON array parsed. "
              f"head: {raw[:200]!r}  tail: {raw[-200:]!r}", flush=True)

    written = 0
    with conn.cursor() as cur:
        for p in proposals:
            if not isinstance(p, dict):
                continue
            key = re.sub(r"[^a-z0-9_]", "", str(p.get("field_key", "")).lower().replace(" ", "_"))
            label = str(p.get("label", "")).strip()
            rationale = str(p.get("rationale", "")).strip()
            if not key or not label or not rationale:
                continue
            try:
                conf = max(0.0, min(1.0, float(p.get("confidence", 0.5))))
            except (TypeError, ValueError):
                conf = 0.5
            cur.execute(
                """INSERT INTO discovered_fields
                     (company_id, field_key, label, section_hint, rationale,
                      example_value, confidence)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (company_id, field_key) DO UPDATE
                     SET rationale = EXCLUDED.rationale,
                         example_value = EXCLUDED.example_value,
                         confidence = GREATEST(discovered_fields.confidence, EXCLUDED.confidence)""",
                (comp["id"], key[:60], label[:120], str(p.get("section_hint", "NEW"))[:8],
                 rationale[:600], str(p.get("example_value", ""))[:400], conf),
            )
            written += 1
            print(f"  + [{slug}] {label} — {rationale[:70]}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max-docs", type=int, default=12)
    args = ap.parse_args()

    conn = pg_connect(row_factory=dict_row)
    conn.autocommit = True
    minio = minio_client()
    client = llm()
    model = os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")

    if args.all:
        with conn.cursor() as cur:
            cur.execute("SELECT slug FROM companies ORDER BY slug")
            slugs = [r["slug"] for r in cur.fetchall()]
    elif args.slug:
        slugs = [args.slug]
    else:
        raise SystemExit("give a slug or --all")

    total = 0
    for s in slugs:
        print(f"— analysing {s} …", flush=True)
        total += analyse_company(conn, minio, client, model, s, args.max_docs)

    print(f"\n{total} field proposal(s) written. Review them at /discoveries")


if __name__ == "__main__":
    main()
