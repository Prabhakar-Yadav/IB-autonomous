# NemoClaw Project Delivery Report

Date: 2026-10-08

Repository target: `https://github.com/vaultstack-ai/ib-autonomous-investment-prabhakar`

## Executive Summary

The NemoClaw project has been packaged as a GitHub-ready repository for an AI-native investment-banking diligence intelligence platform. It includes the phase-based source code, Docker execution setup, schemas, workers, dashboard, analysis agents, on-prem RAG extension, scripts, model-comparison artifacts, and project handoff documentation.

Real environment files are intentionally excluded. Docker remains the execution/orchestration layer for local services and workers, while the GitHub repository stores the source and reproducible configuration.

## What Has Been Done

- Built the data layer with Postgres/pgvector, MinIO raw-document storage, SearXNG metasearch, PostgREST, schema SQL, and a 103-field company schema.
- Implemented company crawling, document cataloging, extraction, source validation, fact versioning, confidence tracking, and provenance storage.
- Added collection workers for news, GitHub, LinkedIn-style public snippets, registry sources, patents, and targeted gap filling.
- Added a scheduler for autonomous recurring collection, extraction, transcript intake, semantic indexing, and analysis cycles.
- Built analyst-facing surfaces: agent DB access, transcript intake, and a FastAPI dashboard with company profiles, search, conflicts, discoveries, pipeline health, and review pages.
- Added semantic search using pgvector and NVIDIA embeddings.
- Added schema-discovery analysis that proposes new fields without automatically mutating the canonical schema.
- Added a privacy-preserving on-prem RAG extension for confidential files using local parsing, embeddings, and Ollama-compatible inference.
- Captured model-comparison scripts and outputs for hosted large-model extraction versus smaller/on-prem-friendly model paths.
- Added root repository packaging: `README.md`, `.env.example`, stronger `.gitignore`, `.dockerignore`, and this delivery report.

## Docker Scope

Docker is used for execution, not as a replacement for source control. The Docker setup includes:

- Postgres/pgvector for canonical facts and semantic index tables.
- MinIO for raw document storage.
- SearXNG for local metasearch.
- PostgREST for read-only agent access.
- Worker containers for crawling, extraction, verification, and collection jobs.
- Scheduler container for recurring autonomous runs.
- Dashboard container for the analyst UI.
- Analysis container for schema discovery.
- Separate compose setup for on-prem RAG.

The main Docker entry point is `phase1-data-layer/db/docker-compose.yml`. The on-prem RAG entry point is `phase7-onprem-rag/docker-compose.yml`.

## Repository Hygiene

The following files are excluded from GitHub:

- `.env`
- `**/.env`
- `.env.*`
- `**/.env.*`
- Python caches and local virtual environments.
- Accidental local database files and bind-mounted runtime state.

The repository keeps `.env.example` files so another developer can recreate local configuration without exposing credentials.

## Current Status

The system is feature-complete for a local end-to-end diligence intelligence prototype:

- Data collection is implemented.
- Extraction and verification are implemented.
- Provenance and versioning are implemented.
- Analyst dashboard and review flows are implemented.
- Semantic search is implemented.
- Schema discovery is implemented.
- On-prem/private-data RAG path is implemented.
- Model-comparison tooling is present.

Known handoff result: on the Sequretek comparison, hosted Nemotron-3-Ultra-550B filled more of the 103-field schema than Nemotron-Nano-9B on the same collected document set, and additional targeted gap-fill rounds increased coverage further.

## Risks And Open Items

- Real `.env` values must be recreated locally after clone.
- The Docker stack should be smoke-tested after push using the target machine's WSL/Docker environment.
- The final Nano-9B versus Ultra-550B fair comparison should be completed and summarized.
- Some public-source fields will remain empty because they require founder transcripts, confidential files, or internal financial/cap-table data.
- Production deployment still needs formal secret management, backups, monitoring, auth hardening, and CI.
- Existing model-comparison logs may include noisy search results and should be treated as research artifacts rather than polished product output.

## Recommended Next Steps

1. Push the prepared repository to GitHub.
2. Clone it fresh on a clean machine or folder.
3. Create local `.env` from `.env.example`.
4. Run `docker compose config` from `phase1-data-layer/db`.
5. Start `postgres`, `minio`, `searxng`, `postgrest`, and `dashboard`.
6. Run one company through collect and extract.
7. Verify dashboard pages, conflict queue, discoveries, and semantic search.
8. Finish the model decision: hosted 550B, API-based smaller model, or on-prem model.
9. Add CI checks for Python syntax, compose validation, schema loading, and extraction parser behavior.
10. Add an operational runbook for backups, restore, key rotation, and private-data handling.

## Final Packaging Outcome

The project is ready to be committed and pushed with code, Docker configuration, documentation, reference assets, reports, and execution scripts. Environment files and local runtime secrets are not included.
