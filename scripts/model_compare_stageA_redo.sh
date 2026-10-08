#!/bin/bash
# Redo of Stage A (Ultra-550B) only, with the drain-loop bug fixed:
# the original drain() checked "any 'new' docs left?" BEFORE resetting
# stuck 'processing' rows back to 'new'. If a worker container died
# mid-batch, all remaining docs sat in 'processing' (not 'new'), the
# count read 0, and the loop wrongly declared the stage done after only
# ~16/186 documents. Fixed order: reset stuck rows FIRST, then count.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=sequretek
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_api_stageA_redo.log"
CKPT="$OUT/checkpoint_api.txt"
Q(){ docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -tAc "$1" 2>/dev/null; }
diskguard(){ FREE=$(df -BG --output=avail /mnt/c 2>/dev/null | tail -1 | tr -dc '0-9')
  [ -n "$FREE" ] && [ "$FREE" -lt 2 ] && { echo "!!! ABORT: only ${FREE}GB disk"; return 1; }; return 0; }
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
echo "===== STAGE A REDO (fixed drain bug) — Sequretek — $(date) ====="

# Wait for the currently-running Stage B (nano-9b-v2) to finish first —
# both stages share the same documents/field_values rows for this company.
echo "=== waiting for Stage B (nano-9b-v2) to finish first ==="
while ! grep -q 'ALL_DONE' "$CKPT" 2>/dev/null; do
  sleep 60
done
echo "Stage B confirmed done — proceeding with Stage A redo"

echo "=== reset for clean, FULL Ultra-550B pass ==="
Q "UPDATE field_values SET is_current=false WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND is_current;" >/dev/null
Q "UPDATE documents SET status='new', processed_at=NULL WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null

echo "=== STAGE A REDO: extract with HOSTED Nemotron-3-Ultra-550B (API key, full 186 docs) ==="
drain "-e NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b"
A_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
A_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/ultra_550b_fields_REDO.txt"
echo "ULTRA_550B_REDO filled=$A_FILLED pct=$A_PCT" >> "$CKPT"
echo "STAGEA_REDO_DONE filled=$A_FILLED pct=$A_PCT"

# pull Stage B's already-saved numbers from checkpoint for the final report
B_LINE=$(grep '^NANO_9B ' "$CKPT" | tail -1)

END=$(date +%s)
{
echo "===== CORRECTED FINAL COMPARISON — Sequretek (full 186 documents, both stages) ====="
echo "Nemotron-3-Ultra-550B (hosted) : $A_FILLED/103 fields  (${A_PCT}%)"
echo "nemotron-nano-9b-v2 (hosted)   : $B_LINE"
echo "Total redo time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_CORRECTED.txt"
echo "REDO_ALL_DONE" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
