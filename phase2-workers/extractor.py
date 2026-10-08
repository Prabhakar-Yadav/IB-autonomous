#!/usr/bin/env python3
"""Extraction Engine — clean → chunk → LLM extract → validate → store.

The LLM is never the source of truth. It only parses document text into
candidate facts; this engine validates every candidate against the schema
registry and writes survivors to the canonical database with provenance.

Usage:
    python extractor.py [--limit N]     # process up to N pending documents
"""
import argparse
import concurrent.futures
import json
import re
import signal
from io import BytesIO

from bs4 import BeautifulSoup
from minio import Minio  # noqa: F401 (type reference)
from openai import OpenAI
from psycopg.rows import dict_row
from pypdf import PdfReader

import os

from common import BUCKET, minio_client, pg_connect
from quality import judge_equivalence, norm, source_tier
from verifier import verify as llm_verify

CHUNK_CHARS = 8000
CHUNK_OVERLAP = 300
MAX_CHUNKS_PER_DOC = 3
ENGINE_ID = "extraction-engine"
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2)  # hard-deadline runner
DOC_DEADLINE_SEC = int(os.environ.get("DOC_DEADLINE_SEC", "420"))  # 7 min max per document


class DocTimeout(Exception):
    pass


def _doc_timeout(signum, frame):
    raise DocTimeout(f"document exceeded {DOC_DEADLINE_SEC}s wall clock")

SYSTEM_PROMPT = """You are the extraction engine of an investment-intelligence platform.
You receive one chunk of a company document plus a catalog of schema fields.
Extract ONLY facts explicitly stated in the text. Never infer, never estimate,
never summarize. If the chunk contains no catalog facts, return [].

CRITICAL OUTPUT CONTRACT — violating this destroys the pipeline:
- Your ENTIRE reply must be a single JSON array. It MUST start with '[' and end with ']'.
- NO reasoning, NO explanation, NO preamble, NO markdown fences, NO thinking out loud.
- Use ONLY field_id values copied verbatim from the FIELD CATALOG below. Never invent
  field names.

Format:
[{"field_id": "<id from catalog>", "value": "<faithful value as stated>",
  "confidence": <0.0-1.0>, "quote": "<exact phrase copied word-for-word from the text>"}]

The "quote" MUST be copied verbatim from the DOCUMENT TEXT — it is checked against the
source. If you cannot copy a real supporting phrase from the text, DO NOT output that fact.
Never paraphrase or invent a quote; a fact without a real quote is a hallucination.

Rules:
- field_id MUST be one of the catalog ids; anything else is discarded.
- confidence = how explicit and unambiguous the statement is.
- Marketing superlatives ("world's leading") are not facts — skip them.
- Keep numbers exactly as written, with units/currency.
- NEVER treat copyright years, page footers, promo/offer dates, or article
  publication dates as company facts (a footer "© 2024" is NOT an incorporation date).
- The text may mention many companies (competitors, customers, partners).
  Extract facts ONLY about the TARGET COMPANY named in the request.
- BE MAXIMALLY PRECISE. If the text states an exact date, a full address, an exact
  number, or an identifier, record it in full — never round a date to a year, never
  summarize a list into a category. "24 Dec 2013" is correct; "2013" is a failure.
- Registry/filing pages (CIN, incorporation date, directors, paid-up capital, registered
  office, revenue, profit, auditor) are structured official records: extract those values
  verbatim and with high confidence.
- MULTI-ENTITY RULE: large companies have several legal entities (e.g. "X Software Private
  Limited", "X Payments Private Limited", "X Foundation"). Facts must describe the PRIMARY
  OPERATING entity — normally the OLDEST one and/or the one whose name matches the main
  brand ("X Software/Technologies Private Limited"), NOT a recent subsidiary, NOT a
  foundation/CSR arm, NOT a regional/IFSC arm. Always append the entity name to the value,
  e.g. "24 Dec 2013 (Razorpay Software Private Limited, CIN U72200KA2013PTC097389)".
  If the page is ONLY about a minor subsidiary, return [] rather than a misleading fact."""


def llm():
    """Rotating client across every configured NVIDIA key (handles 429s by switching keys)."""
    from nvidia_client import get_client
    return get_client(timeout=300.0)


MAX_DOC_BYTES = 3_000_000     # parse at most 3 MB; pathological pages hang the parser


def to_text(body: bytes, doc_type: str) -> str:
    if len(body) > MAX_DOC_BYTES:
        body = body[:MAX_DOC_BYTES]
    if doc_type == "pdf":
        reader = PdfReader(BytesIO(body))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:40])
    else:
        # lxml is far faster and far more robust on malformed HTML than html.parser,
        # which can go quadratic and burn CPU indefinitely on junk markup.
        try:
            text = BeautifulSoup(body, "lxml").get_text(" ", strip=True)
        except Exception:
            text = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
    return re.sub(r"[ \t\r\n\f\v]+", " ", text).strip()


def to_text_guarded(body: bytes, doc_type: str, url: str) -> str:
    """Parse in the MAIN thread so the per-document SIGALRM deadline can interrupt it.

    Do not push this into a worker thread: a CPU-bound parse holds the GIL, starves
    the main thread, and the signal handler never gets to run — which is exactly how
    a single malformed page burned 96% CPU for hours.
    """
    return to_text(body, doc_type)


def firecrawl_scrape(url: str) -> str:
    """Render a JavaScript page to clean text via Firecrawl (handles SPA/anti-bot).
    Recovers pages our raw fetcher sees as near-empty (e.g. razorpay.com/pricing)."""
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key or not url.startswith("http"):
        return ""
    try:
        import requests
        resp = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {key}"},
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            timeout=45,
        )
        resp.raise_for_status()
        md = ((resp.json().get("data") or {}).get("markdown")) or ""
        return re.sub(r"\s+", " ", md).strip()
    except Exception:
        return ""


def tavily_extract(url: str) -> str:
    """Secondary renderer fallback if Firecrawl is unavailable/unset."""
    key = os.environ.get("TAVILY_API_KEY")
    if not key or not url.startswith("http"):
        return ""
    try:
        import requests
        resp = requests.post(
            "https://api.tavily.com/extract",
            headers={"Authorization": f"Bearer {key}"},
            json={"urls": [url]}, timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        return re.sub(r"\s+", " ", (results[0].get("raw_content") or "")).strip() if results else ""
    except Exception:
        return ""


def _grounded(quote: str, chunk_norm: str) -> bool:
    """Anti-hallucination guard: the model's supporting quote must actually appear in
    the source text. Prevents synthetic/invented facts — a fact with no real quote is dropped."""
    q = norm(quote)
    if len(q) < 8:
        return False           # too short to verify → reject
    if q in chunk_norm:
        return True
    # allow minor rewording: require ≥70% of the quote's words present in the source
    words = [w for w in q.split() if len(w) > 2]
    if not words:
        return False
    hits = sum(1 for w in words if w in chunk_norm)
    return hits / len(words) >= 0.70


def chunks(text: str):
    start, n = 0, 0
    while start < len(text) and n < MAX_CHUNKS_PER_DOC:
        yield text[start:start + CHUNK_CHARS]
        start += CHUNK_CHARS - CHUNK_OVERLAP
        n += 1


def parse_json_array(raw: str):
    """Tolerant parse: reasoning models often wrap the array in prose or fences."""
    if not raw:
        return []
    raw = raw.strip()
    # strip <think>…</think> style blocks some reasoning models emit
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"```(?:json)?|```", "", raw)
    candidates = re.findall(r"\[[^\[\]]*(?:\{.*?\}[^\[\]]*)*\]", raw, re.DOTALL)
    if not candidates:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        candidates = [m.group(0)] if m else []
    for cand in reversed(candidates):          # last complete array wins
        try:
            data = json.loads(cand)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            continue
    # last resort: salvage individual objects
    objs = []
    for m in re.finditer(r"\{[^{}]*\"field_id\"[^{}]*\}", raw, re.DOTALL):
        try:
            objs.append(json.loads(m.group(0)))
        except json.JSONDecodeError:
            pass
    return objs


def _call(client: OpenAI, model: str, messages, max_tokens: int):
    """Nemotron 3 is a reasoning model: without this it narrates instead of
    emitting JSON. Ask for JSON mode where supported and disable thinking."""
    for extra in ({"chat_template_kwargs": {"thinking": False}}, {}):
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0.0, max_tokens=max_tokens,
                messages=messages, extra_body=extra or None,
            )
            msg = resp.choices[0].message
            # some reasoning models (e.g. nemotron-nano-9b-v2) emit the actual
            # continuation into reasoning_content instead of content, especially
            # when the prompt is prefilled to continue mid-structure (our "["
            # assistant-turn trick). Fall back to it rather than reporting "no facts".
            if msg.content:
                return msg.content
            reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
            if reasoning:
                return reasoning
            return ""
        except Exception:
            continue
    return ""


def extract_chunk(client: OpenAI, model: str, catalog_json: str, company_name: str,
                  source_url: str, chunk: str):
    user = (f"TARGET COMPANY: {company_name}\n\n"
            f"FIELD CATALOG:\n{catalog_json}\n\n"
            f"SOURCE URL: {source_url}\n\nDOCUMENT TEXT:\n{chunk}\n\n"
            "Reply with the JSON array only, starting with '['.")
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
                # prefill: forces the reply to begin inside the array
                {"role": "assistant", "content": "["}]
    raw = _call(client, model, messages, 3000)
    facts = parse_json_array("[" + raw if not raw.lstrip().startswith("[") else raw)
    if not facts:
        # one terse retry — some chunks send the model into narration
        retry = [{"role": "system", "content":
                  "Output ONLY a JSON array of extracted facts. No prose. If none, output []."},
                 {"role": "user", "content": user}]
        facts = parse_json_array(_call(client, model, retry, 3000))
    # anti-hallucination: every fact's supporting quote must exist in the source text
    chunk_norm = norm(chunk)
    return [c for c in facts
            if isinstance(c, dict) and _grounded(str(c.get("quote", "")), chunk_norm)]


def validate(candidates, catalog: dict):
    """Keep only well-formed facts for known, web-allowed fields; best confidence wins."""
    best: dict[str, dict] = {}
    for c in candidates:
        if not isinstance(c, dict):
            continue
        fid = c.get("field_id")
        value = str(c.get("value", "")).strip()
        try:
            conf = float(c.get("confidence", 0))
        except (TypeError, ValueError):
            continue
        if fid not in catalog or not value or not (0.0 <= conf <= 1.0):
            continue
        if fid not in best or conf > best[fid]["confidence"]:
            best[fid] = {"field_id": fid, "value": value[:4000], "confidence": round(conf, 3)}
    return list(best.values())


def _insert(cur, company_id, fact, doc, version, source_type="web",
            status="unverified", is_current=True):
    cur.execute(
        """INSERT INTO field_values
             (company_id, field_id, value_text, confidence, source_type,
              source_url, document_id, collected_by, version, status, is_current)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (company_id, fact["field_id"], fact["value"], fact["confidence"], source_type,
         doc["source_url"], doc["id"], ENGINE_ID, version, status, is_current),
    )


def _verify_current(cur, current_id, confidence):
    cur.execute(
        """UPDATE field_values
           SET status = 'verified', verified_by = 'cross-validator',
               verified_at = now(), confidence = GREATEST(confidence, %s)
           WHERE id = %s""",
        (confidence, current_id),
    )


# distinct independent sources required to VERIFY a value, keyed by the best (lowest)
# tier among the agreeing sources:  tier1→1, tier2→1, tier3→2, tier4→4 (more than three)
REQUIRED_SOURCES = {1: 1, 2: 1, 3: 2, 4: 4}


def _agreeing_sources(cur, company_id, field_id, value, own_domain):
    """Distinct source URLs across history that assert this same value (not rejected).
    Returns (count, best_tier)."""
    cur.execute("""SELECT value_text, source_url FROM field_values
                   WHERE company_id=%s AND field_id=%s AND status<>'rejected'""",
                (company_id, field_id))
    target = norm(value)
    urls = {r["source_url"] for r in cur.fetchall()
            if r["source_url"] and norm(r["value_text"]) == target}
    if not urls:
        return 0, 4
    return len(urls), min(source_tier(u, own_domain) for u in urls)


def write_fact(cur, company_id, fact, doc, source_type="web",
               client=None, model="", own_domain="", label="") -> str:
    """Cross-validating write with source tiers + LLM equivalence judge.

    Textually equal + independent source        -> verify.
    Reworded-but-same (judge: SAME/MORE_PRECISE) -> verify, keep the precise wording.
    Same source changed its statement            -> supersede.
    Genuine disagreement                         -> better tier wins; worse tier only
                                                    files a dissent; equal tiers conflict.
    Verified facts are never overturned by an equal-or-worse source.
    """
    cur.execute(
        """SELECT id, value_text, version, source_url, status FROM field_values
           WHERE company_id = %s AND field_id = %s AND is_current""",
        (company_id, fact["field_id"]),
    )
    current = cur.fetchone()
    if current is None:
        # first source: verified only if its tier already meets the threshold
        # (tier1/tier2 need just 1 source; tier3 needs 2; tier4 needs 4)
        t = source_tier(doc["source_url"] or "", own_domain)
        status = "verified" if REQUIRED_SOURCES.get(t, 4) <= 1 else "unverified"
        _insert(cur, company_id, fact, doc, version=1, source_type=source_type, status=status)
        if status == "verified":
            cur.execute("UPDATE field_values SET verified_by='authoritative-source', "
                        "verified_at=now() WHERE company_id=%s AND field_id=%s AND is_current",
                        (company_id, fact["field_id"]))
        return "authoritative" if status == "verified" else "written"

    same_source = (current["source_url"] or "") == (doc["source_url"] or "")

    if norm(current["value_text"]) == norm(fact["value"]):
        if not same_source:
            # record this confirming source in history, then re-check the threshold
            _insert(cur, company_id, fact, doc, version=current["version"],
                    source_type=source_type, status="unverified", is_current=False)
            n, best = _agreeing_sources(cur, company_id, fact["field_id"],
                                        fact["value"], own_domain)
            if current["status"] != "verified" and n >= REQUIRED_SOURCES.get(best, 4):
                _verify_current(cur, current["id"], fact["confidence"])
                return "verified"
        return "unchanged"

    if same_source:
        cur.execute("UPDATE field_values SET is_current = false WHERE id = %s", (current["id"],))
        _insert(cur, company_id, fact, doc, version=current["version"] + 1,
                source_type=source_type)
        return "updated"

    # source tiers, computed up front so we can skip pointless LLM judge calls
    tier_new = source_tier(doc["source_url"] or "", own_domain)
    tier_cur = source_tier(current["source_url"] or "", own_domain)

    # FAST PATH: a weak source (tier >=3: aggregators/blogs/social) disagreeing with an
    # already-better-sourced fact is just noise. File the dissent WITHOUT an API judge call.
    # This is the main cost/latency saver — junk pages produced dozens of judge calls each.
    if tier_new >= 3 and tier_new > tier_cur:
        _insert(cur, company_id, fact, doc, version=current["version"] + 1,
                source_type=source_type, status="conflicted", is_current=False)
        return "dissent-filed"

    # texts differ, sources differ — LLM-as-a-Verifier (arXiv:2607.05391):
    # decomposed criteria + logit-expectation scores, layered over the tier rules.
    verdict = "DIFFERENT"
    if client is not None:
        vres = llm_verify(client, model, label or fact["field_id"],
                          current["value_text"], fact["value"])
        verdict = vres["verdict"]
        if vres["criteria"]:
            print(f"      verifier {vres['verdict']} score={vres['score']} {vres['criteria']}",
                  flush=True)

    if verdict in ("SAME", "A_MORE_PRECISE"):
        _verify_current(cur, current["id"], fact["confidence"])
        return "verified"
    if verdict == "B_MORE_PRECISE":
        # agreement, incoming wording is richer — it becomes the verified current
        cur.execute("UPDATE field_values SET is_current = false WHERE id = %s", (current["id"],))
        _insert(cur, company_id, fact, doc, version=current["version"] + 1,
                source_type=source_type, status="verified", is_current=True)
        cur.execute(
            "UPDATE field_values SET verified_by = 'cross-validator', verified_at = now() "
            "WHERE company_id = %s AND field_id = %s AND is_current",
            (company_id, fact["field_id"]),
        )
        return "verified"

    # genuine disagreement — source quality decides (tiers already computed above)
    if current["status"] == "verified" and tier_new >= tier_cur:
        _insert(cur, company_id, fact, doc, version=current["version"] + 1,
                source_type=source_type, status="conflicted", is_current=False)
        return "dissent-filed"
    if tier_new > tier_cur:
        # worse source disagreeing with a better one: dissent only
        _insert(cur, company_id, fact, doc, version=current["version"] + 1,
                source_type=source_type, status="conflicted", is_current=False)
        return "dissent-filed"
    if tier_new < tier_cur:
        # better source wins cleanly; old value superseded
        cur.execute("UPDATE field_values SET is_current = false WHERE id = %s", (current["id"],))
        _insert(cur, company_id, fact, doc, version=current["version"] + 1,
                source_type=source_type)
        return "displaced"
    # equal tiers — newer wins but flagged for human review
    cur.execute("UPDATE field_values SET is_current = false, status = 'conflicted' WHERE id = %s",
                (current["id"],))
    _insert(cur, company_id, fact, doc, version=current["version"] + 1,
            source_type=source_type, status="conflicted")
    return "conflicted"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    conn = pg_connect(row_factory=dict_row)
    minio = minio_client()
    client = llm()
    model = os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")

    # three catalogs, by how trustworthy the document class is:
    #  web        — public pages/news: public, non-founder-only fields only
    #  filing     — official registry/statutory filings (MCA mirrors, patent offices).
    #               Indian private companies must file financial statements, so these
    #               legitimately carry revenue/margins/debt/auditor — allow those, but
    #               still never the genuinely private deal data (cap table %, ESOP terms,
    #               current raise, unit economics).
    #  transcript — trusted internal source: the full form
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, label, guidance, sources, founder_only FROM schema_fields ORDER BY sort_order"
        )
        rows = cur.fetchall()
    web_catalog = {r["id"]: r for r in rows if "web" in r["sources"] and not r["founder_only"]}
    # transcripts may fill everything incl. Section A (the interaction being transcribed);
    # web/filing docs get every COMPANY fact but never the internal process sections
    # (A = our own meeting log, N = our follow-up actions — not company facts at all)
    full_catalog = {r["id"]: r for r in rows}
    company_facts_catalog = {r["id"]: r for r in rows
                             if not r["id"].startswith("interaction_")
                             and r["id"] not in ("documents_requested", "documents_owed_back",
                                                 "next_meeting", "internal_owner",
                                                 "mandate_decision_target_date")}

    FILING_EXTRA_FIELDS = {
        # statutory financials published in MCA/registry filings
        "revenue_last_3fy", "revenue_current_fy_forecast", "revenue_growth_yoy",
        "gross_margin", "ebitda_operating_margin", "key_cost_line_items",
        "audited_financials", "accounting_standard", "outstanding_debt_convertibles",
        "related_party_transactions", "cap_table_summary",
    }
    filing_catalog = dict(web_catalog)
    filing_catalog.update({r["id"]: r for r in rows if r["id"] in FILING_EXTRA_FIELDS})

    def catalog_to_json(cat):
        return json.dumps(
            [{"field_id": r["id"], "label": r["label"], "guidance": r["guidance"]}
             for r in cat.values()], ensure_ascii=False)

    web_catalog_json = catalog_to_json(web_catalog)
    filing_catalog_json = catalog_to_json(filing_catalog)
    full_catalog_json = catalog_to_json(full_catalog)
    company_facts_catalog_json = catalog_to_json(company_facts_catalog)

    with conn.cursor() as cur:
        cur.execute(
            """UPDATE documents SET status = 'processing'
               WHERE id IN (SELECT id FROM documents WHERE status = 'new'
                            ORDER BY fetched_at LIMIT %s)
               RETURNING id, company_id, doc_type, source_url, object_key""",
            (args.limit,),
        )
        docs = cur.fetchall()
        cur.execute("SELECT id, display_name, website FROM companies")
        companies_by_id = {r["id"]: r for r in cur.fetchall()}
    conn.commit()
    print(f"processing {len(docs)} document(s) with model {model}")

    for i, doc in enumerate(docs, 1):
        print(f"  [{i}/{len(docs)}] reading {(doc['source_url'] or '')[:70]} ...", flush=True)
        # HARD per-document deadline. Thread timeouts cannot interrupt CPU-bound code
        # (a malformed page once burned 96% CPU for hours); SIGALRM can.
        signal.signal(signal.SIGALRM, _doc_timeout)
        signal.alarm(DOC_DEADLINE_SEC)
        try:
            body = minio.get_object(BUCKET, doc["object_key"]).read()
            text = to_text_guarded(body, doc["doc_type"], doc["source_url"] or "")
            if len(text) < 200 and doc["doc_type"] not in ("transcript", "pdf"):
                # likely a JavaScript-rendered page — retry through a renderer
                rendered = firecrawl_scrape(doc["source_url"] or "") or tavily_extract(doc["source_url"] or "")
                if len(rendered) > len(text):
                    text = rendered
                    print(f"  ↻ re-rendered {doc['source_url']} ({len(text)} chars)")
            if len(text) < 200 and doc["doc_type"] != "transcript":
                raise ValueError("document text too short — likely boilerplate or fetch artifact")

            comp = companies_by_id.get(doc["company_id"], {})
            company_name = comp.get("display_name", "")
            from urllib.parse import urlparse as _urlparse
            own_domain = _urlparse(comp.get("website") or "").netloc.lower().removeprefix("www.")
            # Every document is shown the FULL 103-field catalog. If a fact is
            # explicitly stated anywhere, we want it — provenance (source_type +
            # source_url + confidence) and cross-validation are what protect quality,
            # not hiding fields from the model. Nothing is inferred: the prompt
            # forbids anything not explicitly stated, so unfound fields stay empty.
            doc_class = doc["doc_type"]
            source_type = {"transcript": "transcript", "filing": "document"}.get(doc_class, "web")
            if doc_class == "transcript":
                catalog, cat_json = full_catalog, full_catalog_json
            else:
                catalog, cat_json = company_facts_catalog, company_facts_catalog_json

            candidates = []
            for ci, chunk in enumerate(chunks(text), 1):
                print(f"      chunk {ci} → model...", flush=True)
                # Run in the MAIN thread: the per-document SIGALRM deadline is the single
                # hard cap and it only works here (a worker thread holding the GIL blocks
                # the signal handler entirely).
                try:
                    candidates.extend(extract_chunk(client, model, cat_json, company_name,
                                                    doc["source_url"], chunk))
                except DocTimeout:
                    raise                                   # deadline — let the doc fail
                except Exception as chunk_exc:
                    print(f"      chunk {ci} skipped ({type(chunk_exc).__name__})", flush=True)
            facts = validate(candidates, catalog)

            tally: dict[str, int] = {}
            with conn.transaction():
                with conn.cursor() as cur:
                    for fact in facts:
                        result = write_fact(
                            cur, doc["company_id"], fact, doc, source_type,
                            client=client, model=model, own_domain=own_domain,
                            label=catalog[fact["field_id"]]["label"])
                        tally[result] = tally.get(result, 0) + 1
                    cur.execute(
                        "UPDATE documents SET status = 'extracted', processed_at = now() WHERE id = %s",
                        (doc["id"],),
                    )
            signal.alarm(0)                       # document finished in time
            print(f"  ✓ {doc['source_url']}: {tally or 'no facts'}")
        except Exception as exc:
            signal.alarm(0)                       # stop the clock before recovering
            try:
                conn.rollback()                   # a SIGALRM may land mid-transaction
            except Exception:
                pass
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE documents SET status = 'failed', processed_at = now() "
                            "WHERE id = %s", (doc["id"],),
                        )
            except Exception:
                pass
            print(f"  ! {doc['source_url']}: {exc}", flush=True)

    print("extraction pass complete")


if __name__ == "__main__":
    main()
