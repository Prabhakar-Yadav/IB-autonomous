# Phase 4 (part 2) — Telegram transcript intake

Analysts send founder-call transcripts / meeting notes to the Telegram bot; the
facts (including founder-only financials & cap-table) end up in the database.

Trust design — the sandboxed agent never writes facts:

```
analyst → Telegram → agent POSTs raw text → transcript_inbox (its ONLY write)
        → transcript_processor (trusted) → MinIO + documents (doc_type 'transcript')
        → extractor (trusted; FULL catalog incl. founder-only) → field_values
```

## Setup (once, in Ubuntu/WSL)

```bash
# 1. inbox table + agent grants (+ PostgREST schema reload)
docker exec -i nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
  < "/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase4-analyst-surface/intake/sql/10_transcript_inbox.sql"

# 2. re-apply the (updated) network policy — now allows POST /transcript_inbox
nemoclaw pe-intake policy-add --from-file \
  "/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase4-analyst-surface/policy/pe-db-preset.yaml" --yes

# 3. re-upload the (updated) skill
openshell sandbox upload pe-intake \
  "/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase4-analyst-surface/skill/company-db/SKILL.md" \
  /sandbox/.openclaw/workspace/skills/company-db/SKILL.md
```

## Test

1. On Telegram (fresh session), send the bot a fake founder-call transcript, e.g.:
   *"Transcript of call with Dhruva Space founder: our FY25 revenue was ₹18 crore,
   we're raising $15M Series B at ~$120M pre-money, ESOP pool is 8%..."*
   and tell it which company it's for.
2. Bot should confirm it filed the transcript (not state the facts itself).
3. Process it (or wait for the next scheduler cycle):
   ```bash
   docker compose run --rm scheduler python /app/phase4/transcript_processor.py
   docker compose run --rm worker python extractor.py --limit 5
   ```
4. Verify founder-only fields landed with source_type 'transcript':
   ```bash
   docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel \
     -c "SELECT label, left(value_text,50) AS value, source_type, confidence
         FROM current_field_values WHERE slug='dhruva-space' AND section_code IN ('F','G','H');"
   ```
5. Ask the bot on Telegram: "What is Dhruva Space raising?" — it should now answer,
   citing the transcript source.
