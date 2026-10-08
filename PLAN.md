# AI-Native Investment Banking Platform — Build Plan (v2)

Source of truth for vision: `Product Doc - AI Native Investment Banking.docx`
Field reference: `IB inputs required from nemoclaw.xlsx`

## What we are building

Not a chatbot that researches companies on demand. A **continuously updated enterprise
intelligence platform**: persistent NemoClaw/OpenClaw workers collect raw information 24/7,
an extraction engine turns it into structured, verifiable facts, and those facts become the
firm's institutional memory. Every downstream capability (search, valuation, due diligence,
monitoring, recommendations) reads from that trusted data layer.

**Engineering principle: the LLM is never the source of truth. The database is.**
The LLM is only an intelligent parser (unstructured → structured JSON with source +
confidence). Workers only collect — they never reason.

## Architecture (from the product doc)

```
Internet
  → Persistent Collection Workers (NemoClaw/OpenClaw)   [collect only]
  → Extraction Engine (clean → chunk → LLM extract → validate → normalize)
  → Canonical Enterprise Database (Postgres) + Object Storage (raw docs)
  → Knowledge Graph + Semantic Index (pgvector / OpenSearch)
  → Analysis Agents (valuation, competitive intel, thesis, risk)
  → Investment Committee / natural-language interface
```

### Workers (each runs forever, collect-only)
- **Company Worker** — website change detection, pages, PDFs, presentations, careers, blog, press releases
- **LinkedIn Worker** — hiring, employee count, leadership changes
- **News Worker** — Reuters, Bloomberg, Moneycontrol, Economic Times
- **GitHub Worker** — repos, stars, commits, releases, languages
- (Later: patents, filings/MCA, app stores, social)

### Extraction factory (same pipeline for every document)
Raw HTML/PDF → clean → chunk → LLM extraction (structured JSON only, never summaries)
→ validate → normalize → store. Example output:
`{"enterprise_customers": 12000, "countries": 42, "source": "https://...", "confidence": 0.97}`

### Master Company Schema
One canonical schema per company. Doc envisions ~25 sections (Basic Info, Legal, Financials,
Products, Technology, Customers, Pricing, Founders, Leadership, Investors, Funding, Hiring,
Patents, Competitors, Geographies, Security, Compliance, ESG, Partnerships, Acquisitions,
Litigation, News, Signals, Risks, Growth, Scores) growing toward ~5000 fields.
**v1 seed = the 110 fields in the Excel**, restructured into this schema. Founder-only
fields (cap table, raise terms, internal financials) are fed via call transcripts/Telegram,
not scraping.

### Every field stores metadata, not just a value
value, unit/currency, confidence, source URL, page/locator, collected_by (worker),
verified_by (cross-validator), last_updated, version (append-only history).

## Decisions made
| Decision | Choice |
|---|---|
| Agent runtime | NVIDIA NemoClaw (OpenClaw in OpenShell sandbox), WSL2 + Docker on Windows |
| Inference | NVIDIA hosted Nemotron (build.nvidia.com API key) |
| Facts DB | Postgres (+ pgvector later) in Docker |
| Raw docs | Object storage (local MinIO in Docker to start) |
| Analyst channel | Telegram first (transcripts, founder-only data, queries) |
| Search index / graph | pgvector first; OpenSearch + knowledge graph in a later phase |

## Build phases

**Phase 0 — Foundation** ✅ DONE 2026-07-11 (sandbox `pe-intake` live: Nemotron 3 Ultra 550B via NVIDIA Endpoints, Tavily search, Telegram bot verified)
1. Windows prep: WSL2 + Ubuntu-24.04 + Docker Desktop ✅
2. Keys: NVIDIA API, Tavily search, Telegram bot — all in `.env` (never commit) ✅
3. NemoClaw installed; sandbox **`pe-intake`** live on **nvidia/nemotron-3-ultra-550b-a55b**,
   Telegram channel (DM allowlist, groups disabled), Balanced policy + tavily/telegram presets,
   resource profile creator (50/50), 4 GB swap added ✅
   - Web UI: http://127.0.0.1:18789/ · Status: `nemoclaw pe-intake status` · Logs: `nemoclaw pe-intake logs --follow`

**Phase 1 — Data layer** ✅ DONE 2026-07-11 (103 fields verified in Postgres; MinIO bucket ready)
Folder: `phase1-data-layer/`
- `schema/company_schema.json` — master schema v1: 103 fields / 13 sections from the Excel,
  each with type, allowed sources, founder_only flag ✅
- `db/docker-compose.yml` — Postgres 16 + MinIO + bucket init (also defines the Phase 2
  `worker` service) ✅
- `db/init/01_schema.sql` — companies, schema_fields, documents, field_values (append-only,
  full provenance envelope), interactions, workers, worker_runs + views ✅
- `db/init/02_seed_fields.sql` — auto-generated from the JSON (103 rows) ✅

**Phase 2 — First worker + extraction pipeline (the vertical slice)** ✅ DONE 2026-07-11
(3 companies collected+extracted: zoho 12, razorpay 16, dhruva-space 11 fields; 46 docs, 3 clean worker runs; provenance verified incl. CIN/GST, RBI licence, ISO cert)
Folder: `phase2-workers/`
- `company_worker.py` — collect-only crawler: pages+PDFs → MinIO + documents catalog,
  hash-based change detection, worker_runs audit ✅
- `extractor.py` — clean → chunk → Nemotron (NVIDIA API) → validate against schema
  registry (web-allowed, non-founder-only fields only) → field_values with provenance ✅
- `common.py`, `Dockerfile`, `requirements.txt`; `worker` service defined in the Phase 1 compose ✅
- Design note: workers run as plain containers next to the DB (not inside the agent sandbox) —
  simpler networking/policy; the pe-intake agent stays the analyst front door. Scheduling → Phase 3.
- ▶ user: follow `phase2-workers/README.md`; test companies: Zoho, Razorpay, Dhruva Space

**Phase 3 — More workers** 🔨 News Worker + cross-validation built 2026-07-11, awaiting user run
- Folder `phase3-news-worker/`: `news_worker.py` — Tavily-powered press search (funding,
  valuation, M&A, legal, leadership), article fetch w/ snippet fallback, doc_type `news` ✅
- Cross-validation added to `phase2-workers/extractor.py`: independent agreement → `verified`;
  disagreement → `conflicted` (verified facts never overturned by a lone source);
  prompt hardened (no copyright dates; target-company-only) ✅
- News run DONE for razorpay 2026-07-11 (20 articles, verified+conflicted facts landed; 24.3%)
- Scheduler ✅ DONE 2026-07-12 — folder `phase3-scheduler/`: always-on container, first full
  autonomous cycle completed (crawl+news+extract for all 3 companies; scoreboard after:
  razorpay 26.2%, zoho 18.4%, dhruva 14.6%; 7 verified / 31 conflicted / 23 unverified facts).
  INTERVAL_HOURS=24. Post-reboot ritual: start Docker → `nemoclaw pe-intake connect` once.
- Validation engine v2 🔨 built 2026-07-13: `phase2-workers/quality.py` (source tiers 0–4:
  transcript > own-site/gov > major press > aggregators > blogs; LLM equivalence judge),
  write_fact upgraded (reworded agreement → verified; worse source can't displace better),
  `reconcile.py` one-shot to re-judge existing conflicts
  ▶ user: rebuild worker+scheduler, run reconcile.py, review remaining queue in dashboard
- Reconcile run ✅ 2026-07-13: conflicts 31→16 (13 better-source restorations, 1 verified,
  1 better-source win); 16 equal-tier disputes left in dashboard queue for human review
- More workers ✅ DONE 2026-07-13 (in `phase3-news-worker/`), all wired into the scheduler:
  - `github_worker.py` — official GitHub API: org profile, top repos, languages
  - `linkedin_worker.py` — public search snippets only (never scrapes linkedin.com)
  - `registry_worker.py` — MCA-mirror pages (ZaubaCorp/Tofler/IndiaFilings/InstaFinancials/
    Falconebiz): CIN, incorporation date, directors, capital. doc_type 'filing', tier 2
  - `patents_worker.py` — Google Patents public search + Indian Patent Office sweep
  - `gap_filler_worker.py` — TARGETED: reads each company's empty schema fields and runs a
    focused search per gap (runs after the first extraction pass each cycle)
  - companies.github_org column added; source tiers extended (registry mirrors = tier 2)
  - First autonomous cycle w/ all 8 workers: 122 news + 93 web + 20 registry + patents +
    GitHub + LinkedIn docs collected

**Fill-rate fixes (2026-07-13)** — user challenged "the data IS on the internet". All three
were real bugs, not design limits:
1. Missing sources: registry worker lacked IndiaFilings/InstaFinancials/etc → added 7 domains
2. Precision loss: extractor returned "2015" when the page stated an exact date → prompt now
   demands maximal precision (exact dates/addresses/identifiers, never rounded)
   + MULTI-ENTITY rule: Razorpay has 6 legal entities; facts must describe the PRIMARY
   operating entity (oldest/brand-matching), with entity name appended to the value
3. **Financials were over-classified as founder-only.** True in the US, FALSE in India:
   MCA filings are public and Inc42/Entrackr/Moneycontrol report every startup's annual
   revenue/loss. Registry mirrors paywall it ("Locked In full report") but the PRESS is free.
   → news_worker gained financial queries (FY revenue/profit/EBITDA/ESOP);
   → schema: revenue_last_3fy, revenue_growth_yoy, gross_margin, ebitda_operating_margin,
     arr_mrr, key_cost_line_items, audited_financials, outstanding_debt, headcount are now
     web-fillable (founder_only=false);
   → extractor gained a third catalog for doc_type='filing' (statutory financials allowed).
   RESULT: Razorpay revenue FY25 ₹3,783cr / FY24 ₹2,296cr, growth 65% — both confidence 1.00.
   Completeness: razorpay 27.2%→31.1%, zoho 21.4%→24.3%.
   Still genuinely founder-only: cap table %, ESOP terms, current raise terms, CAC/LTV/churn,
   cash runway, net burn.
4. **12 more fields were wrongly marked non-web** (TAM/SAM/SOM, product roadmap, corporate
   structure, channel mix, manufacturing/supply chain, TRL, pivots, key-person risk…) — all
   are publicly reported. → now web-fillable. Web-fillable pool: 39 → **59 of 103 fields**
   (automatic ceiling ~38% → ~57%).
5. **Gap-filler upgraded**: per-field query recipes (40 hand-written search phrasings that
   mirror how each fact is actually reported), 3 results/gap, up to 30 gaps/company/cycle.
   First real run collected ~180 targeted documents across the 3 companies.
6. **🔴 THE BIG ONE — reasoning-model narration bug.** Nemotron 3 Ultra is a *reasoning*
   model: given the large field catalog it replied with prose ("The user wants me to extract…"),
   never emitted the JSON array, and even hallucinated field names. Our parser saw no array →
   "no facts". This silently suppressed extraction platform-wide from day one. Registry pages
   containing CIN/incorporation/directors returned NOTHING.
   Fix in `extractor.py`: strict output contract in the prompt, `thinking: False` via
   extra_body, assistant prefill of "[", max_tokens 2000→3000, tolerant parser (strips
   <think> blocks/fences, takes last complete array, salvages individual objects), plus a
   terse retry. Same two pages went from "no facts" → 15 facts.
7. **Extractor now sees the FULL 103-field catalog on every document** (user's call, correct):
   restricting the catalog was hiding legitimately-public data. Provenance (source_type,
   source_url, confidence) + cross-validation protect quality — not field-hiding. The
   "only explicit statements" rule still forbids inference, so unfound fields stay empty.

**Phase 4 — Analyst surface** (part 1 pulled forward, ahead of Phase 3 by decision 2026-07-11)
- Part 1: bot ↔ DB ✅ DONE 2026-07-11 (Telegram test passed: profile with source+confidence
  receipts, completeness ranking, correct founder-only refusal on revenue) — folder `phase4-analyst-surface/`:
  PostgREST (read-only REST on :3005) added to phase1 compose; `agent_ro` SELECT-only role;
  `policy/pe-db-preset.yaml` (sandbox → host.docker.internal:3005, GET only);
  `skill/company-db/SKILL.md` (answer from DB, cite source+confidence, founder-only etiquette)
  ▶ user: follow `phase4-analyst-surface/README.md` steps 1–5
- Part 2: transcript intake ✅ DONE 2026-07-12 (end-to-end proven: Telegram transcript →
  inbox → pipeline → sections A/F/G/H filled with transcript:// receipts; extractor
  min-length guard exempted for transcripts) — folder `phase4-analyst-surface/intake/`:
  `transcript_inbox` table (agent's ONLY write, via PostgREST POST), `transcript_processor.py`
  (inbox → MinIO/documents as doc_type 'transcript'), extractor now uses the FULL catalog for
  transcripts (founder-only fields allowed, source_type 'transcript'); policy + skill updated
  ▶ user: `phase4-analyst-surface/intake/README.md`
- Part 3: web dashboard 🔨 built 2026-07-13, awaiting user run — folder
  `phase4-analyst-surface/dashboard/` (FastAPI, container `nemoclaw-dashboard`,
  http://localhost:8090): companies overview w/ completeness bars · per-company profile
  with receipts + "ask the founder" gap list · conflict review queue (keep→verified /
  reject→restore previous) · pipeline health
  ▶ user: `docker compose build dashboard && docker compose up -d dashboard` → open http://localhost:8090

**Phase 5 — Intelligence layer** ✅ part 1 DONE 2026-07-13
- Folder `phase5-intelligence/`: pgvector semantic index (`doc_chunks`, HNSW, 1024-dim
  nvidia/nv-embedqa-e5-v5), `embedder.py` (scheduler runs each cycle), dashboard **Search**
  tab (plain-language search across all documents with company+source receipts);
  Postgres image → pgvector/pgvector:pg16. First index: 998 chunks. Search live at
  http://localhost:8090/search
- Later: knowledge graph (investors ↔ companies ↔ people ↔ competitors); bot access to
  semantic search

**Client-requested upgrades — built 2026-07-19**
1. **Open-source search** — Tavily replaced by **SearXNG** (self-hosted metasearch:
   Google/Bing/DDG/Startpage/Qwant; no key, no quota). `phase3-news-worker/websearch.py`
   is the single search entry point; all 5 search workers route through it, Tavily kept
   only as optional fallback. **Nemotron is now the only external API** (extraction/
   reasoning only — never collection).
2. **LLM-as-a-Verifier** (arXiv:2607.05391, Stanford/UC Berkeley) — `phase2-workers/verifier.py`
   implements all three scaling axes: (a) score granularity via expectation over
   scoring-token logprobs, (b) repeated evaluation (VERIFIER_SAMPLES), (c) criteria
   decomposition into entity / period / unit / value, aggregated as a conjunction
   (weakest criterion caps agreement) with early exit on entity/period mismatch.
   Runs only on candidate conflicts (after the tier fast-path) to control cost.
   Layered on — not replacing — the source-tier thresholds.
3. **Source tiers expanded** — LinkedIn + Y Combinator promoted to tier 2; added GLEIF,
   OpenCorporates, UK Companies House, SEC EDGAR, MCA CDM/data.gov.in, WIPO/Espacenet/
   IP India, NSE/BSE, GST portal.
4. **UI split** — `/company/<slug>` now shows **verified facts only**;
   `/company/<slug>/review` holds unverified + conflicted (with keep/reject).
5. **Discovery agent** (`phase6-analysis/discovery_agent.py`) — reads collected facts +
   raw evidence and proposes NEW schema fields beyond the 103, each with rationale,
   observed example and source. Written to `discovered_fields`; cross-company roll-up
   at `/discoveries`. Proposes only — never writes facts.

Researched, deferred: open-source PPT generation (Presenton / python-pptx template),
RAG framework (lead to provide).

**Phase 6 — Analysis agents** (read the DB only, never scrape)
- Valuation (DCF/comps), Competitive Intelligence, Investment Thesis (bull/bear with linked
  evidence), Risk (litigation, concentration, leadership changes)

## Ground rules
- All project files live inside this `NemoClaw/` folder (NemoClaw itself installs into WSL/Docker — system-level, unavoidable)
- User runs all commands; Claude prepares files and exact commands
- Workers collect, LLM parses, DB is truth; nothing is fabricated — unknown fields stay unknown with a reason
- Financial/cap-table data only from documents/transcripts, never guessed

## Appendix — Phase 0 Windows prep (NVIDIA official steps)
1. Elevated PowerShell: `wsl --install --no-distribution` → reboot if prompted
2. `wsl --install -d Ubuntu-24.04` (do NOT use `--no-launch`), set Unix user, `exit`
3. Verify: `wsl -l -v` → Ubuntu-24.04, VERSION 2
4. Docker Desktop with WSL 2 backend; enable Settings > Resources > WSL integration
5. In WSL: `docker info` should succeed
One-shot alternative: `Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/NVIDIA/NemoClaw/main/scripts/bootstrap-windows.ps1' -OutFile "$env:TEMP\bootstrap-windows.ps1"; powershell.exe -ExecutionPolicy Bypass -File "$env:TEMP\bootstrap-windows.ps1"`

## References
- NemoClaw docs: https://docs.nvidia.com/nemoclaw/user-guide/openclaw/home
- Quickstart: https://docs.nvidia.com/nemoclaw/user-guide/openclaw/get-started/quickstart
- Windows prep: https://docs.nvidia.com/nemoclaw/user-guide/openclaw/get-started/prerequisites/windows-preparation.md
- GitHub: https://github.com/NVIDIA/NemoClaw
