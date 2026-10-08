#!/bin/bash
# Final corrected comparison: EXPLICIT Nemotron-3-Ultra-550B (hosted) vs
# qwen3:8b (local, on-prem box VRAM, ~9B-class substitute for Nemotron-Nano
# which failed to load on this box's Ollama — see notes). SAME 186 already-
# collected documents used for both, re-extracted from scratch each time.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=sequretek
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_final.log"
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
echo "===== FINAL COMPARISON — Sequretek — $(date) ====="
echo "(reusing 186 already-collected documents, no re-crawl)"

echo "=== reset for clean Nemotron-3-Ultra-550B (hosted) pass ==="
Q "UPDATE field_values SET is_current=false WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND is_current;" >/dev/null
Q "UPDATE documents SET status='new', processed_at=NULL WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null

echo "=== STAGE A: extract with HOSTED Nemotron-3-Ultra-550B (explicit) ==="
drain "-e NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b"
A_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
A_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/ultra_550b_fields.txt"
echo "ULTRA_550B filled=$A_FILLED pct=$A_PCT" >> "$OUT/checkpoint2.txt"
echo "STAGEA_DONE filled=$A_FILLED pct=$A_PCT"

echo ""
echo "=== reset for local qwen3:8b pass (same 186 documents) ==="
Q "UPDATE field_values SET is_current=false WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND is_current;" >/dev/null
Q "UPDATE documents SET status='new', processed_at=NULL WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null

echo "=== STAGE B: extract with LOCAL qwen3:8b (on-prem, box VRAM, via tunnel) ==="
drain "-e NVIDIA_BASE_URL=http://host.docker.internal:11434/v1 -e NVIDIA_MODEL=qwen3:8b"
B_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
B_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/qwen3_8b_fields.txt"
echo "QWEN3_8B filled=$B_FILLED pct=$B_PCT" >> "$OUT/checkpoint2.txt"
echo "STAGEB_DONE filled=$B_FILLED pct=$B_PCT"

END=$(date +%s)
{
echo "===== FINAL COMPARISON — Sequretek (186 identical documents) ====="
echo "Nemotron-3-Ultra-550B (hosted) : $A_FILLED/103 fields  (${A_PCT}%)"
echo "qwen3:8b (local, on-prem GPU)  : $B_FILLED/103 fields  (${B_PCT}%)"
echo "Total time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_FINAL.txt"
echo "ALL_DONE" >> "$OUT/checkpoint2.txt"
} 2>&1 | tee -a "$LOG"
