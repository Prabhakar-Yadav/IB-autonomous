#!/bin/bash
# After BOTH stages of the comparison finish, push the company's real profile
# completeness higher: run the gap-filler (targets docs specifically for
# fields still empty) then re-extract with whichever model scored higher.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=sequretek
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_gapfill.log"
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
echo "===== GAP-FILL PASS — Sequretek — $(date) ====="

echo "=== waiting for Stage B (nano-9b-v2) to finish first ==="
while ! grep -q 'TRUE_ALL_DONE' "$CKPT" 2>/dev/null; do
  sleep 60
done
echo "Both stages confirmed done — comparing scores"

A_PCT=40.8   # Nemotron-3-Ultra-550B, confirmed
B_LINE=$(grep '^NANO_9B_REDO ' "$CKPT" | tail -1)
B_PCT=$(echo "$B_LINE" | grep -oE 'pct=[0-9.]+' | cut -d= -f2)
echo "Ultra-550B: ${A_PCT}%   nano-9b-v2: ${B_PCT}%"

WINNER_MODEL="nvidia/nemotron-3-ultra-550b-a55b"
WINNER_NAME="Nemotron-3-Ultra-550B"
if (( $(echo "$B_PCT > $A_PCT" | bc -l 2>/dev/null || echo 0) )); then
  WINNER_MODEL="nvidia/nvidia-nemotron-nano-9b-v2"
  WINNER_NAME="nemotron-nano-9b-v2"
fi
echo "Winner: $WINNER_NAME — will gap-fill + finalize profile with this model"

echo "=== gap-filler: fetch documents targeted at still-empty fields ==="
docker compose run --rm news-worker python gap_filler_worker.py $SLUG --max-fields 60

echo "=== extract newly-fetched gap-fill documents with $WINNER_NAME ==="
drain "-e NVIDIA_MODEL=$WINNER_MODEL"

FINAL_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
FINAL_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/final_profile_fields.txt"

END=$(date +%s)
{
echo "===== FINAL PROFILE (post gap-fill, using $WINNER_NAME) ====="
echo "Fields filled: $FINAL_FILLED/103  (${FINAL_PCT}%)"
echo "Gap-fill time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_GAPFILLED_FINAL.txt"
echo "GAPFILL_ALL_DONE" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
