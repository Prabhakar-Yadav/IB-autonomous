#!/bin/bash
# Finish the 9B comparison (191/251 already extracted, orphan container
# still finishing the last batch) - continue any stragglers, then move to
# round 2 gap-fill if fields are still missing, then compute final numbers.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=sequretek
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_gapfill_9b_finish.log"
CKPT="$OUT/checkpoint_9b_gapfill.txt"
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
echo "===== 9B FINISH (191/251 already done, continuing) — $(date) ====="
drain "-e NVIDIA_MODEL=nvidia/nvidia-nemotron-nano-9b-v2"
P1_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
P1_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
echo "9B_ROUND1 filled=$P1_FILLED pct=$P1_PCT" >> "$CKPT"
echo "ROUND1_DONE filled=$P1_FILLED pct=$P1_PCT"

echo "=== round 2: gap-fill remaining empty fields, re-extract with 9B ==="
MISSING=$(Q "SELECT 103 - fields_filled FROM company_completeness WHERE slug='$SLUG';")
if [ "$MISSING" -gt 0 ] 2>/dev/null; then
  docker compose run --rm news-worker python gap_filler_worker.py $SLUG --max-fields "$MISSING"
  drain "-e NVIDIA_MODEL=nvidia/nvidia-nemotron-nano-9b-v2"
fi
P2_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
P2_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
echo "9B_ROUND2 filled=$P2_FILLED pct=$P2_PCT" >> "$CKPT"
echo "ROUND2_DONE filled=$P2_FILLED pct=$P2_PCT"

Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/nano_9b_gapfilled_fields.txt"

END=$(date +%s)
{
echo "===== 9B vs 550B — FAIR GAP-FILL COMPARISON (Sequretek, same doc pool) ====="
echo "Nemotron-3-Ultra-550B (2 gap-fill rounds) : 54/103 fields  (52.4%)"
echo "nemotron-nano-9b-v2  (2 gap-fill rounds)  : $P2_FILLED/103 fields  (${P2_PCT}%)"
echo "Total time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_9B_VS_550B_GAPFILLED.txt"
echo "9B_GAPFILL_ALL_DONE" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
