#!/bin/bash
# Push one company toward a completeness target: gap-fill -> extract -> re-check,
# escalating each round, stopping at target or when a round gains nothing.
# Usage: push_target.sh <slug> [target_pct]
set +e
SLUG="${1:-zindagi-tech}"
TARGET="${2:-70}"
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1
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
echo "===== PUSH $SLUG TO ${TARGET}% — $(date) ====="

# clear anything left mid-flight, then process whatever is already collected
Q "UPDATE documents SET status='new', processed_at=NULL WHERE status='processing';" >/dev/null
drain

for ROUND in $(seq 1 10); do
  P=$(PCT); P=${P:-0}
  echo ""
  echo ">>> ROUND $ROUND — currently ${P}% (target ${TARGET}%)  $(date +%H:%M:%S)"
  if awk "BEGIN{exit !($P >= $TARGET)}"; then echo "TARGET REACHED"; break; fi

  docker compose run --rm news-worker python gap_filler_worker.py "$SLUG" --max-fields 60
  drain

  if [ "$ROUND" = "2" ]; then
    docker compose run --rm worker python applicability.py "$SLUG"
  fi

  if [ "$ROUND" -ge 3 ]; then
    for w in news_worker registry_worker linkedin_worker; do
      docker compose run --rm news-worker python "$w.py" "$SLUG"
    done
    drain
  fi

  NEW=$(PCT); NEW=${NEW:-0}
  echo ">>> round $ROUND: ${P}% -> ${NEW}%"
  if awk "BEGIN{exit !($NEW <= $P + 0.5)}"; then
    echo ">>> no further gain — public sources exhausted for this company, stopping"
    break
  fi
done

END=$(date +%s)
echo ""
echo "===== FINAL $(date) ====="
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT slug, fields_filled, completeness_pct FROM company_completeness ORDER BY 3 DESC;"
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT status, count(*) FROM field_values fv JOIN companies c ON c.id=fv.company_id
  WHERE c.slug='$SLUG' AND fv.is_current GROUP BY 1;"
echo "TOTAL TIME: $(( (END-START)/60 )) min"
