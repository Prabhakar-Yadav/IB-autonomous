#!/bin/bash
# Resume the model comparison exactly where it left off (Stage 2 completion +
# Stage 3 reset + Stage 4 local-model extraction). Documents already collected
# and NOT re-crawled — only extraction continues.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=sequretek
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run.log"
Q(){ docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -tAc "$1" 2>/dev/null; }
diskguard(){ FREE=$(df -BG --output=avail /mnt/c 2>/dev/null | tail -1 | tr -dc '0-9')
  [ -n "$FREE" ] && [ "$FREE" -lt 3 ] && { echo "!!! ABORT: only ${FREE}GB disk"; return 1; }; return 0; }
drain(){ local extra_args="$1"
  for i in $(seq 1 30); do
    diskguard || return 1
    N=$(Q "SELECT count(*) FROM documents WHERE status='new' AND company_id=(SELECT id FROM companies WHERE slug='$SLUG');")
    [ -z "$N" ] && { echo "!!! DB not responding"; return 1; }
    [ "$N" = "0" ] && break
    echo "  drain $i: $N queued $(date +%H:%M:%S)"
    Q "UPDATE documents SET status='new', processed_at=NULL WHERE status='processing' AND company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null
    docker compose run --rm $extra_args worker python extractor.py --limit 999
  done
}

START=$(date +%s)
{
echo "===== RESUME — $(date) ====="

echo "=== finishing STAGE 2: hosted Nemotron-3-Ultra-550B ==="
Q "UPDATE documents SET status='new', processed_at=NULL WHERE status='processing' AND company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null
drain ""
HOSTED_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
HOSTED_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/hosted_550b_fields.txt"
echo "HOSTED_550B filled=$HOSTED_FILLED pct=$HOSTED_PCT" >> "$OUT/checkpoint.txt"
echo "STAGE2_DONE hosted_filled=$HOSTED_FILLED hosted_pct=$HOSTED_PCT"

echo ""
echo "=== STAGE 3: reset field_values, re-queue SAME documents for local model ==="
Q "UPDATE field_values SET is_current=false WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND is_current;" >/dev/null
Q "UPDATE documents SET status='new', processed_at=NULL WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null
echo "STAGE3_DONE" >> "$OUT/checkpoint.txt"

echo ""
echo "=== STAGE 4: extract with LOCAL Nemotron-Nano-9B (on-prem, box VRAM) ==="
drain "-e NVIDIA_BASE_URL=http://host.docker.internal:11434/v1 -e NVIDIA_MODEL=nemotron-nano"
LOCAL_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
LOCAL_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/local_9b_fields.txt"
echo "LOCAL_9B filled=$LOCAL_FILLED pct=$LOCAL_PCT" >> "$OUT/checkpoint.txt"
echo "STAGE4_DONE local_filled=$LOCAL_FILLED local_pct=$LOCAL_PCT"

DOC_TOTAL=$(Q "SELECT count(*) FROM documents WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');")
END=$(date +%s)
{
echo "===== COMPARISON — Sequretek ====="
echo "Documents used (identical for both): $DOC_TOTAL"
echo "Hosted Nemotron-3-Ultra-550B : $HOSTED_FILLED/103 fields  (${HOSTED_PCT}%)"
echo "Local  Nemotron-Nano-9B      : $LOCAL_FILLED/103 fields  (${LOCAL_PCT}%)"
echo "Resume-stage time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON.txt"
echo "ALL_DONE" >> "$OUT/checkpoint.txt"
} 2>&1 | tee -a "$LOG"
