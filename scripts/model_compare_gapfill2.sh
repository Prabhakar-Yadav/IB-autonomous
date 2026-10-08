#!/bin/bash
# Second, more aggressive gap-fill pass targeting all remaining empty fields
# (not just top 60), plus a discovery-agent pass to surface new fields beyond
# the 103-field schema that the evidence supports but has no column for.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=sequretek
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_gapfill2.log"
CKPT="$OUT/checkpoint_api.txt"
Q(){ docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -tAc "$1" 2>/dev/null; }
diskguard(){ FREE=$(df -BG --output=avail /mnt/c 2>/dev/null | tail -1 | tr -dc '0-9')
  [ -n "$FREE" ] && [ "$FREE" -lt 1 ] && { echo "!!! ABORT: only ${FREE}GB disk"; return 1; }; return 0; }
drain(){ local extra_args="$1"
  for i in $(seq 1 40); do
    diskguard || return 1
    Q "UPDATE documents SET status='new', processed_at=NULL WHERE status='processing' AND company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null
    N=$(Q "SELECT count(*) FROM documents WHERE status='new' AND company_id=(SELECT id FROM companies WHERE slug='$SLUG');")
    [ -z "$N" ] && { echo "!!! DB not responding"; return 1; }
    [ "$N" = "0" ] && break
    echo "  drain $i: $N queued $(date +%H:%M:%S)"
    docker compose run --rm $extra_args worker python extractor.py --limit 999
  done
}

START=$(date +%s)
{
echo "===== GAP-FILL PASS #2 (target ALL remaining empty fields) — $(date) ====="

MISSING=$(Q "SELECT 103 - fields_filled FROM company_completeness WHERE slug='$SLUG';")
echo "empty fields remaining before this pass: $MISSING"

echo "=== gap-filler: fetch docs for ALL remaining empty fields ==="
docker compose run --rm news-worker python gap_filler_worker.py $SLUG --max-fields "$MISSING"

echo "=== extract newly-fetched documents with Ultra-550B ==="
drain "-e NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b"

echo ""
echo "=== discovery agent: surface new fields beyond the 103-field schema ==="
docker compose run --rm analysis python discovery_agent.py $SLUG --max-docs 20

FINAL_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
FINAL_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/final_profile_fields_v2.txt"

Q "SELECT label||' | section='||section_hint||' | conf='||confidence||' | '||rationale||' | example: '||example_value
   FROM discovered_fields
   WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG')
   ORDER BY confidence DESC;" > "$OUT/discovered_new_fields.txt"
DISCOVERED_COUNT=$(Q "SELECT count(*) FROM discovered_fields WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');")

END=$(date +%s)
{
echo "===== GAP-FILL PASS #2 FINAL — Sequretek ====="
echo "Fields filled (of 103 standard): $FINAL_FILLED  (${FINAL_PCT}%)"
echo "New fields discovered beyond the 103 schema: $DISCOVERED_COUNT"
echo "Pass time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_GAPFILL2_FINAL.txt"
echo "GAPFILL2_ALL_DONE" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
