"""Shared local pieces — Ollama (LLM + embeddings) and an embedded vector store.

Everything is local: Ollama on localhost, LanceDB as an on-disk file store (no
server, no root, no container). There is no code path to any external service.
"""
import os

import lancedb
import requests
from openai import OpenAI

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.environ.get("ZT_LLM_MODEL", "qwen2.5:7b-instruct")
EMBED_MODEL = os.environ.get("ZT_EMBED_MODEL", "bge-m3")
DB_PATH = os.environ.get("ZT_DB_PATH", os.path.expanduser("~/zt-rag/lancedb"))
CHUNKS_TABLE = "chunks"


def llm() -> OpenAI:
    return OpenAI(base_url=f"{OLLAMA_URL}/v1", api_key="ollama", timeout=600.0, max_retries=1)


EMBED_DIM = 1024


def _embed_one(text: str):
    r = requests.post(f"{OLLAMA_URL}/api/embeddings",
                      json={"model": EMBED_MODEL, "prompt": text}, timeout=300)
    r.raise_for_status()
    return r.json()["embedding"]


def embed(texts):
    """Embed strings locally. Bulletproof: a single unembeddable chunk (e.g. an
    oversized table block) is progressively truncated, and worst-case gets a zero
    vector, so one bad chunk can never crash a whole ingest. Returns list[list[float]]."""
    import time
    out = []
    for t in texts:
        t = (t or " ").strip() or " "
        vec = None
        for cap in (4000, 1500, 500, 120):          # progressive truncation fallback
            try:
                vec = _embed_one(t[:cap])
                break
            except Exception:
                time.sleep(1)
        if vec is None:
            print(f"    [embed] skipped an unembeddable chunk ({len(t)} chars) — zero vector")
            vec = [0.0] * EMBED_DIM
        out.append(vec)
    return out


def db():
    return lancedb.connect(DB_PATH)


def open_chunks(database):
    """Open the chunks table if it exists, else None (created on first ingest)."""
    return database.open_table(CHUNKS_TABLE) if CHUNKS_TABLE in database.table_names() else None
