#!/bin/bash
# Complete the Tailored AI test: same 48 documents, extract with Qwen3.5-9B
# and Qwen3.5-27B (both local on the GPU box via tunnel), resetting between
# runs. Results write to the normal companies table (visible on dashboard).
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=tailoredai
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_tailoredai_full.log"
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
TUNNEL="-e NVIDIA_BASE_URL=http://host.docker.internal:11435/v1"

START=$(date +%s)
{
echo "===== TAILORED AI — completing full model test — $(date) ====="

echo "=== reset, extract with Qwen3.5-9B ==="
Q "UPDATE field_values SET is_current=false WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND is_current;" >/dev/null
Q "UPDATE documents SET status='new', processed_at=NULL WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null
drain "$TUNNEL -e NVIDIA_MODEL=qwen3.5:latest"
Q9_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
Q9_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
echo "QWEN9B filled=$Q9_FILLED pct=$Q9_PCT" >> "$CKPT"
echo "QWEN9B_DONE filled=$Q9_FILLED pct=$Q9_PCT"
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/tailoredai_qwen9b_fields.txt"

echo "=== reset, extract with Qwen3.5-27B ==="
Q "UPDATE field_values SET is_current=false WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND is_current;" >/dev/null
Q "UPDATE documents SET status='new', processed_at=NULL WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null
drain "$TUNNEL -e NVIDIA_MODEL=qwen3.5:27b"
Q27_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
Q27_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
echo "QWEN27B filled=$Q27_FILLED pct=$Q27_PCT" >> "$CKPT"
echo "QWEN27B_DONE filled=$Q27_FILLED pct=$Q27_PCT"
Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/tailoredai_qwen27b_fields.txt"

# leave the BEST result as the live/current profile for the dashboard
if [ "$Q27_FILLED" -ge "$Q9_FILLED" ] 2>/dev/null; then
  echo "keeping Qwen3.5-27B result as the live dashboard profile (best score)"
else
  echo "=== re-extracting with 9B to restore it as the live dashboard profile (best score) ==="
  Q "UPDATE field_values SET is_current=false WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND is_current;" >/dev/null
  Q "UPDATE documents SET status='new', processed_at=NULL WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');" >/dev/null
  drain "$TUNNEL -e NVIDIA_MODEL=qwen3.5:latest"
fi

END=$(date +%s)
{
echo "===== TAILORED AI — FULL MODEL COMPARISON ====="
echo "LFM2.5-230M (Liquid AI, extraction-specialized) : 0/103 fields  (0.0%)  — schema-compliance failure, see notes"
echo "Qwen3.5-9B  (local, GPU box)                    : $Q9_FILLED/103 fields  (${Q9_PCT}%)"
echo "Qwen3.5-27B (local, GPU box)                    : $Q27_FILLED/103 fields  (${Q27_PCT}%)"
echo "Total time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_TAILOREDAI_FULL.txt"
echo "TAILOREDAI_FULL_DONE" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
