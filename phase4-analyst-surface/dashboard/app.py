#!/usr/bin/env python3
"""Analyst dashboard — the platform's web face.

Read: company profiles with provenance, completeness, gap lists, pipeline health.
Write: exactly one action — resolving conflicted facts (keep/reject). The dashboard
is trusted code running next to the DB; the sandboxed agent has no access to it.
"""
import os

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="PE Intelligence Dashboard")
templates = Jinja2Templates(directory="templates")

SECTION_ORDER = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "N"]


def db():
    return psycopg.connect(
        host=os.environ.get("PGHOST", "postgres"),
        dbname=os.environ.get("PGDATABASE", "enterprise_intel"),
        user=os.environ.get("PGUSER", "vaultstack"),
        password=os.environ["PGPASSWORD"],
        row_factory=dict_row,
    )


@app.get("/")
def overview(request: Request):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT cc.slug, cc.display_name, cc.fields_filled, cc.fields_total,
                      cc.completeness_pct, c.website, c.status,
                      count(*) FILTER (WHERE fv.status = 'verified')   AS verified,
                      count(*) FILTER (WHERE fv.status = 'conflicted') AS conflicted,
                      max(fv.created_at)                               AS last_fact_at
               FROM company_completeness cc
               JOIN companies c ON c.id = cc.company_id
               LEFT JOIN field_values fv ON fv.company_id = cc.company_id AND fv.is_current
               GROUP BY cc.slug, cc.display_name, cc.fields_filled, cc.fields_total,
                        cc.completeness_pct, c.website, c.status
               ORDER BY cc.completeness_pct DESC"""
        )
        companies = cur.fetchall()
        cur.execute(
            """SELECT count(*) FILTER (WHERE status = 'conflicted') AS conflicts,
                      count(*) FILTER (WHERE status = 'verified')   AS verified,
                      count(*)                                      AS total
               FROM field_values WHERE is_current"""
        )
        stats = cur.fetchone()
    return templates.TemplateResponse(
        request, "index.html", {"companies": companies, "stats": stats})


@app.get("/company/{slug}/review")
def company_review(request: Request, slug: str):
    """Unverified + conflicted facts for one company — kept off the main profile."""
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM companies WHERE slug = %s", (slug,))
        comp = cur.fetchone()
        if not comp:
            return RedirectResponse("/", status_code=303)
        cur.execute(
            """SELECT fv.id, sf.section_code, sf.section_title, sf.label, sf.founder_only,
                      fv.value_text, fv.confidence, fv.status, fv.source_type, fv.source_url,
                      fv.collected_by, fv.version, fv.created_at,
                      prev.value_text AS prev_value, prev.source_url AS prev_source
               FROM field_values fv
               JOIN schema_fields sf ON sf.id = fv.field_id
               LEFT JOIN LATERAL (
                   SELECT value_text, source_url FROM field_values p
                   WHERE p.company_id = fv.company_id AND p.field_id = fv.field_id
                     AND p.version < fv.version AND p.status <> 'rejected'
                   ORDER BY p.version DESC LIMIT 1
               ) prev ON true
               WHERE fv.company_id = %s AND fv.is_current
                 AND fv.status IN ('unverified', 'conflicted')
               ORDER BY (fv.status = 'conflicted') DESC, sf.sort_order""",
            (comp["id"],),
        )
        rows = cur.fetchall()
    conflicted = [r for r in rows if r["status"] == "conflicted"]
    unverified = [r for r in rows if r["status"] == "unverified"]
    return templates.TemplateResponse(
        request, "company_review.html",
        {"company": comp, "conflicted": conflicted, "unverified": unverified})


@app.get("/company/{slug}")
def company(request: Request, slug: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM companies WHERE slug = %s", (slug,))
        comp = cur.fetchone()
        if not comp:
            return RedirectResponse("/", status_code=303)
        # main profile shows VERIFIED facts only; unverified/conflicted live on /review
        cur.execute(
            """SELECT sf.section_code, sf.section_title, sf.label, sf.founder_only,
                      v.value_text, v.confidence, v.status, v.source_type, v.source_url,
                      v.collected_by, v.verified_by, v.version, v.created_at
               FROM schema_fields sf
               LEFT JOIN LATERAL (
                   SELECT * FROM field_values fv
                   WHERE fv.field_id = sf.id AND fv.company_id = %s AND fv.is_current
                     AND fv.status = 'verified'
               ) v ON true
               ORDER BY sf.sort_order""",
            (comp["id"],),
        )
        rows = cur.fetchall()
        cur.execute(
            """SELECT count(*) FILTER (WHERE status='conflicted') AS conflicted,
                      count(*) FILTER (WHERE status='unverified') AS unverified
               FROM field_values WHERE company_id = %s AND is_current""",
            (comp["id"],),
        )
        review_counts = cur.fetchone()
        cur.execute(
            "SELECT completeness_pct, fields_filled, fields_total FROM company_completeness WHERE slug = %s",
            (slug,),
        )
        completeness = cur.fetchone()

    sections, gaps = {}, {}
    for r in rows:
        code = r["section_code"]
        if r["value_text"] is not None:
            sections.setdefault(code, {"title": r["section_title"], "facts": []})["facts"].append(r)
        else:
            gaps.setdefault(code, {"title": r["section_title"], "fields": []})["fields"].append(r)
    ordered_sections = [(c, sections[c]) for c in SECTION_ORDER if c in sections]
    ordered_gaps = [(c, gaps[c]) for c in SECTION_ORDER if c in gaps]

    return templates.TemplateResponse(
        request, "company.html",
        {"company": comp, "completeness": completeness, "review": review_counts,
         "sections": ordered_sections, "gaps": ordered_gaps})


@app.get("/how")
def how(request: Request):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) n FROM documents")
        docs = cur.fetchone()["n"]
        cur.execute("SELECT count(*) n FROM field_values WHERE is_current")
        facts = cur.fetchone()["n"]
        cur.execute("SELECT count(*) n FROM companies")
        comps = cur.fetchone()["n"]
        cur.execute("SELECT count(*) n FROM worker_runs")
        runs = cur.fetchone()["n"]
        cur.execute("SELECT coalesce(count(*),0) n FROM doc_chunks")
        chunks = cur.fetchone()["n"]
        cur.execute("""SELECT collected_by, count(*) n FROM documents
                       GROUP BY 1 ORDER BY 2 DESC""")
        workers = cur.fetchall()
    return templates.TemplateResponse(request, "how.html",
        {"docs": docs, "facts": facts, "comps": comps, "runs": runs,
         "chunks": chunks, "workers": workers})


@app.get("/trace/{slug}")
def trace(request: Request, slug: str):
    """Extraction trace: how every field got (or didn't get) its value."""
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM companies WHERE slug = %s", (slug,))
        comp = cur.fetchone()
        if not comp:
            return RedirectResponse("/", status_code=303)
        cur.execute(
            "SELECT id, section_code, section_title, label, founder_only "
            "FROM schema_fields ORDER BY sort_order")
        fields = cur.fetchall()
        cur.execute(
            """SELECT fv.field_id, fv.value_text, fv.confidence, fv.status, fv.is_current,
                      fv.version, fv.source_type, fv.source_url, fv.collected_by,
                      fv.created_at, d.collected_by AS doc_worker, d.doc_type
               FROM field_values fv LEFT JOIN documents d ON d.id = fv.document_id
               WHERE fv.company_id = %s ORDER BY fv.field_id, fv.version""",
            (comp["id"],),
        )
        versions: dict = {}
        for r in cur.fetchall():
            versions.setdefault(r["field_id"], []).append(r)
        cur.execute(
            "SELECT collected_by, count(*) AS docs FROM documents "
            "WHERE company_id = %s GROUP BY 1 ORDER BY 2 DESC", (comp["id"],))
        workers = cur.fetchall()
        cur.execute(
            """SELECT coalesce(d.collected_by, fv.collected_by) AS via, count(*) AS facts
               FROM field_values fv LEFT JOIN documents d ON d.id = fv.document_id
               WHERE fv.company_id = %s AND fv.is_current GROUP BY 1 ORDER BY 2 DESC""",
            (comp["id"],))
        fact_sources = cur.fetchall()

    sections, code = [], None
    stats = {"filled": 0, "verified": 0, "conflicted": 0, "empty": 0, "founder_gap": 0}
    for f in fields:
        if f["section_code"] != code:
            sections.append({"code": f["section_code"], "title": f["section_title"], "fields": []})
            code = f["section_code"]
        vlist = versions.get(f["id"], [])
        current = next((v for v in vlist if v["is_current"]), None)
        if current:
            stats["filled"] += 1
            if current["status"] == "verified":
                stats["verified"] += 1
            elif current["status"] == "conflicted":
                stats["conflicted"] += 1
        else:
            stats["empty"] += 1
            if f["founder_only"]:
                stats["founder_gap"] += 1
        sections[-1]["fields"].append({"meta": f, "versions": vlist, "current": current})

    max_docs = max((w["docs"] for w in workers), default=1)
    max_facts = max((s["facts"] for s in fact_sources), default=1)
    return templates.TemplateResponse(
        request, "trace.html",
        {"company": comp, "sections": sections, "stats": stats,
         "workers": workers, "fact_sources": fact_sources,
         "max_docs": max_docs, "max_facts": max_facts, "total": len(fields)})


@app.get("/conflicts")
def conflicts(request: Request):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT fv.id, c.slug, c.display_name, sf.label, sf.section_title,
                      fv.value_text, fv.confidence, fv.source_url, fv.source_type,
                      fv.created_at, prev.value_text AS prev_value,
                      prev.source_url AS prev_source, prev.confidence AS prev_confidence
               FROM field_values fv
               JOIN companies c ON c.id = fv.company_id
               JOIN schema_fields sf ON sf.id = fv.field_id
               LEFT JOIN LATERAL (
                   SELECT value_text, source_url, confidence FROM field_values p
                   WHERE p.company_id = fv.company_id AND p.field_id = fv.field_id
                     AND p.version < fv.version AND p.status <> 'rejected'
                   ORDER BY p.version DESC LIMIT 1
               ) prev ON true
               WHERE fv.is_current AND fv.status = 'conflicted'
               ORDER BY c.slug, sf.sort_order"""
        )
        items = cur.fetchall()
    return templates.TemplateResponse(request, "conflicts.html", {"items": items})


@app.post("/conflicts/{fact_id}/keep")
def keep_fact(fact_id: int):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE field_values
               SET status = 'verified', verified_by = 'analyst-dashboard', verified_at = now()
               WHERE id = %s AND is_current AND status = 'conflicted'""",
            (fact_id,),
        )
        conn.commit()
    return RedirectResponse("/conflicts", status_code=303)


@app.post("/conflicts/{fact_id}/reject")
def reject_fact(fact_id: int):
    with db() as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """UPDATE field_values SET status = 'rejected', is_current = false
                   WHERE id = %s AND is_current AND status = 'conflicted'
                   RETURNING company_id, field_id, version""",
                (fact_id,),
            )
            row = cur.fetchone()
            if row:
                # restore the newest earlier non-rejected version, if any
                cur.execute(
                    """UPDATE field_values SET is_current = true
                       WHERE id = (SELECT id FROM field_values
                                   WHERE company_id = %s AND field_id = %s
                                     AND version < %s AND status <> 'rejected'
                                   ORDER BY version DESC LIMIT 1)""",
                    (row["company_id"], row["field_id"], row["version"]),
                )
    return RedirectResponse("/conflicts", status_code=303)


@app.get("/search")
def search(request: Request, q: str = ""):
    results, error = [], None
    if q.strip():
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
                api_key=os.environ["NVIDIA_API_KEY"],
            )
            emb = client.embeddings.create(
                model=os.environ.get("NVIDIA_EMBED_MODEL", "nvidia/nv-embedqa-e5-v5"),
                input=[q.strip()],
                extra_body={"input_type": "query", "truncate": "END"},
            ).data[0].embedding
            vec = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
            with db() as conn, conn.cursor() as cur:
                cur.execute(
                    """SELECT c.slug, c.display_name, d.title, d.source_url, d.doc_type,
                              ch.chunk_text, 1 - (ch.embedding <=> %s::vector) AS score
                       FROM doc_chunks ch
                       JOIN documents d ON d.id = ch.document_id
                       JOIN companies c ON c.id = ch.company_id
                       ORDER BY ch.embedding <=> %s::vector
                       LIMIT 12""",
                    (vec, vec),
                )
                results = cur.fetchall()
        except KeyError:
            error = "NVIDIA_API_KEY is not configured for the dashboard container."
        except Exception as exc:
            error = f"Search failed: {exc}"
    return templates.TemplateResponse(
        request, "search.html", {"q": q, "results": results, "error": error})


@app.get("/discoveries")
def discoveries(request: Request):
    """Fields the discovery agent proposes adding beyond the 103-field schema."""
    rows, err = [], None
    with db() as conn, conn.cursor() as cur:
        try:
            cur.execute("SELECT * FROM discovered_field_rollup LIMIT 200")
            rows = cur.fetchall()
        except Exception:
            err = ("Discovery tables not created yet — run "
                   "phase6-analysis/sql/30_discovered_fields.sql, then the discovery agent.")
    return templates.TemplateResponse(request, "discoveries.html",
                                      {"rows": rows, "error": err})


@app.get("/pipeline")
def pipeline(request: Request):
    with db() as conn, conn.cursor() as cur:
        # complete run ledger since day one (newest first)
        cur.execute(
            """SELECT wr.worker_id, c.slug, wr.started_at, wr.finished_at, wr.status, wr.stats
               FROM worker_runs wr LEFT JOIN companies c ON c.id = wr.company_id
               ORDER BY wr.started_at DESC LIMIT 1000"""
        )
        runs = cur.fetchall()
        cur.execute("SELECT status, count(*) AS n FROM documents GROUP BY status ORDER BY n DESC")
        doc_stats = cur.fetchall()
        cur.execute(
            "SELECT status, count(*) AS n FROM transcript_inbox GROUP BY status ORDER BY n DESC")
        inbox_stats = cur.fetchall()

        # lifetime totals — everything the platform has ever done
        cur.execute(
            """SELECT count(*) AS runs, min(started_at) AS first_run, max(started_at) AS last_run
               FROM worker_runs""")
        lifetime = cur.fetchone()
        cur.execute("SELECT count(*) AS docs FROM documents")
        lifetime["docs"] = cur.fetchone()["docs"]
        cur.execute("SELECT count(*) AS facts, count(*) FILTER (WHERE is_current) AS current_facts "
                    "FROM field_values")
        r = cur.fetchone()
        lifetime["fact_versions"] = r["facts"]
        lifetime["current_facts"] = r["current_facts"]
        cur.execute("SELECT count(*) AS chunks FROM doc_chunks")
        lifetime["chunks"] = cur.fetchone()["chunks"]

        cur.execute(
            """SELECT wr.worker_id, count(*) AS runs,
                      count(*) FILTER (WHERE wr.status = 'succeeded') AS ok,
                      min(wr.started_at) AS first_run, max(wr.started_at) AS last_run,
                      coalesce((SELECT count(*) FROM documents d
                                WHERE d.collected_by = wr.worker_id), 0) AS docs
               FROM worker_runs wr GROUP BY wr.worker_id ORDER BY docs DESC"""
        )
        per_worker = cur.fetchall()
        cur.execute(
            """SELECT c.slug,
                      count(DISTINCT d.id) AS docs,
                      count(DISTINCT fv.id) FILTER (WHERE fv.is_current) AS facts,
                      count(DISTINCT wr.id) AS runs
               FROM companies c
               LEFT JOIN documents d ON d.company_id = c.id
               LEFT JOIN field_values fv ON fv.company_id = c.id
               LEFT JOIN worker_runs wr ON wr.company_id = c.id
               GROUP BY c.slug ORDER BY docs DESC"""
        )
        per_company = cur.fetchall()
    return templates.TemplateResponse(
        request, "pipeline.html",
        {"runs": runs, "doc_stats": doc_stats, "inbox_stats": inbox_stats,
         "lifetime": lifetime, "per_worker": per_worker, "per_company": per_company})
