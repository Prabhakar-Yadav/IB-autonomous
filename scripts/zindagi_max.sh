#!/bin/bash
# Apply all pending changes, then push zindagi-tech as high as public data allows.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
SLUG=zindagi-tech
Q(){ docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -tAc "$1" 2>/dev/null; }
PCT(){ Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';" | tr -d ' \r'; }
drain(){ for i in $(seq 1 30); do
  N=$(Q "SELECT count(*) FROM documents WHERE status='new';" | tr -d ' \r')
  [ "$N" = "0" ] && break
  echo "   drain $i: $N queued $(date +%H:%M:%S)"
  Q "UPDATE documents SET status='new', processed_at=NULL WHERE status='processing';" >/dev/null
  docker compose run --rm worker python extractor.py --limit 999
done; }

START=$(date +%s)
echo "===== BUILD + APPLY ALL CHANGES $(date) ====="
docker compose build worker news-worker dashboard analysis
docker compose up -d --force-recreate dashboard          # verified / review pages live
docker compose up -d searxng

echo "===== APPLICABILITY (decisive N/A pass) ====="
Q "UPDATE documents SET status='new', processed_at=NULL WHERE status='processing';" >/dev/null
drain
docker compose run --rm worker python applicability.py $SLUG
echo ">>> after N/A pass: $(PCT)%"

echo "===== TARGETED HUNT ROUNDS ====="
for ROUND in 1 2 3 4; do
  P=$(PCT); P=${P:-0}
  echo ""
  echo ">>> ROUND $ROUND — currently ${P}%  $(date +%H:%M:%S)"
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
  if awk "BEGIN{exit !($NEW >= 80)}"; then echo "TARGET 80% REACHED"; break; fi
  if awk "BEGIN{exit !($NEW <= $P + 0.5)}"; then echo ">>> no gain — sources exhausted"; break; fi
done

echo "===== DISCOVERY AGENT (extra fields beyond the 103) ====="
docker compose run --rm analysis python discovery_agent.py $SLUG

END=$(date +%s)
echo ""
echo "===== FINAL $(date) ====="
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT slug, fields_filled, completeness_pct FROM company_completeness ORDER BY 3 DESC;"
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT status, count(*) FROM field_values fv JOIN companies c ON c.id=fv.company_id
  WHERE c.slug='$SLUG' AND fv.is_current GROUP BY 1;"
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT label, left(rationale,60) AS why FROM discovered_fields d
  JOIN companies c ON c.id=d.company_id WHERE c.slug='$SLUG' LIMIT 15;"
echo "TOTAL TIME: $(( (END-START)/60 )) min"
