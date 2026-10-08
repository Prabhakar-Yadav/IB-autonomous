# VaultStack AI / NemoClaw — Full Project Context Handoff

## What this project is

VaultStack AI (internal codename "NemoClaw") is an AI-native investment-banking
diligence intelligence platform, built to automatically research private
companies and fill out a structured 103-field intake schema (company
overview, funding, leadership, financials, market position, cyber posture,
regulatory exposure, etc.) — the kind of profile an investment-banking or
PE/VC analyst would otherwise compile by hand from public sources.

It runs primarily in Docker on a Windows laptop, with an optional extension
that offloads inference to a separate on-prem GPU box over SSH/Tailscale,
built specifically so confidential deal documents never have to leave local
control or be sent to a third-party API.

## Architecture

- **Docker Compose, multi-service**: Postgres (structured facts + schema),
  MinIO (object storage for raw crawled documents), SearXNG (self-hosted
  metasearch, used instead of a paid search API), a FastAPI dashboard
  (localhost:8090) for company profiles, a conflict-review queue, and a
  "Discoveries" page.
- **8 Docker-orchestrated collection workers** (phase2/phase3): a company
  worker (website crawl), news worker, LinkedIn worker, company-registry
  worker, patents worker, a gap-filler worker (targets specifically-empty
  schema fields with follow-up searches), plus an extraction worker and an
  analysis worker.
- **103-field company schema** stored in Postgres, with per-field
  provenance, confidence scores, and `is_current` versioning (facts are
  never deleted, only superseded, for full audit history).
- **Nemotron-powered extraction engine** (`extractor.py`): pulls raw
  documents from MinIO, chunks them, sends each chunk to an NVIDIA Nemotron
  model with a strict JSON-array-only prompt (using an assistant-turn
  prefill trick — `{"role":"assistant","content":"["}` — to force the model
  to answer instead of narrate), and an anti-hallucination check requiring
  every extracted fact's supporting quote to actually appear in the source
  text.
- **Tiered source-trust and LLM cross-verification system**: facts from
  different documents are cross-checked against each other; when two
  sources disagree, the system flags a conflict rather than silently
  picking one, and routes it to a human-reviewable conflict queue.
- **Schema-discovery agent** (`discovery_agent.py`): a separate LLM pass
  that looks at everything collected for a company and proposes NEW fields
  beyond the fixed 103 — things a deal team would materially want but the
  schema has no column for — with a rationale, an observed example value,
  and a source, so a human can accept or reject. Proposals seen across
  multiple companies roll up as candidates to formally add to the schema.
- **Hardened against real production failure modes**:
  - A hard per-document wall-clock deadline (`SIGALRM`, run in the main
    thread) after a malformed page once burned 96% CPU for hours — thread
    timeouts can't interrupt CPU-bound code, but a Unix signal can.
  - A rotating multi-API-key client with retry/backoff that distinguishes
    real quota errors (429, switch keys) from shared-endpoint saturation
    (503 "Worker local total request limit reached" — switching keys does
    nothing, only patient backoff helps).
  - A disk-space guard on every long-running batch script, since Docker
    silently corrupts/crashes when the host disk fills.
  - Checkpoint files written after every pipeline stage so a multi-hour run
    can survive a crash, a laptop shutdown, or a manual stop, and resume
    from exactly where it left off without re-crawling anything already
    collected.

## The on-prem / privacy-preserving extension

For a specific confidential client dataset, a fully separate on-prem RAG
pipeline was built with a hard constraint that documents must never be read
by an external AI API at all — every step (Docling for OCR/PDF parsing,
bge-m3 for embeddings, local LLMs served via Ollama) runs on a GPU box the
user controls, reached from the Docker Compose stack over an SSH tunnel
(`host.docker.internal:11434` forwarded to the box's Ollama, which only
binds to localhost and has no public exposure).

## The model comparison work (most recent phase)

The core open question: is it worth switching the pipeline's primary model
from the large hosted **Nemotron-3-Ultra-550B** to the much smaller
**Nemotron-Nano-9B-v2**, for cost/latency/on-prem-feasibility reasons?

Methodology: collect one company's source documents exactly once, then run
the full extraction pipeline against the *identical* document set multiple
times, once per model, resetting `field_values.is_current` and re-queuing
documents between runs so any difference in fields-filled isolates the
model's contribution rather than data differences.

**Test company: Sequretek** (Indian cybersecurity startup, moderate public
footprint). Result on the same 186 documents:
- Nemotron-3-Ultra-550B: 42/103 fields (40.8%)
- Nemotron-Nano-9B-v2: 34/103 fields (33.0%)

Two further gap-fill rounds (targeting specifically-still-empty fields with
more documents, extracted with the winning 550B model) pushed completeness
to 54/103 (52.4%) — the remaining gap is largely non-public information
(detailed financials, cap table, internal metrics) that no amount of
additional web crawling will surface.

A second head-to-head is currently in progress: running the *same*
two-round gap-fill process with Nano-9B-v2 on the identical final document
pool that got 550B to 52.4%, to get a truly fair before/after number for
the model-switch decision.

## Real engineering bugs found and fixed during this work

1. **A drain-loop ordering bug**: the batch-extraction retry loop checked
   "are there any documents left to process?" *before* releasing documents
   stuck in a `'processing'` state from a previously-killed container. When
   a run was interrupted, the loop saw zero `'new'` documents (they were
   all sitting in `'processing'`) and wrongly declared the stage complete
   after processing only a fraction of the batch. Fixed by reordering: release
   stuck rows first, then count.
2. **A silent model/reasoning-content bug**: `nemotron-nano-9b-v2` is a
   reasoning model that, combined with the JSON-array prefill trick used to
   force structured output, was returning its actual answer inside a
   separate `reasoning_content` field instead of `content`. The extraction
   code only ever read `.content`, saw nothing, and logged every single
   chunk as "no facts" — producing a false 0% result that looked
   identical to a genuine "the model found nothing" outcome. Caught by
   direct diagnostic testing (not accepting the number at face value),
   root-caused, and fixed with a fallback: read `reasoning_content` when
   `content` is empty.
3. A crash-looping, unrelated container (from a separate PE-intake project
   in the same Docker environment) that had restarted over 4,500 times was
   identified as the likely cause of repeated critical disk-space
   emergencies (drive dropping from double-digit GB free to under 500MB
   within hours) — flagged rather than touched, since it belonged to a
   different active project.

## Skills/technologies demonstrated across this project

Docker & Docker Compose multi-service orchestration, PostgreSQL schema
design with fact-versioning/provenance, MinIO/S3-compatible object storage,
prompt engineering for structured JSON extraction from LLMs (including
handling reasoning-model quirks), building retry/backoff logic around a
shared rate-limited API, SSH tunneling and on-prem GPU inference via
Ollama, self-healing/checkpoint-driven long-running batch pipelines,
disk-space and resource-safety guards for unattended overnight runs, root-
cause debugging of silent data-pipeline failures rather than accepting
face-value results, and running controlled, reproducible A/B model
comparisons (same-input methodology) to make an evidence-based
build-vs-buy / model-selection decision for a real team.

## Status as of this handoff

The Nano-9B-v2 vs Ultra-550B gap-filled comparison is actively running
(background, checkpointed, ETA ~2-2.5 hours from launch). Once complete it
will give the team a final, fair number to base the on-prem model-switch
decision on. A message summarizing the comparison and two open questions
(should we run 9B on-prem instead of via API, and has the VC/PE partner
shared real deal data to test against) has already been sent to the
project lead.
