#!/usr/bin/env python3
"""Platform doctor — run this FIRST whenever anything misbehaves.

Checks every dependency the pipeline needs and prints a plain-English verdict
with the exact fix. Costs zero API credits (reachability only).

Usage:
    docker compose run --rm worker python doctor.py
"""
import os
import socket
import sys

RESULTS = []


def check(name, fn, fix):
    try:
        detail = fn() or "ok"
        RESULTS.append((True, name, detail, ""))
        print(f"  ✅ {name}: {detail}")
    except Exception as exc:
        RESULTS.append((False, name, str(exc), fix))
        print(f"  ❌ {name}: {type(exc).__name__}: {str(exc)[:120]}")
        print(f"     FIX → {fix}")


def dns(host):
    def _f():
        socket.getaddrinfo(host, 443)
        return "name resolves"
    return _f


def reach(url):
    def _f():
        import requests
        r = requests.head(url, timeout=10, allow_redirects=True)
        return f"reachable (HTTP {r.status_code})"
    return _f


def main():
    print("── NETWORK ─────────────────────────────────────────")
    check("DNS (internet name lookup)", dns("one.one.one.one"),
          "No DNS at all → Wi-Fi/ISP problem or WSL network wedge. "
          "Check a site in your Windows browser; if browser works, reboot the PC.")
    check("General internet (cloudflare)", reach("https://one.one.one.one"),
          "DNS works but connections fail → firewall/VPN/antivirus or unstable Wi-Fi.")
    check("Tavily search API endpoint", reach("https://api.tavily.com"),
          "Search/gap-filler/news workers will fail. Usually same cause as general internet.")
    check("NVIDIA inference endpoint", reach("https://integrate.api.nvidia.com"),
          "Extractor will fail/hang. Usually same cause as general internet.")

    print("── CREDENTIALS (present, not validated) ────────────")
    for var in ("NVIDIA_API_KEY", "TAVILY_API_KEY", "PGPASSWORD", "MINIO_SECRET_KEY"):
        val = os.environ.get(var, "")
        if val and "change-me" not in val:
            print(f"  ✅ {var}: set ({len(val)} chars)")
        else:
            RESULTS.append((False, var, "missing", ""))
            print(f"  ❌ {var}: MISSING or default → check phase1-data-layer/db/.env")

    print("── PLATFORM SERVICES ───────────────────────────────")
    def _pg():
        from common import pg_connect
        with pg_connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM documents")
            n = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM documents WHERE status='new'")
            q = cur.fetchone()[0]
        return f"connected — {n} documents total, {q} queued for extraction"
    check("Postgres (facts database)", _pg,
          "Is the postgres container healthy? → docker compose up -d postgres; check password in .env")

    def _minio():
        from common import minio_client, BUCKET
        if not minio_client().bucket_exists(BUCKET):
            raise RuntimeError(f"bucket '{BUCKET}' missing")
        return "connected — bucket present"
    check("MinIO (document store)", _minio,
          "→ docker compose up -d minio; check MINIO_PASSWORD in .env")

    print("── VERDICT ─────────────────────────────────────────")
    fails = [r for r in RESULTS if not r[0]]
    if not fails:
        print("  ALL SYSTEMS GO — safe to run workers and the extractor.")
        sys.exit(0)
    print(f"  {len(fails)} problem(s) found — fix the ❌ items above, then re-run doctor.")
    print("  Rule of thumb: many network ❌ at once = reboot the PC; a single site failing = that URL is wrong.")
    sys.exit(1)


if __name__ == "__main__":
    main()
