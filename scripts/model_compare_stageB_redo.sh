#!/bin/bash
# Redo of Stage B (nano-9b-v2) only, after fixing a real bug in extractor.py's
# _call(): this model puts its actual JSON answer in reasoning_content instead
# of content (because of our "[" prefill trick), and the old code only ever
# read .content, so every chunk logged "no facts" -> false 0.0% result.
# Waits for the Stage A redo to finish first (shares the same DB rows).
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=sequretek
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_api_stageB_redo.log"
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
echo "===== STAGE B REDO (fixed reasoning_content bug) — Sequretek — $(date) ====="

echo "=== waiting for Stage A redo to finish first ==="
while ! grep -q 'STAGEA_REDO_DONE' "$CKPT" 2>/dev/null; do
  sleep 60
done
A_LINE=$(grep '^ULTRA_550B_REDO ' "$CKPT" | tail -1)
echo "Stage A redo confirmed done ($A_LINE) — proceeding with Stage B redo"

echo "=== reset for clean, FULL nano-9b-v2 pass (with the bug fixed) ==="
Q "UPDATE field_values SET is_current=false WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND is_current;" >/dev/null
Q "UPDATE documents SET status='new', processed_at=NULL WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null

echo "=== STAGE B REDO: extract with HOSTED nvidia-nemotron-nano-9b-v2 (API key, fixed) ==="
drain "-e NVIDIA_MODEL=nvidia/nvidia-nemotron-nano-9b-v2"
B_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
B_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/nano_9b_fields_REDO.txt"
echo "NANO_9B_REDO filled=$B_FILLED pct=$B_PCT" >> "$CKPT"
echo "STAGEB_REDO_DONE filled=$B_FILLED pct=$B_PCT"

END=$(date +%s)
{
echo "===== TRUE FINAL COMPARISON — Sequretek (full 186 documents, both bugs fixed) ====="
echo "Nemotron-3-Ultra-550B (hosted) : $A_LINE"
echo "nemotron-nano-9b-v2 (hosted)   : filled=$B_FILLED pct=$B_PCT"
echo "Total redo time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_TRUE_FINAL.txt"
echo "TRUE_ALL_DONE" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
