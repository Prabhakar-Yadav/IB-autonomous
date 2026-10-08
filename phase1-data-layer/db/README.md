# Data Layer — Postgres + MinIO

The canonical enterprise database (facts) and object store (raw documents).
Runs as Docker containers alongside the `pe-intake` sandbox.

## Files
- `docker-compose.yml` — Postgres 16 + MinIO + one-shot bucket creator
- `init/01_schema.sql` — tables, constraints, views (runs automatically on first start)
- `init/02_seed_fields.sql` — 103 schema fields seeded from `../schema/company_schema.json`
  (auto-generated — regenerate when the JSON changes, don't hand-edit)

## First start (run in Ubuntu/WSL)

```bash
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db

# set real passwords first (once):
export POSTGRES_PASSWORD='pick-a-strong-password'
export MINIO_PASSWORD='pick-another-strong-password'

docker compose up -d
docker compose ps          # postgres + minio should be 'healthy'; minio-init exits after done
```

## Verify

```bash
# 103 fields seeded?
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
  -c "SELECT count(*) AS fields FROM schema_fields;"

# sections overview
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
  -c "SELECT section_code, section_title, count(*) FROM schema_fields GROUP BY 1,2 ORDER BY 1;"
```

MinIO console: http://localhost:9001 (user `vaultstack`, your MINIO_PASSWORD).

## Notes
- Passwords: exported env vars are used at `docker compose up` time. For a persistent
  setup, put them in a `db/.env` file (`POSTGRES_PASSWORD=...` / `MINIO_PASSWORD=...`) —
  compose reads it automatically. Never commit `.env`.
- The init SQL runs **only on first start** (empty volume). To re-run from scratch:
  `docker compose down -v` (DESTROYS all data) then `up -d`.
- Writing facts: never UPDATE a `field_values` row's value. Flip `is_current` to false
  on the old row and INSERT the new one with `version + 1`. `current_field_values` and
  `company_completeness` views give the live picture.
