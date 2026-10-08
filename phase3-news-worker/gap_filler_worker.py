#!/usr/bin/env python3
"""Gap-Filler Worker. Collect only — but AIMED, not generic.

Generic crawling hopes a page happens to mention a fact. This worker instead
reads a company's ACTUAL missing fields (from the gap list the dashboard shows),
builds a targeted search query per gap, and fetches results. The existing
extractor then reads those pages knowing exactly what it's hunting for, because
the search itself was already field-specific — this is what raises fill rate
without extracting fields we don't have or fabricating anything.

Usage:
    python gap_filler_worker.py <slug> [--max-fields N]
"""
import argparse
import hashlib
import sys
import time
from io import BytesIO
from urllib.parse import urlparse

import os

import requests

from common import BUCKET, minio_client, pg_connect

WORKER_ID = "gap-filler-worker"
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NemoClawGapFiller/0.1)"}

# Per-field query recipes. A generic '"Company" <label>' search rarely surfaces the
# right page; these phrasings mirror how the fact is actually reported in the wild.
FIELD_QUERIES = {
    "tam_sam_som": '{name} market size TAM addressable market opportunity billion',
    "market_sizing_basis": '{name} industry market report analyst estimate size',
    "direct_competitors": '{name} competitors alternatives vs comparison',
    "indirect_competitors_substitutes": '{name} alternatives substitutes competing solutions',
    "product_roadmap_next_12m": '{name} roadmap upcoming launch plans next year announcement',
    "geographies_targeted_next_12_24m": '{name} expansion new markets international plans',
    "business_model_pivots": '{name} pivot changed business model strategy shift history',
    "technology_readiness_level": '{name} technology stage prototype pilot commercial deployment',
    "third_party_licensed_tech": '{name} technology partners licensed integrations vendors',
    "key_technical_risks": '{name} technical challenges risks limitations scaling',
    "manufacturing_supply_chain": '{name} manufacturing facility supply chain production partners',
    "corporate_structure": '{name} subsidiaries group structure holding company entities',
    "channel_mix": '{name} sales channels partners resellers direct self-serve distribution',
    "key_person_dependency": '{name} founder key executives dependency succession',
    "patents": '{name} patents granted filed IP portfolio',
    "regulatory_approvals_needed": '{name} regulatory approval license RBI OR FDA OR CE certification',
    "rd_team_size_structure": '{name} engineering team size R&D headcount developers',
    "advisory_board_directors": '{name} board of directors advisors governance',
    "key_management_team": '{name} leadership team CXO executives management',
    "total_headcount_split": '{name} employees headcount team size staff',
    "awards_certifications": '{name} certification SOC2 ISO award recognition Gartner',
    "litigation": '{name} lawsuit litigation legal case court',
    "data_privacy_compliance": '{name} GDPR DPDP data privacy security compliance',
    "export_control_sanctions": '{name} export control sanctions dual-use restrictions',
    "revenue_last_3fy": '{name} FY25 OR FY24 revenue crore financial results',
    "revenue_growth_yoy": '{name} revenue growth percent YoY FY25',
    "gross_margin": '{name} gross margin profit margin percentage',
    "ebitda_operating_margin": '{name} EBITDA operating margin loss profitability',
    "arr_mrr": '{name} ARR annual recurring revenue run rate',
    "outstanding_debt_convertibles": '{name} debt borrowings convertible notes charges',
    "audited_financials": '{name} auditor audited financial statements annual report',
    "nrr_gross_retention": '{name} retention rate churn customers',
    "total_customers_users": '{name} customers users merchants businesses served number',
    "market_tailwinds_headwinds": '{name} industry growth drivers challenges outlook',
    "barriers_to_entry": '{name} moat barriers to entry competitive advantage',
    "esop_pool_vesting": '{name} ESOP employee stock options pool buyback',
    "prior_funding_rounds": '{name} funding rounds series A B C investors raised',
    "current_investors_board": '{name} investors backers shareholders',
    "total_equity_raised": '{name} total funding raised to date',
}

SECTION_HINTS = {
    "B": "company profile about",
    "C": "product overview business model",
    "D": "technology patents R&D",
    "E": "market size competitors",
    "I": "founders leadership team",
    "J": "customers users traction metrics certifications",
    "K": "compliance litigation regulatory",
    "L": "customers revenue geography",
}


from websearch import search as web_search


def tavily(query: str, max_results=4):
    return web_search(query, max_results)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--max-fields", type=int, default=10)
    args = ap.parse_args()
    slug = args.slug

    conn = pg_connect()
    conn.autocommit = True
    minio = minio_client()

    with conn.cursor() as cur:
        cur.execute("SELECT id, display_name, website FROM companies WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if not row:
            sys.exit(f"company '{slug}' not found")
        company_id, display_name, website = row
        own_domain = urlparse(website or "").netloc.lower().removeprefix("www.")

        # web-fillable, non-founder-only fields with no current value = real gaps
        cur.execute(
            """SELECT sf.id, sf.label, sf.section_code, sf.guidance
               FROM schema_fields sf
               WHERE 'web' = ANY(sf.sources) AND NOT sf.founder_only
                 AND NOT EXISTS (
                     SELECT 1 FROM field_values fv
                     WHERE fv.company_id = %s AND fv.field_id = sf.id AND fv.is_current)
               ORDER BY sf.sort_order LIMIT %s""",
            (company_id, args.max_fields),
        )
        gaps = cur.fetchall()
        cur.execute(
            "INSERT INTO workers (id, description) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (WORKER_ID, "Targeted search per missing schema field; collect only."),
        )
        cur.execute(
            "INSERT INTO worker_runs (worker_id, company_id) VALUES (%s, %s) RETURNING id",
            (WORKER_ID, company_id),
        )
        run_id = cur.fetchone()[0]

    print(f"{len(gaps)} gap field(s) targeted for {display_name}")
    stats = {"queries": 0, "fetched": 0, "stored": 0, "skipped_unchanged": 0,
             "snippet_fallback": 0, "failed": 0}
    seen: set[str] = set()

    for field_id, label, section, guidance in gaps:
        recipe = FIELD_QUERIES.get(field_id)
        if recipe:
            query = recipe.format(name=f'"{display_name}"')
        else:
            hint = SECTION_HINTS.get(section, "")
            query = f'"{display_name}" {label} {hint}'.strip()
        try:
            results = tavily(query)
            stats["queries"] += 1
        except Exception as exc:
            print(f"  ! search failed for '{label}': {exc}")
            stats["failed"] += 1
            continue

        for r in results[:3]:  # top 3 results per gap
            url = (r.get("url") or "").split("#")[0]
            if not url or url in seen:
                continue
            seen.add(url)
            body = None
            try:
                resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
                resp.raise_for_status()
                body = resp.content
                stats["fetched"] += 1
            except Exception:
                snippet = (r.get("content") or "").strip()
                if snippet:
                    body = f"{r.get('title', '')}\n\n{snippet}\n\n(source: {url})".encode()
                    stats["snippet_fallback"] += 1
                else:
                    stats["failed"] += 1
                    continue

            digest = hashlib.sha256(body).hexdigest()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM documents WHERE company_id = %s AND source_url = %s AND content_hash = %s",
                    (company_id, url, digest),
                )
                if cur.fetchone():
                    stats["skipped_unchanged"] += 1
                    continue
                key = f"{slug}/gapfill/{digest[:12]}_{field_id}"
                minio.put_object(BUCKET, key, BytesIO(body), len(body))
                cur.execute(
                    """INSERT INTO documents
                         (company_id, collected_by, doc_type, title, source_url, object_key, content_hash)
                       VALUES (%s, %s, 'webpage', %s, %s, %s, %s)""",
                    (company_id, WORKER_ID, f"Gap-fill for '{label}': {r.get('title', '')[:200]}",
                     url, key, digest),
                )
                stats["stored"] += 1
                print(f"  + [{label}] {url}")
            time.sleep(0.6)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE worker_runs SET finished_at = now(), status = 'succeeded', stats = %s::jsonb WHERE id = %s",
            (__import__("json").dumps(stats), run_id),
        )
    print(f"run complete: {stats}")


if __name__ == "__main__":
    main()
