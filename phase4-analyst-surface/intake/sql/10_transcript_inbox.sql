-- Transcript intake inbox: the ONLY thing the sandboxed agent may write.
-- Raw transcripts land here; the trusted pipeline turns them into facts.

CREATE TABLE IF NOT EXISTS transcript_inbox (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_slug text NOT NULL,
    submitted_by text NOT NULL DEFAULT 'telegram-analyst',
    content      text NOT NULL,
    status       text NOT NULL DEFAULT 'new'
                 CHECK (status IN ('new', 'processed', 'failed')),
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

GRANT INSERT, SELECT ON transcript_inbox TO agent_ro;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO agent_ro;

-- tell PostgREST to pick up the new table without a restart
NOTIFY pgrst, 'reload schema';
