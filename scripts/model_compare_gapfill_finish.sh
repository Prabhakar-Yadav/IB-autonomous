#!/bin/bash
# Finish the gap-fill extraction WITHOUT resetting (an orphan container kept
# working through the earlier stop and already wrote real facts). Continues
# any remaining/stuck documents, then computes final completeness.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=sequretek
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_gapfill_finish.log"
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
echo "===== GAP-FILL FINISH (no reset, continuing) — $(date) ====="
echo "=== continue any remaining/stuck documents with Ultra-550B (the winning model) ==="
drain "-e NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b"

FINAL_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
FINAL_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/final_profile_fields.txt"

END=$(date +%s)
{
echo "===== FINAL PROFILE (post gap-fill, Nemotron-3-Ultra-550B) ====="
echo "Fields filled: $FINAL_FILLED/103  (${FINAL_PCT}%)"
echo "Gap-fill finish time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_GAPFILLED_FINAL.txt"
echo "GAPFILL_ALL_DONE" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
