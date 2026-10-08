#!/usr/bin/env python3
"""Transcript intake processor.

Moves analyst-submitted transcripts from transcript_inbox into the normal
document pipeline: raw text -> MinIO + documents catalog (doc_type 'transcript')
+ an interactions row. The extraction engine then processes them like any
other document — but with the full field catalog (founder-only fields allowed,
because transcripts are trusted internal sources).

Usage:
    python transcript_processor.py
"""
import hashlib
from io import BytesIO

from common import BUCKET, minio_client, pg_connect

PROCESSOR_ID = "telegram-intake"


def main() -> None:
    conn = pg_connect()
    conn.autocommit = True
    minio = minio_client()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, company_slug, submitted_by, content FROM transcript_inbox WHERE status = 'new' ORDER BY id"
        )
        items = cur.fetchall()
    print(f"inbox: {len(items)} new transcript(s)")

    for item_id, slug_raw, submitted_by, content in items:
        slug = slug_raw.strip().lower()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM companies WHERE slug = %s", (slug,))
                row = cur.fetchone()
                if row:
                    company_id = row[0]
                else:
                    # analyst may intake a company before any crawl exists
                    cur.execute(
                        """INSERT INTO companies (slug, display_name)
                           VALUES (%s, %s) RETURNING id""",
                        (slug, slug.replace("-", " ").title()),
                    )
                    company_id = cur.fetchone()[0]

            body = content.encode("utf-8")
            digest = hashlib.sha256(body).hexdigest()

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM documents WHERE company_id = %s AND content_hash = %s",
                    (company_id, digest),
                )
                if cur.fetchone():
                    cur.execute(
                        "UPDATE transcript_inbox SET status = 'processed', note = 'duplicate' WHERE id = %s",
                        (item_id,),
                    )
                    print(f"  = inbox #{item_id} ({slug}): duplicate, skipped")
                    continue

            key = f"{slug}/transcripts/{digest[:12]}_inbox{item_id}.txt"
            minio.put_object(BUCKET, key, BytesIO(body), len(body))
            pseudo_url = f"transcript://{slug}/inbox-{item_id}"

            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO documents
                         (company_id, collected_by, doc_type, title, source_url, object_key, content_hash)
                       VALUES (%s, %s, 'transcript', %s, %s, %s, %s)""",
                    (company_id, PROCESSOR_ID, f"Analyst transcript (inbox #{item_id})",
                     pseudo_url, key, digest),
                )
                cur.execute(
                    """INSERT INTO interactions (company_id, notes, transcript_object_key)
                       VALUES (%s, %s, %s)""",
                    (company_id, f"Transcript submitted via Telegram by {submitted_by}", key),
                )
                cur.execute(
                    "UPDATE transcript_inbox SET status = 'processed' WHERE id = %s", (item_id,)
                )
            print(f"  + inbox #{item_id} ({slug}): queued for extraction")
        except Exception as exc:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE transcript_inbox SET status = 'failed', note = %s WHERE id = %s",
                    (str(exc)[:500], item_id),
                )
            print(f"  ! inbox #{item_id} ({slug}): {exc}")

    print("intake pass complete")


if __name__ == "__main__":
    main()
