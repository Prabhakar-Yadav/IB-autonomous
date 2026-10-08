#!/bin/bash
# Tailored AI (tailoredai.co) company test using LFM2.5-230M (Liquid AI's
# dedicated lightweight extraction model), served locally via Ollama on the
# GPU box (new v0.32.13 instance, port 11435, tunneled to
# host.docker.internal:11435). Results write to the normal companies table,
# so they appear on the dashboard as usual.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=tailoredai
NAME="Tailored AI"
URL="https://www.tailoredai.co"
OUT=/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/scripts/model_compare_results
LOG="$OUT/run_tailoredai.log"
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

START=$(date +%s)
{
echo "===== TAILORED AI test — LFM2.5-230M (Liquid AI extraction model) — $(date) ====="
diskguard || { echo "ABORT before start: disk too low"; exit 1; }

echo "=== STAGE 0: collect ==="
docker compose run --rm worker python company_worker.py $SLUG "$NAME" "$URL"
for w in news_worker linkedin_worker registry_worker patents_worker; do
  diskguard || { echo "ABORT mid-collection: disk too low"; break; }
  docker compose run --rm news-worker python $w.py $SLUG
done

DOC_TOTAL=$(Q "SELECT count(*) FROM documents WHERE company_id=(SELECT id FROM companies WHERE slug='$SLUG');")
echo "documents collected: $DOC_TOTAL"
echo "STAGE0_DONE docs=$DOC_TOTAL" > "$CKPT"

echo "=== STAGE 1: extract with LFM2.5-230M (via tunnel to GPU box, port 11435) ==="
drain "-e NVIDIA_BASE_URL=http://host.docker.internal:11435/v1 -e NVIDIA_MODEL=oamazonasgabriel/lfm2.5-230m:bf16-8gbRAM"
P1_PCT=$(Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';")
P1_FILLED=$(Q "SELECT fields_filled FROM company_completeness WHERE slug='$SLUG';")
echo "LFM230M_PASS1 filled=$P1_FILLED pct=$P1_PCT" >> "$CKPT"
echo "STAGE1_DONE filled=$P1_FILLED pct=$P1_PCT"

Q "SELECT sf.section_code||' | '||sf.label||' | '||fv.value_text||' | conf='||fv.confidence
   FROM field_values fv JOIN schema_fields sf ON sf.id=fv.field_id
   WHERE fv.company_id=(SELECT id FROM companies WHERE slug='$SLUG') AND fv.is_current
   ORDER BY sf.sort_order;" > "$OUT/tailoredai_lfm230m_fields.txt"

END=$(date +%s)
{
echo "===== FINAL — Tailored AI (LFM2.5-230M) ====="
echo "Documents collected: $DOC_TOTAL"
echo "Fields filled: $P1_FILLED/103  (${P1_PCT}%)"
echo "Total time: $(( (END-START)/60 )) min"
} | tee "$OUT/COMPARISON_TAILOREDAI_LFM230M.txt"
echo "TAILOREDAI_LFM_DONE" >> "$CKPT"
} 2>&1 | tee -a "$LOG"
