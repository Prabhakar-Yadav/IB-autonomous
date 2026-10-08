#!/bin/bash
# Final comparison, API-key only (no local box/tunnel dependency):
# Nemotron-3-Ultra-550B (hosted) vs nvidia-nemotron-nano-9b-v2 (hosted).
# Same 186 already-collected Sequretek documents, re-extracted from scratch
# for each model. Results checkpointed to disk after every stage.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=sequretek
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_api.log"
CKPT="$OUT/checkpoint_api.txt"
Q(){ docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -tAc "$1" 2>/dev/null; }
diskguard(){ FREE=$(df -BG --output=avail /mnt/c 2>/dev/null | tail -1 | tr -dc '0-9')
  [ -n "$FREE" ] && [ "$FREE" -lt 2 ] && { echo "!!! ABORT: only ${FREE}GB disk"; return 1; }; return 0; }
drain(){ local extra_args="$1"
  for i in $(seq 1 40); do
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
echo "===== API-ONLY COMPARISON — Sequretek — $(date) ====="
echo "(reusing 186 already-collected documents, no re-crawl, no local box)"

echo "=== reset for clean Ultra-550B pass ==="
Q "UPDATE field_values SET is_current=false WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND is_current;" >/dev/null
Q "UPDATE documents SET status='new', processed_at=NULL WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null

echo "=== STAGE A: extract with HOSTED Nemotron-3-Ultra-550B (API key) ==="
drain "-e NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b"
A_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
A_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/ultra_550b_fields.txt"
echo "ULTRA_550B filled=$A_FILLED pct=$A_PCT" >> "$CKPT"
echo "STAGEA_DONE filled=$A_FILLED pct=$A_PCT"

echo ""
echo "=== reset for nano-9b-v2 pass (same 186 documents) ==="
Q "UPDATE field_values SET is_current=false WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND is_current;" >/dev/null
Q "UPDATE documents SET status='new', processed_at=NULL WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null

echo "=== STAGE B: extract with HOSTED nvidia-nemotron-nano-9b-v2 (API key) ==="
drain "-e NVIDIA_MODEL=nvidia/nvidia-nemotron-nano-9b-v2"
B_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
B_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/nano_9b_fields.txt"
echo "NANO_9B filled=$B_FILLED pct=$B_PCT" >> "$CKPT"
echo "STAGEB_DONE filled=$B_FILLED pct=$B_PCT"

END=$(date +%s)
{
echo "===== FINAL COMPARISON (API-only) — Sequretek (186 identical documents) ====="
echo "Nemotron-3-Ultra-550B (hosted) : $A_FILLED/103 fields  (${A_PCT}%)"
echo "nemotron-nano-9b-v2 (hosted)   : $B_FILLED/103 fields  (${B_PCT}%)"
echo "Total time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_API.txt"
echo "ALL_DONE" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
