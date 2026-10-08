#!/bin/bash
# Gap-fill pass for Tailored AI: target remaining empty fields with more
# documents, extract with Qwen3.5-27B (the winning model from the 3-way
# test: 66/103, 64.1%). This is now the standard final step for every
# company test going forward.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=tailoredai
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_tailoredai_gapfill.log"
CKPT="$OUT/checkpoint_tailoredai.txt"
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
TUNNEL="-e NVIDIA_BASE_URL=http://host.docker.internal:11435/v1 -e NVIDIA_MODEL=qwen3.5:27b"

START=$(date +%s)
{
echo "===== TAILORED AI GAP-FILL (Qwen3.5-27B) — $(date) ====="
MISSING=$(Q "SELECT 103 - fields_filled FROM company_completeness WHERE slug='$SLUG';")
echo "empty fields remaining before gap-fill: $MISSING"

docker compose run --rm news-worker python gap_filler_worker.py $SLUG --max-fields "$MISSING"
drain "$TUNNEL"

FINAL_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
FINAL_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/tailoredai_gapfilled_fields.txt"

END=$(date +%s)
{
echo "===== TAILORED AI — GAP-FILLED FINAL (Qwen3.5-27B) ====="
echo "Fields filled: $FINAL_FILLED/103  (${FINAL_PCT}%)"
echo "Pass time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_TAILOREDAI_GAPFILLED.txt"
echo "TAILOREDAI_GAPFILL_DONE filled=$FINAL_FILLED pct=$FINAL_PCT" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
