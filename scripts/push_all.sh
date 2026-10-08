#!/bin/bash
# Push every company toward 70%+ using the fixed pipeline.
# Order: weakest first, so the biggest wins land early.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
Q(){ docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -tAc "$1" 2>/dev/null; }
PCT(){ Q "SELECT completeness_pct FROM company_completeness WHERE slug='$1';" | tr -d ' \r'; }
diskguard(){ FREE=$(df -BG --output=avail /mnt/c 2>/dev/null | tail -1 | tr -dc '0-9')
  [ -n "$FREE" ] && [ "$FREE" -lt 4 ] && { echo "!!! ABORT: ${FREE}GB disk left"; return 1; }; return 0; }
drain(){ for i in $(seq 1 25); do
  diskguard || return 1
  N=$(Q "SELECT count(*) FROM documents WHERE status='new';" | tr -d ' \r')
  [ -z "$N" ] && { echo "!!! DB not responding — stopping"; return 1; }
  [ "$N" = "0" ] && break
  echo "   drain $i: $N queued $(date +%H:%M:%S)"
  Q "UPDATE documents SET status='new', processed_at=NULL WHERE status='processing';" >/dev/null
  docker compose run --rm worker python extractor.py --limit 999
done; }

START=$(date +%s)
echo "===== PUSH ALL COMPANIES $(date) ====="
docker compose build worker news-worker analysis dashboard
docker compose up -d --force-recreate dashboard

# weakest first — most headroom
for SLUG in dhruva-space droom zoho razorpay zindagi-tech; do
  P=$(PCT $SLUG); P=${P:-0}
  echo ""
  echo "########## $SLUG — starting at ${P}%  $(date +%H:%M) ##########"
  if awk "BEGIN{exit !($P >= 85)}"; then echo "already high, skipping"; continue; fi

  for ROUND in 1 2; do
    B=$(PCT $SLUG); B=${B:-0}
    echo ">>> [$SLUG] round $ROUND from ${B}%"
    docker compose run --rm news-worker python gap_filler_worker.py $SLUG --max-fields 50
    if [ "$ROUND" = "2" ]; then
      for w in news_worker registry_worker linkedin_worker; do
        docker compose run --rm news-worker python "$w.py" $SLUG
      done
    fi
    drain || break
    docker compose run --rm worker python applicability.py $SLUG      # the fixed N/A pass
    A=$(PCT $SLUG); A=${A:-0}
    echo ">>> [$SLUG] round $ROUND: ${B}% -> ${A}%"
    awk "BEGIN{exit !($A >= 75)}" && { echo "[$SLUG] target reached"; break; }
    awk "BEGIN{exit !($A <= $B + 0.5)}" && { echo "[$SLUG] no gain, next company"; break; }
  done
  echo "########## $SLUG done at $(PCT $SLUG)% ##########"
done

echo ""
echo "===== DISCOVERY (new columns across all companies) ====="
docker compose run --rm analysis python discovery_agent.py --all
docker compose run --rm analysis python promote_fields.py --min-companies 2 --min-confidence 0.75
drain

END=$(date +%s)
echo ""; echo "===== FINAL $(date) ====="
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT slug, fields_filled, completeness_pct FROM company_completeness ORDER BY 3 DESC;"
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT label, companies, round(avg_confidence,2) AS conf FROM discovered_field_rollup LIMIT 15;"
echo "TOTAL TIME: $(( (END-START)/60 )) min"
