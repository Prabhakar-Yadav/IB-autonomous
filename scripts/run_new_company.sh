#!/bin/bash
# ONE-COMMAND full profile of a brand-new company, timed end to end.
# Usage: run_new_company.sh <slug> "<Display Name>" <website-url>
# Every stall/error guard from the pipeline is active; it self-terminates cleanly.
set +e
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db || exit 1

SLUG="$1"; NAME="$2"; URL="$3"
[ -z "$SLUG" ] || [ -z "$NAME" ] || [ -z "$URL" ] && { echo "usage: run_new_company.sh <slug> \"<Name>\" <url>"; exit 1; }

Q(){ docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -tAc "$1" 2>/dev/null; }
PCT(){ Q "SELECT completeness_pct FROM company_completeness WHERE slug='$SLUG';" | tr -d ' \r'; }
diskguard(){ FREE=$(df -BG --output=avail /mnt/c 2>/dev/null | tail -1 | tr -dc '0-9')
  [ -n "$FREE" ] && [ "$FREE" -lt 4 ] && { echo "!!! ABORT: only ${FREE}GB disk free"; return 1; }; return 0; }
drain(){ for i in $(seq 1 25); do
  diskguard || return 1
  N=$(Q "SELECT count(*) FROM documents WHERE status='new';" | tr -d ' \r')
  [ -z "$N" ] && { echo "!!! DB not responding — stopping"; return 1; }
  [ "$N" = "0" ] && break
  echo "   drain $i: $N queued  $(date +%H:%M:%S)"
  Q "UPDATE documents SET status='new', processed_at=NULL WHERE status='processing';" >/dev/null
  docker compose run --rm worker python extractor.py --limit 999
done; }

START=$(date +%s)
echo "=================================================="
echo " NEW COMPANY RUN: $NAME ($SLUG)"
echo " URL: $URL"
echo " Started: $(date)"
echo "=================================================="

echo ">>> [1/7] build images"
docker compose build worker news-worker analysis >/dev/null 2>&1
docker compose up -d searxng >/dev/null 2>&1

echo ">>> [2/7] collect: website"
docker compose run --rm worker python company_worker.py "$SLUG" "$NAME" "$URL"

echo ">>> [3/7] collect: news / linkedin / registry / patents"
for w in news_worker linkedin_worker registry_worker patents_worker; do
  docker compose run --rm news-worker python "$w.py" "$SLUG"
done

echo ">>> [4/7] extract everything collected"
drain

echo ">>> [5/7] gap-fill empty fields, then extract; applicability N/A pass"
for ROUND in 1 2; do
  B=$(PCT); B=${B:-0}
  echo "   -- round $ROUND from ${B}% --"
  docker compose run --rm news-worker python gap_filler_worker.py "$SLUG" --max-fields 55
  drain || break
  docker compose run --rm worker python applicability.py "$SLUG"
  A=$(PCT); A=${A:-0}
  echo "   -- round $ROUND: ${B}% -> ${A}% --"
  awk "BEGIN{exit !($A <= $B + 0.5)}" && { echo "   (no further gain)"; break; }
done

echo ">>> [6/7] discovery: propose new columns from this company's evidence"
docker compose run --rm analysis python discovery_agent.py "$SLUG"

echo ">>> [7/7] done"
END=$(date +%s); MINS=$(( (END-START)/60 ))

echo ""
echo "=================================================="
echo " RESULT — $NAME"
echo "=================================================="
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT completeness_pct AS pct, fields_filled AS filled FROM company_completeness WHERE slug='$SLUG';"
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT status, count(*) FROM field_values fv JOIN companies c ON c.id=fv.company_id
  WHERE c.slug='$SLUG' AND fv.is_current GROUP BY 1 ORDER BY 2 DESC;"
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT count(*) AS documents_collected FROM documents d JOIN companies c ON c.id=d.company_id
  WHERE c.slug='$SLUG';"
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
 "SELECT label, left(rationale,55) AS proposed_new_field FROM discovered_fields d
  JOIN companies c ON c.id=d.company_id WHERE c.slug='$SLUG' ORDER BY confidence DESC LIMIT 10;"
echo ""
echo " TOTAL TIME: ${MINS} min   (view: http://localhost:8090/company/$SLUG)"
echo "=================================================="
