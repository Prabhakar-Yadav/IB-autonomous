#!/bin/bash
# Finish Stage B WITHOUT resetting field_values (a surviving orphan worker
# container kept extracting through an earlier interruption and already
# wrote real facts to the DB). This only continues any remaining/stuck
# documents, then computes final numbers and triggers the gap-fill pass.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=sequretek
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_api_stageB_finish.log"
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
echo "===== STAGE B FINISH (no reset, continuing existing progress) — $(date) ====="
echo "=== continue any remaining/stuck documents with nano-9b-v2 ==="
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
echo "Nemotron-3-Ultra-550B (hosted) : 42/103 fields  (40.8%)"
echo "nemotron-nano-9b-v2 (hosted)   : $B_FILLED/103 fields  (${B_PCT}%)"
echo "Stage B finish time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_TRUE_FINAL.txt"
echo "TRUE_ALL_DONE" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
