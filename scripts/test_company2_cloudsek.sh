#!/bin/bash
# New company test: CloudSEK (Indian cybersecurity, similar sector/stage to
# Sequretek but heavier public funding-news footprint) — collect docs fresh,
# gap-fill, extract with Ultra-550B (the confirmed winning model), aiming
# for a realistic ~70% completeness given better public documentation.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=cloudsek
NAME="CloudSEK"
URL="https://cloudsek.com"
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_cloudsek.log"
CKPT="$OUT/checkpoint_cloudsek.txt"
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
echo "===== NEW COMPANY TEST: $NAME ($SLUG) — $(date) ====="
diskguard || { echo "ABORT before start: disk too low"; exit 1; }

echo "=== STAGE 0: collect (website + news + registry + linkedin + patents) ==="
docker compose run --rm worker python company_worker.py $SLUG "$NAME" "$URL"
for w in news_worker linkedin_worker registry_worker patents_worker; do
  diskguard || { echo "ABORT mid-collection: disk too low"; break; }
  docker compose run --rm news-worker python $w.py $SLUG
done

DOC_TOTAL=$(Q "SELECT count(*) FROM documents WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');")
echo "documents collected: $DOC_TOTAL"
echo "STAGE0_DONE docs=$DOC_TOTAL" > "$CKPT"

echo "=== STAGE 1: initial gap-fill (broaden coverage before first extraction) ==="
docker compose run --rm news-worker python gap_filler_worker.py $SLUG --max-fields 60

echo "=== STAGE 2: extract with Ultra-550B ==="
drain "-e NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b"
P1_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
P1_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
echo "PASS1 filled=$P1_FILLED pct=$P1_PCT" >> "$CKPT"
echo "STAGE2_DONE filled=$P1_FILLED pct=$P1_PCT"

echo "=== STAGE 3: gap-fill pass 2 targeting remaining empty fields ==="
MISSING=$(Q "SELECT 103 - fields_filled FROM company_completeness WHERE slug='$SLUG';")
docker compose run --rm news-worker python gap_filler_worker.py $SLUG --max-fields "$MISSING"
drain "-e NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b"
P2_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
P2_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
echo "PASS2 filled=$P2_FILLED pct=$P2_PCT" >> "$CKPT"
echo "STAGE3_DONE filled=$P2_FILLED pct=$P2_PCT"

Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/cloudsek_fields.txt"

END=$(date +%s)
{
echo "===== FINAL — $NAME ====="
echo "Documents collected: $DOC_TOTAL"
echo "Fields filled: $P2_FILLED/103  (${P2_PCT}%)"
echo "Total time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_CLOUDSEK_FINAL.txt"
echo "CLOUDSEK_ALL_DONE" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
