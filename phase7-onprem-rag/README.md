# Phase 7 — On-Prem RAG (confidential documents)

Fully local pipeline: confidential company PDFs → structured facts (the 103-field
schema) + question-answering, running entirely on the lead's GPU box. **No external
API. No data leaves the machine.**

```
PDFs (ZT-DATA on the box)
  → Docling  (parse + OCR → Markdown/tables)
  → structure-aware chunking
  → bge-m3 embeddings          (local, via Ollama)
  → pgvector                   (local container)
  → per-field RAG extraction   (local qwen2.5:14b via Ollama) → zt_field_values
  → RAG Q&A with citations
```

Stack: Docling · Ollama (qwen2.5:14b + bge-m3) · pgvector · Python. Runs on
`prabhakar@100.105.243.101`. VRAM budget ≈ 10–12 GB (14b ~9GB + embeddings ~1GB).

## One-time setup (on the box)

```bash
cd ~/zt-rag

# 1. local models (already pulling): the LLM and the embedder
ollama pull qwen2.5:14b
ollama pull bge-m3

# 2. python deps (ideally in a venv)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. on-prem vector DB
docker compose up -d zt-postgres
```

## Run on a company's documents

```bash
# put the PDFs somewhere on the box, e.g. ~/zt-data/zindagi/
# 1. ingest (parse + embed + index)
python ingest.py zindagi ~/zt-data/zindagi

# 2. fill the 103 fields from the documents
python extract_local.py zindagi

# 3. ask questions
python rag_query.py zindagi "What were the FY24 and FY25 revenue figures?"
```

## Inspect results (SQL, on the box)

```bash
docker exec zt-postgres psql -U zt -d zt_rag -c \
 "SELECT section_code, label, left(value_text,60), confidence, source_file, source_page
  FROM zt_field_values WHERE company='zindagi' ORDER BY section_code;"
```

## Model choices (VRAM budget)
- Default LLM `qwen2.5:14b` (~9GB). Lower memory → `ZT_LLM_MODEL=qwen2.5:7b-instruct`
  (already on the box, ~5GB). Higher quality if VRAM allows → `gpt-oss:20b`.
- Embeddings `bge-m3` (1024-dim). Lighter → `nomic-embed-text` (change EMBED_DIM to 768
  in common.py + init.sql).

## Notes
- Everything is offline. `common.py` only ever calls `localhost:11434` (Ollama) and the
  local Postgres. There is no code path to any external service.
- Results (`zt_field_values`) carry source file + page + quote, so every extracted fact
  is auditable back to the exact PDF location — same provenance discipline as the public
  platform. They can later be exported into the main company profile as source_type
  'document' if/when the two systems are bridged.
