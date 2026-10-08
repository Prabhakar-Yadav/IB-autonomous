#!/usr/bin/env python3
"""Fill the 103-field schema from the confidential PDFs — fully on-prem.

Per field: embed a query from label+guidance, retrieve the most relevant chunks
from LanceDB (RAG), and ask the LOCAL LLM to extract the value (or NOT FOUND)
with confidence + supporting quote + source file. Writes results to a JSON file.

Usage:
    python extract_local.py <company-slug> [--schema company_schema.json] [--topk 6]
"""
import argparse
import json
import os
import re

from common import LLM_MODEL, db, embed, llm, open_chunks

SYSTEM = """You extract one specific data field about a company from document excerpts
(the excerpts may be prose, tables, or chart/figure text).

Rules:
- Use ONLY the excerpts. Do not use outside knowledge.
- The answer is often present but phrased differently, or sits inside a table row or a
  bullet list — read carefully and EXTRACT it. Combine facts across excerpts if needed.
- Only answer NOT FOUND when the information is genuinely absent. Do not be lazy.
- Keep numbers, names and lists exactly as written.

Reply as strict JSON on the FINAL line, nothing after it:
{"value": "<value or NOT FOUND>", "confidence": <0.0-1.0>, "quote": "<short supporting quote>", "source": "<filename>"}"""


def parse_json_tail(raw: str):
    for m in reversed(re.findall(r"\{.*?\}", raw or "", re.DOTALL)):
        try:
            return json.loads(m)
        except json.JSONDecodeError:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("company")
    ap.add_argument("--schema", default="company_schema.json")
    ap.add_argument("--topk", type=int, default=10)
    args = ap.parse_args()

    schema = json.load(open(args.schema, encoding="utf-8"))
    fields = [(f["id"], f["label"], f.get("guidance", ""), sec["code"])
              for sec in schema["sections"] for f in sec["fields"]]

    tbl = open_chunks(db())
    if tbl is None:
        raise SystemExit("no indexed documents — run ingest.py first")
    client = llm()
    print(f"extracting {len(fields)} fields for '{args.company}' with {LLM_MODEL} (local)")

    results, filled = [], 0
    for fid, label, guidance, code in fields:
        qvec = embed([f"{label}. {guidance}"])[0]
        hits = (tbl.search(qvec).where(f"company = '{args.company}'", prefilter=True)
                .limit(args.topk).to_list())
        if not hits:
            continue
        context = "\n\n".join(f"[{h['filename']}] {h['content'][:900]}" for h in hits)
        user = f"FIELD: {label}\nGUIDANCE: {guidance}\n\nDOCUMENT EXCERPTS:\n{context}\n\nExtract the field."
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL, temperature=0.0, max_tokens=400,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": user}])
            ans = parse_json_tail(resp.choices[0].message.content)
        except Exception as exc:
            print(f"  ! {label}: {exc}"); continue
        if not ans:
            continue
        value = str(ans.get("value", "")).strip()
        if not value or value.upper().startswith("NOT FOUND"):
            continue
        try:
            conf = max(0.0, min(1.0, float(ans.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        rec = {"field_id": fid, "label": label, "section_code": code, "value": value[:2000],
               "confidence": round(conf, 3), "source_file": ans.get("source") or hits[0]["filename"],
               "quote": str(ans.get("quote", ""))[:500]}
        results.append(rec)
        filled += 1
        print(f"  ✓ {label}: {value[:60]}  (conf {conf:.2f}, {rec['source_file']})")

    os.makedirs("results", exist_ok=True)
    out = os.path.join("results", f"{args.company}_fields.json")
    json.dump({"company": args.company, "filled": filled, "total": len(fields),
               "completeness_pct": round(100 * filled / len(fields), 1), "fields": results},
              open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\ndone — {filled}/{len(fields)} fields ({round(100*filled/len(fields),1)}%) → {out}")


if __name__ == "__main__":
    main()
