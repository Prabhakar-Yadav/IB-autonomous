#!/bin/bash
# Full run for zindagi-tech with key rotation: N/A pass, gap hunt, discovery, promote, fill.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=zindagi-tech
Q(){ docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -tAc "$1" 2>/dev/null; }
PCT(){ Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';" | tr -d ' \r'; }
diskguard(){   # abort before filling the disk (this has killed 3 runs)
  FREE=$(df -BG --output=avail /mnt/c 2>/dev/null | tail -1 | tr -dc '0-9')
  if [ -n "$FREE" ] && [ "$FREE" -lt 3 ]; then
    echo "!!! ABORT: only ${FREE}GB disk free — stopping before Docker crashes"; return 1
  fi; return 0; }

drain(){ for i in $(seq 1 30); do
  diskguard || return 1
  N=$(Q "SELECT count(*) FROM documents WHERE status='new';" | tr -d ' \r')
  if [ -z "$N" ]; then
    echo "!!! database not responding (blank count) — stopping instead of spinning"; return 1
  fi
  [ "$N" = "0" ] && break
  echo "   drain $i: $N queued $(date +%H:%M:%S)"
  Q "UPDATE documents SET status='new', processed_at=NULL WHERE status='processing';" >/dev/null
  docker compose run --rm worker python extractor.py --limit 999
done; }

START=$(date +%s)
echo "===== BUILD $(date) ====="
docker compose build worker news-worker dashboard analysis
docker compose up -d --force-recreate dashboard
docker compose up -d searxng

echo "===== KEY CHECK ====="
docker compose run --rm worker python -c "from nvidia_client import get_client; print('NVIDIA keys active:', get_client().n_keys)"

echo "===== 1. N/A PASS (decisive, with key rotation) ====="
Q "UPDATE documents SET status='new', processed_at=NULL WHERE status='processing';" >/dev/null
drain
docker compose run --rm worker python applicability.py $SLUG
echo ">>> after N/A: $(PCT)%"

echo "===== 2. TARGETED HUNT ====="
for ROUND in 1 2 3; do
  P=$(PCT); P=${P:-0}
  echo ""; echo ">>> ROUND $ROUND — at ${P}%  $(date +%H:%M:%S)"
  docker compose run --rm news-worker python gap_filler_worker.py $SLUG --max-fields 60
  if [ "$ROUND" -ge 2 ]; then
    for w in news_worker registry_worker linkedin_worker patents_worker; do
      docker compose run --rm news-worker python "$w.py" $SLUG
    done
  fi
  drain
  docker compose run --rm worker python applicability.py $SLUG
  NEW=$(PCT); NEW=${NEW:-0}
  echo ">>> round $ROUND: ${P}% -> ${NEW}%"
  awk "BEGIN{exit !($NEW >= 80)}" && { echo "80% REACHED"; break; }
  awk "BEGIN{exit !($NEW <= $P + 0.5)}" && { echo ">>> no gain, moving on"; break; }
done

echo "===== 3. DISCOVERY AGENT (new columns beyond the 103) ====="
docker compose run --rm analysis python discovery_agent.py $SLUG

echo "===== 4. PROMOTE discovered columns into the schema ====="
docker compose run --rm analysis python promote_fields.py --min-companies 1 --min-confidence 0.6

echo "===== 5. FILL the new columns ====="
drain
docker compose run --rm worker python applicability.py $SLUG

END=$(date +%s)
echo ""; echo "===== FINAL $(date) ====="
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT slug, fields_filled, completeness_pct FROM company_completeness ORDER BY 3 DESC;"
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT status, count(*) FROM field_values fv JOIN companies c ON c.id=fv.company_id
  WHERE c.slug='$SLUG' AND fv.is_current GROUP BY 1;"
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT label, left(rationale,55) AS why, status FROM discovered_fields d
  JOIN companies c ON c.id=d.company_id WHERE c.slug='$SLUG' ORDER BY confidence DESC LIMIT 20;"
echo "TOTAL TIME: $(( (END-START)/60 )) min"
