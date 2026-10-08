# Workers & Extraction Engine — Phase 2

Two programs, strict separation of duties (product-doc rule):

- `company_worker.py` — **collects only**: crawls a company website (up to 15 pages
  + 5 PDFs, priority to about/products/pricing/press/careers), stores raw copies in
  MinIO, catalogs them in `documents`. Skips anything whose content hash is unchanged.
- `extractor.py` — **parses only**: takes pending documents, cleans HTML/PDF to text,
  chunks it, asks Nemotron for facts matching the schema catalog, **validates** every
  candidate (known field? web-allowed? not founder_only? confidence sane?), and writes
  survivors to `field_values` with full provenance (source URL, document, confidence,
  version). Facts for founder-only fields are impossible by construction — those fields
  are never in the catalog sent to the model.

`common.py` holds shared DB/MinIO connections. Runs inside the `worker` container
defined in `../phase1-data-layer/db/docker-compose.yml`.

## Run the vertical slice (Ubuntu/WSL)

```bash
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db

# same passwords as Phase 1, plus your NVIDIA key (from your .env note):
export POSTGRES_PASSWORD='...'
export MINIO_PASSWORD='...'
export NVIDIA_API_KEY='nvapi-...'

docker compose build worker                 # once, ~1 min

# 1) collect
docker compose run --rm worker python company_worker.py zoho "Zoho" https://www.zoho.com
docker compose run --rm worker python company_worker.py razorpay "Razorpay" https://razorpay.com
docker compose run --rm worker python company_worker.py dhruva-space "Dhruva Space" https://www.dhruvaspace.com

# 2) extract (each run processes up to N pending docs; repeat until none left)
docker compose run --rm worker python extractor.py --limit 10
```

## See the results

```bash
# completeness per company
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
  -c "SELECT slug, fields_filled, completeness_pct FROM company_completeness;"

# facts for one company, with provenance
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
  -c "SELECT section_code, label, left(value_text, 60) AS value, confidence, source_url
      FROM current_field_values WHERE slug = 'zoho' ORDER BY section_code, label;"

# pipeline state
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
  -c "SELECT status, count(*) FROM documents GROUP BY 1;"
```

## Expectations
- Web sources can realistically fill ~30 of 103 fields (snapshot, product, team,
  competitors, certifications…). Financials/cap-table stay empty **by design** —
  they only come from documents/transcripts/founder via later phases.
- Extraction costs NVIDIA API credits: ~6 chunks/doc max, so a 15-page crawl ≈
  ≤90 model calls. Start with one company if you want to watch it closely.
