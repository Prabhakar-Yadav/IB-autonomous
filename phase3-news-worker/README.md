# Phase 3 — News Worker + Cross-Validation

Two upgrades:

1. **News Worker** (`news_worker.py`, this folder) — collect-only, like the company
   worker, but it reads the *press* instead of the company's own site. For each company
   it runs 5 targeted searches via the Tavily API (funding, valuation, acquisitions,
   lawsuits/regulators, leadership), fetches the articles (falls back to the search
   snippet for paywalled ones), stores raw copies in MinIO, catalogs as doc_type `news`.
2. **Cross-validation** (upgraded `phase2-workers/extractor.py`) — when writing a fact:
   - same value from an **independent source** → status `verified` (verified_by =
     cross-validator), confidence raised
   - different value from the **same source** → source updated itself; normal supersede
   - different value vs a **verified** fact → verified fact stays; dissent filed as a
     non-current `conflicted` row for human review
   - two single sources disagreeing → newer wins but flagged `conflicted`
   Also hardened the extraction prompt: no copyright/footer dates as facts; only facts
   about the target company (news articles mention many companies).

## Run it (Ubuntu/WSL)

```bash
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db
export POSTGRES_PASSWORD='...'; export MINIO_PASSWORD='...'
export NVIDIA_API_KEY='nvapi-...'; export TAVILY_API_KEY='tvly-...'

docker compose build news-worker      # once
docker compose build worker           # rebuild: extractor gained cross-validation

# collect news (per company)
docker compose run --rm news-worker python news_worker.py razorpay
docker compose run --rm news-worker python news_worker.py zoho
docker compose run --rm news-worker python news_worker.py dhruva-space

# extract + cross-validate (repeat until 0 documents)
docker compose run --rm worker python extractor.py --limit 15
```

## Inspect the outcome

```bash
# verified facts (two independent sources agreed)
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
  -c "SELECT slug, label, left(value_text,50) AS value, confidence
      FROM current_field_values WHERE status='verified' ORDER BY slug;"

# conflicts needing a human eye
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
  -c "SELECT slug, label, left(value_text,50) AS value, source_url
      FROM current_field_values WHERE status='conflicted' ORDER BY slug;"

# updated scoreboard
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
  -c "SELECT slug, fields_filled, completeness_pct FROM company_completeness;"
```

## One-time data fix (analyst override)
The bad "Razorpay incorporated 2024" fact (misread from a pricing-page date; the
prompt now forbids this class of error) — retire it:

```bash
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
  -c "UPDATE field_values SET is_current=false, status='rejected'
      WHERE id=(SELECT fv.id FROM field_values fv JOIN companies c ON c.id=fv.company_id
                WHERE c.slug='razorpay' AND fv.field_id='date_of_incorporation' AND fv.is_current);"
```
(The news pass will likely refill it correctly — funding articles usually state
"founded in 2014".)
