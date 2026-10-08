#!/usr/bin/env python3
"""Ingest confidential PDFs into the on-prem vector store (LanceDB).

Docling parses each PDF (OCR for scans) into Markdown, we split into overlapping,
structure-aware chunks, embed each with the local embedder, and store in LanceDB
on local disk. Reads the PDFs on the box at runtime — nothing leaves the machine.

Usage:
    python ingest.py <company-slug> <folder-of-pdfs>
"""
import re
import sys
from pathlib import Path

from docling.document_converter import DocumentConverter

from common import CHUNKS_TABLE, db, embed

CHUNK_CHARS = 1400
CHUNK_OVERLAP = 200
MAX_CHUNK_CHARS = 2600          # tables can be a bit larger before we row-split


def _is_table_line(ln: str) -> bool:
    return ln.count("|") >= 2


def split_markdown(md: str):
    """Format-robust chunking that works for ANY document Docling produces:
      • Markdown TABLES are kept intact as their own chunk (never split mid-table);
        a huge table is split by rows while repeating the header, so structure survives.
      • TEXT is split on paragraph (blank-line) boundaries, packed to ~CHUNK_CHARS with
        a small overlap, so sentences/subtopics stay whole.
      • The nearest preceding heading is attached to every chunk for context.
    This handles all-text, all-tables, and mixed text+charts (Docling OCRs charts to
    text) the same way — no per-document tuning needed.
    """
    lines = md.splitlines()
    cur_head, i = "", 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^#{1,6}\s", line):
            cur_head = re.sub(r"^#{1,6}\s+", "", line).strip()
            i += 1
            continue

        # ---- table block: gather consecutive table-ish lines, keep together ----
        if _is_table_line(line):
            tbl = []
            while i < len(lines) and (_is_table_line(lines[i]) or lines[i].strip() == ""):
                if lines[i].strip():
                    tbl.append(lines[i])
                i += 1
                if i < len(lines) and not _is_table_line(lines[i]) and lines[i].strip():
                    break
            if not tbl:
                continue
            header = "\n".join(tbl[:2])                      # markdown header + separator
            if len("\n".join(tbl)) <= MAX_CHUNK_CHARS:
                yield cur_head, "\n".join(tbl)
            else:                                            # row-split, repeat header
                body_rows, buf, size = tbl[2:], [], len(header)
                for row in body_rows:
                    if size + len(row) > MAX_CHUNK_CHARS and buf:
                        yield cur_head, header + "\n" + "\n".join(buf)
                        buf, size = [], len(header)
                    buf.append(row); size += len(row) + 1
                if buf:
                    yield cur_head, header + "\n" + "\n".join(buf)
            continue

        # ---- text block: gather until next heading/table, split by paragraphs ----
        txt = []
        while i < len(lines) and not re.match(r"^#{1,6}\s", lines[i]) and not _is_table_line(lines[i]):
            txt.append(lines[i]); i += 1
        body = re.sub(r"\n{3,}", "\n\n", "\n".join(txt)).strip()
        if not body:
            continue
        paras = [p.strip() for p in body.split("\n\n") if p.strip()]
        buf, size = [], 0
        for p in paras:
            if size + len(p) > CHUNK_CHARS and buf:
                yield cur_head, "\n\n".join(buf)
                # small overlap: carry the last paragraph forward
                buf = [buf[-1]] if len(buf[-1]) < CHUNK_OVERLAP else []
                size = sum(len(x) for x in buf)
            if len(p) > MAX_CHUNK_CHARS:                     # a single giant paragraph
                for s in range(0, len(p), CHUNK_CHARS - CHUNK_OVERLAP):
                    yield cur_head, p[s:s + CHUNK_CHARS]
            else:
                buf.append(p); size += len(p) + 2
        if buf:
            yield cur_head, "\n\n".join(buf)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    company, folder = sys.argv[1], Path(sys.argv[2])
    pdfs = sorted(folder.glob("*.pdf")) + sorted(folder.glob("*.PDF"))
    if not pdfs:
        sys.exit(f"no PDFs found in {folder}")

    conv = DocumentConverter()
    database = db()
    rows = []
    for pdf in pdfs:
        try:
            md = conv.convert(str(pdf)).document.export_to_markdown()
        except Exception as exc:
            print(f"  ! {pdf.name}: parse failed ({exc})"); continue
        chunks = [(h, t) for h, t in split_markdown(md) if len(t) > 40]
        if not chunks:
            print(f"  · {pdf.name}: no usable text"); continue
        vecs = embed([t for _, t in chunks])
        for i, ((head, text), v) in enumerate(zip(chunks, vecs)):
            rows.append({"vector": v, "company": company, "filename": pdf.name,
                         "chunk_index": i, "section": head[:200], "content": text})
        print(f"  + {pdf.name}: {len(chunks)} chunk(s)")

    if not rows:
        sys.exit("nothing to index")

    if CHUNKS_TABLE in database.table_names():
        tbl = database.open_table(CHUNKS_TABLE)
        tbl.delete(f"company = '{company}'")     # re-ingest cleanly
        tbl.add(rows)
    else:
        tbl = database.create_table(CHUNKS_TABLE, data=rows)
    print(f"done — {len(rows)} chunk(s) indexed for '{company}' (total rows: {tbl.count_rows()})")


if __name__ == "__main__":
    main()
