#!/bin/bash
# Fair model comparison: ONE company, ONE set of collected documents,
# extracted TWICE — once with hosted Nemotron-3-Ultra-550B (current default),
# once with local Nemotron-Nano-9B (on-prem, box VRAM, via SSH tunnel to
# host.docker.internal:11434). Same evidence both times -> isolates the
# model's contribution. Results checkpointed to disk after every stage.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=sequretek
NAME="Sequretek"
URL="https://www.sequretek.com"
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
mkdir -p "$OUT"
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
echo "===== MODEL COMPARISON: $NAME ($SLUG) — $(date) ====="
docker compose build worker news-worker >/dev/null 2>&1

echo "=== STAGE 0: collect (website + news + registry + linkedin + patents) ==="
docker compose run --rm worker python company_worker.py $SLUG "$NAME" "$URL"
for w in news_worker linkedin_worker registry_worker patents_worker; do
  docker compose run --rm news-worker python $w.py $SLUG
done

echo "=== STAGE 1: broaden coverage (gap-fill once, shared by both models) ==="
docker compose run --rm news-worker python gap_filler_worker.py $SLUG --max-fields 60

DOC_TOTAL=$(Q "SELECT count(*) FROM documents WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');")
echo "documents collected: $DOC_TOTAL"
echo "STAGE0_DONE docs=$DOC_TOTAL" > "$OUT/checkpoint.txt"

echo ""
echo "=== STAGE 2: extract with HOSTED Nemotron-3-Ultra-550B ==="
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

END=$(date +%s)
{
echo "===== COMPARISON — $NAME ====="
echo "Documents used (identical for both): $DOC_TOTAL"
echo "Hosted Nemotron-3-Ultra-550B : $HOSTED_FILLED/103 fields  (${HOSTED_PCT}%)"
echo "Local  Nemotron-Nano-9B      : $LOCAL_FILLED/103 fields  (${LOCAL_PCT}%)"
echo "Total time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON.txt"
echo "ALL_DONE" >> "$OUT/checkpoint.txt"
} 2>&1 | tee -a "$LOG"
