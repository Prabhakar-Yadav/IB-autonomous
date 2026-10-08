# Phase 5 — Intelligence layer (semantic search)

Every collected document (web pages, news, filings, transcripts) gets split into
chunks and embedded as vectors (NVIDIA `nv-embedqa-e5-v5`) into pgvector. The
dashboard's **Search** tab then answers plain-language questions by *meaning*
("regulatory trouble", "who is raising money") across the entire corpus.

Components:
- `sql/20_semantic.sql` — pgvector extension + `doc_chunks` table + HNSW index
- `embedder.py` — indexes new extracted documents (scheduler runs it every cycle)
- dashboard `/search` — query box + ranked results with company + source receipts
- Postgres image swapped to `pgvector/pgvector:pg16` (drop-in, data preserved)

## Setup (run in Ubuntu, in order)

```bash
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db

# 1. swap Postgres to the pgvector image (containers restart; DATA IS PRESERVED —
#    it lives in the named volume, and this image is the same Postgres 16)
docker compose pull postgres
docker compose up -d postgres
docker compose ps          # wait until healthy

# 2. semantic schema
docker exec -i nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
  < "/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase5-intelligence/sql/20_semantic.sql"

# 3. new-workers migration (github org column + set known orgs)
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
  "ALTER TABLE companies ADD COLUMN IF NOT EXISTS github_org text;"
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
  "UPDATE companies SET github_org='razorpay' WHERE slug='razorpay';
   UPDATE companies SET github_org='zoho' WHERE slug='zoho';"

# 4. rebuild everything that changed
docker compose build news-worker worker scheduler dashboard
docker compose up -d --force-recreate dashboard scheduler

# 5. first index run (also happens automatically every cycle)
docker compose run --rm scheduler python /app/phase5/embedder.py --limit 100

# 6. optional immediate test of the new workers (scheduler covers them next cycle)
docker compose run --rm news-worker python linkedin_worker.py razorpay
docker compose run --rm news-worker python registry_worker.py dhruva-space
docker compose run --rm news-worker python github_worker.py razorpay razorpay
docker compose run --rm worker python extractor.py --limit 15
```

Then open http://localhost:8090/search and ask something like
"regulatory investigation" or "satellite technology".

## Notes
- Embedding costs: ~1 API call per 16 chunks; a typical document is 1–5 chunks.
- LinkedIn worker never scrapes linkedin.com (ToS) — it stores public search
  snippets with their own source URLs. Registry worker fetches public MCA-mirror
  pages (ZaubaCorp/Tofler/TheCompanyCheck), stored as doc_type 'filing'.
- Next step of Phase 5 (later): knowledge graph + bot access to semantic search.
