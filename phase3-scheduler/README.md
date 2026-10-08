# Phase 3 (part 2) — Scheduler: true 24/7 operation

One always-on container (`nemoclaw-scheduler`) that runs the whole pipeline on a
timer — no human commands needed anymore. Every cycle (default: 24h):

1. **Company worker** re-crawls every active company's site (hash check = only
   changed pages are stored)
2. **News worker** sweeps the press for each company (needs TAVILY_API_KEY)
3. **Intake processor** files any transcripts analysts submitted via Telegram
4. **Extractor** runs until the document queue is empty (cross-validation applies)

`restart: unless-stopped` → it starts whenever Docker starts. Reboot-proof.

## Start it

```bash
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db
# secrets come from db/.env (copy .env.example → .env and fill it in — no exports)
docker compose build scheduler
docker compose up -d scheduler
docker logs -f nemoclaw-scheduler     # watch the first cycle live (Ctrl+C to stop watching)
```

A full cycle runs immediately on start, then sleeps INTERVAL_HOURS.

## Tuning
- `INTERVAL_HOURS` in `db/.env` (e.g. `INTERVAL_HOURS=12`). Shorter = fresher data,
  more NVIDIA/Tavily credit burn. 24 is a sane default for 3 companies.
- Add a company to the rotation: run the company worker once for it (manual
  `docker compose run --rm worker python company_worker.py <slug> "<Name>" <url>`)
  — from then on the scheduler owns it. Pause a company:
  `UPDATE companies SET status='watchlist' WHERE slug='...';`

## Reality check
"24/7" is bounded by this laptop: PC off / Docker stopped = platform paused
(the scheduler catches up on the next cycle after boot). True always-on arrives
when this stack moves to a server/cloud VM at handover — same containers, no changes.
