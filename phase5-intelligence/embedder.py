#!/usr/bin/env python3
"""Semantic index builder.

Takes extracted documents that have no chunks yet, splits their text, embeds
each chunk with NVIDIA's hosted embedding model, and stores vectors in
doc_chunks. Idempotent; run any time (the scheduler runs it every cycle).

Usage:
    python embedder.py [--limit N]    # documents per run (default 60)
"""
import argparse
import os
import re
from io import BytesIO

import psycopg
from bs4 import BeautifulSoup
from minio import Minio
from openai import OpenAI
from psycopg.rows import dict_row
from pypdf import PdfReader

BUCKET = "raw-documents"
CHUNK_CHARS = 1500
CHUNK_OVERLAP = 150
MAX_CHUNKS_PER_DOC = 40
BATCH = 16
EMBED_MODEL = os.environ.get("NVIDIA_EMBED_MODEL", "nvidia/nv-embedqa-e5-v5")


def pg():
    return psycopg.connect(
        host=os.environ.get("PGHOST", "postgres"),
        dbname=os.environ.get("PGDATABASE", "enterprise_intel"),
        user=os.environ.get("PGUSER", "vaultstack"),
        password=os.environ["PGPASSWORD"],
        row_factory=dict_row,
    )


def minio_client() -> Minio:
    return Minio(
        os.environ.get("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.environ.get("MINIO_ACCESS_KEY", "vaultstack"),
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )


def to_text(body: bytes, doc_type: str) -> str:
    if doc_type == "pdf":
        text = "\n".join((p.extract_text() or "") for p in PdfReader(BytesIO(body)).pages)
    else:
        text = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def chunks(text: str):
    start, n = 0, 0
    while start < len(text) and n < MAX_CHUNKS_PER_DOC:
        yield text[start:start + CHUNK_CHARS]
        start += CHUNK_CHARS - CHUNK_OVERLAP
        n += 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()

    conn = pg()
    conn.autocommit = True
    minio = minio_client()
    client = OpenAI(
        base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        api_key=os.environ["NVIDIA_API_KEY"],
        timeout=120.0,
        max_retries=1,
    )

    with conn.cursor() as cur:
        cur.execute(
            """SELECT d.id, d.company_id, d.doc_type, d.object_key, d.source_url
               FROM documents d
               WHERE d.status = 'extracted'
                 AND NOT EXISTS (SELECT 1 FROM doc_chunks ch WHERE ch.document_id = d.id)
               ORDER BY d.fetched_at LIMIT %s""",
            (args.limit,),
        )
        docs = cur.fetchall()
    print(f"embedding {len(docs)} document(s) with {EMBED_MODEL}")

    total_chunks = 0
    for doc in docs:
        try:
            body = minio.get_object(BUCKET, doc["object_key"]).read()
            text = to_text(body, doc["doc_type"])
            parts = [c for c in chunks(text) if c.strip()]
            if not parts:
                continue
            vectors = []
            for i in range(0, len(parts), BATCH):
                resp = client.embeddings.create(
                    model=EMBED_MODEL, input=parts[i:i + BATCH],
                    extra_body={"input_type": "passage", "truncate": "END"},
                )
                vectors.extend(d.embedding for d in resp.data)
            with conn.cursor() as cur:
                for idx, (part, vec) in enumerate(zip(parts, vectors)):
                    cur.execute(
                        """INSERT INTO doc_chunks
                             (company_id, document_id, chunk_index, chunk_text, embedding)
                           VALUES (%s, %s, %s, %s, %s::vector)
                           ON CONFLICT (document_id, chunk_index) DO NOTHING""",
                        (doc["company_id"], doc["id"], idx, part,
                         "[" + ",".join(f"{x:.6f}" for x in vec) + "]"),
                    )
            total_chunks += len(parts)
            print(f"  ✓ {doc['source_url']}: {len(parts)} chunk(s)")
        except Exception as exc:
            print(f"  ! {doc['source_url']}: {exc}")

    print(f"semantic index pass complete — {total_chunks} chunk(s) added")


if __name__ == "__main__":
    main()
