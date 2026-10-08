# NemoClaw Investment Banking Intelligence Platform

NemoClaw is an AI-native investment-banking diligence platform for building a continuously updated company intelligence database. It collects public and private-source documents, stores the raw evidence, extracts structured facts into a canonical schema, tracks provenance/confidence, and gives analysts a dashboard plus agent-facing read access.

Docker is used here as the local execution layer: Postgres, MinIO, SearXNG, workers, scheduler, dashboard, and analysis jobs run as containers. The repository contains the source code, schemas, Docker definitions, docs, scripts, model-comparison outputs, and client/reference files. Real `.env` files are intentionally excluded.

## What Is Included

| Path | Purpose |
| --- | --- |
| `phase1-data-layer/` | Postgres/pgvector schema, seed data, MinIO object storage, SearXNG, PostgREST, and the main Docker Compose runtime. |
| `phase2-workers/` | Company crawler, extraction pipeline, validation, reconciliation, NVIDIA client, and quality checks. |
| `phase3-news-worker/` | News, GitHub, LinkedIn, registry, patents, and gap-filler collection workers. |
| `phase3-scheduler/` | Always-on scheduler that runs crawl, search, intake, extraction, embedding, and analysis cycles. |
| `phase4-analyst-surface/` | Agent DB skill, policy files, transcript intake, and FastAPI analyst dashboard. |
| `phase5-intelligence/` | Semantic indexing with pgvector and NVIDIA embeddings. |
| `phase6-analysis/` | Schema-discovery analysis agents and promotion SQL. |
| `phase7-onprem-rag/` | Privacy-preserving on-prem RAG extension using local parsing, embeddings, Ollama, and a separate compose file. |
| `scripts/` | Controlled model-comparison, gap-fill, and batch execution scripts plus captured result artifacts. |
| `ZT-DATA/` | Reference PDFs used by the on-prem/private-data extension. |
| `PLAN.md` | Build history, product decisions, phases, and operational notes. |
| `PROJECT_CONTEXT_HANDOFF.md` | Full project handoff and latest engineering context. |
| `REPORT.md` / `REPORT.pdf` | Delivery report, completed work, current status, and recommended next steps. |

## Core Architecture

```text
Internet and analyst documents
  -> collection workers
  -> MinIO raw-document storage
  -> extraction and verification workers
  -> Postgres canonical facts DB
  -> pgvector semantic index
  -> dashboard, agent skill, analysis agents, and on-prem RAG
```

The LLM is not the source of truth. It parses evidence into structured facts. The database stores versioned facts with source URL, document ID, confidence, status, and review state.

## Docker Runtime

The main compose file is:

```bash
phase1-data-layer/db/docker-compose.yml
```

Main services:

- `postgres`: canonical facts database with pgvector.
- `minio` and `minio-init`: raw document storage and bucket creation.
- `searxng`: local metasearch used by collection workers.
- `postgrest`: read-only REST access for the agent.
- `worker`: company crawler and extraction runner.
- `news-worker`: news/search/registry/patents/gap-filler workers.
- `scheduler`: recurring autonomous pipeline.
- `dashboard`: FastAPI analyst UI on `http://localhost:8090`.
- `analysis`: on-demand schema discovery and field promotion support.

Start the core stack from WSL/Ubuntu:

```bash
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db
cp .env.example .env
# Fill .env with real local values. Do not commit it.
docker compose up -d postgres minio minio-init searxng postgrest dashboard
```

Build and run workers on demand:

```bash
docker compose build worker news-worker scheduler analysis dashboard
docker compose run --rm worker python company_worker.py sequretek "Sequretek" https://www.sequretek.com
docker compose run --rm worker python extractor.py --limit 25
docker compose up -d scheduler
```

Run the on-prem RAG extension separately:

```bash
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase7-onprem-rag
docker compose up -d
```

## Environment And Secrets

Real env files are deliberately excluded by `.gitignore`:

- `.env`
- `**/.env`
- `.env.*`
- `**/.env.*`

Use `.env.example` or `phase1-data-layer/db/.env.example` as templates. Required values depend on the services you run, but commonly include:

- `POSTGRES_PASSWORD`
- `MINIO_PASSWORD`
- `AGENT_RO_PASSWORD`
- `NVIDIA_API_KEY`
- `NVIDIA_MODEL`
- `NVIDIA_EMBED_MODEL`
- optional `TAVILY_API_KEY`, `FIRECRAWL_API_KEY`, `GITHUB_TOKEN`

## Current Status

Completed work includes the data layer, extraction pipeline, multi-source workers, scheduler, transcript intake, dashboard, semantic search, schema discovery, SearXNG migration, LLM-as-verifier logic, controlled model-comparison scripts, and on-prem RAG extension.

The most recent handoff notes focus on a fair model comparison between hosted Nemotron-3-Ultra-550B and smaller/on-prem-friendly models. The key known result in the handoff is that Ultra-550B filled more fields than Nano-9B on the same Sequretek source set, while targeted gap-filling raised completeness further.

## Recommended Next Steps

1. Recreate local `.env` from `.env.example` and verify the compose stack starts cleanly.
2. Run `docker compose config` from `phase1-data-layer/db` to validate the merged Docker configuration.
3. Run a fresh one-company smoke test: collect, extract, view dashboard, inspect provenance.
4. Complete the Nano-9B versus Ultra-550B comparison on the same final document pool.
5. Decide whether the on-prem model path is acceptable for confidential deal data.
6. Add automated smoke tests around schema loading, document queue release, extraction parsing, and dashboard read endpoints.

## GitHub Packaging Notes

This repository is prepared for GitHub with source, docs, Docker configs, reference artifacts, and generated report files. Real env files and local runtime state are excluded. Before pushing from a new machine, run:

```bash
git status --short
git ls-files | grep -E '(^|/)\.env($|\.)' && echo "Remove env files before pushing"
```
