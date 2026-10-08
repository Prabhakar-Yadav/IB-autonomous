#!/usr/bin/env python3
"""Ask questions over the confidential PDFs — on-prem RAG Q&A with citations.

Retrieves the most relevant chunks (LanceDB) and has the local LLM answer using
only those chunks, citing source file. Fully offline.

Usage:
    python rag_query.py <company-slug> "your question"
    python rag_query.py <company-slug>            # interactive
"""
import sys

from common import LLM_MODEL, db, embed, llm, open_chunks

SYSTEM = """Answer the question using ONLY the provided document excerpts.
Cite the source file for each fact, like (filename). If the excerpts do not
contain the answer, say so plainly. Do not use outside knowledge."""


def answer(client, tbl, company, question):
    qvec = embed([question])[0]
    hits = (tbl.search(qvec).where(f"company = '{company}'", prefilter=True)
            .limit(8).to_list())
    if not hits:
        return "No indexed documents for this company."
    context = "\n\n".join(f"[{h['filename']}] {h['content'][:1000]}" for h in hits)
    resp = client.chat.completions.create(
        model=LLM_MODEL, temperature=0.1, max_tokens=700,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": f"EXCERPTS:\n{context}\n\nQUESTION: {question}"}])
    return resp.choices[0].message.content


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    company = sys.argv[1]
    tbl = open_chunks(db())
    if tbl is None:
        raise SystemExit("no indexed documents — run ingest.py first")
    client = llm()
    if len(sys.argv) >= 3:
        print(answer(client, tbl, company, " ".join(sys.argv[2:]))); return
    print(f"RAG Q&A over '{company}' (local {LLM_MODEL}). Ctrl+C to exit.")
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q:
            print(answer(client, tbl, company, q))


if __name__ == "__main__":
    main()
