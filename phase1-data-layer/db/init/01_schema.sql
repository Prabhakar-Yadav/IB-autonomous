-- Canonical Enterprise Database — Phase 1
-- Principle: the LLM is never the source of truth; this database is.
-- Every fact carries provenance (source, confidence, collector, version).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Companies ────────────────────────────────────────────────────────────────
CREATE TABLE companies (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         text NOT NULL UNIQUE,          -- url-safe short name, e.g. 'acme-robotics'
    display_name text NOT NULL,
    website      text,
    status       text NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'archived', 'watchlist')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ── Schema registry (seeded from schema/company_schema.json) ─────────────────
CREATE TABLE schema_fields (
    id            text PRIMARY KEY,             -- e.g. 'legal_entity_name'
    section_code  text NOT NULL,                -- 'A'..'N'
    section_id    text NOT NULL,                -- e.g. 'company_snapshot'
    section_title text NOT NULL,
    label         text NOT NULL,
    data_type     text NOT NULL
                  CHECK (data_type IN ('text','date','datetime','url','number','percent','money','list','boolean')),
    guidance      text NOT NULL DEFAULT '',
    sources       text[] NOT NULL,              -- allowed source types
    founder_only  boolean NOT NULL DEFAULT false, -- never fill from web research
    sort_order    integer NOT NULL
);

-- ── Raw documents (bodies live in MinIO; this is the catalog) ────────────────
CREATE TABLE documents (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   uuid REFERENCES companies(id) ON DELETE CASCADE,
    collected_by text NOT NULL,                 -- worker/agent name
    doc_type     text NOT NULL
                 CHECK (doc_type IN ('webpage','pdf','presentation','press_release','careers',
                                     'blog','news','filing','transcript','pitch_deck','other')),
    title        text,
    source_url   text,
    object_key   text NOT NULL,                 -- MinIO path: raw-documents/<slug>/<file>
    content_hash text,                          -- sha256 for change detection
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,                   -- null = not yet through extraction
    status       text NOT NULL DEFAULT 'new'
                 CHECK (status IN ('new','processing','extracted','failed','skipped'))
);
CREATE INDEX idx_documents_company  ON documents(company_id);
CREATE INDEX idx_documents_pending  ON documents(status) WHERE status = 'new';
CREATE UNIQUE INDEX idx_documents_dedupe
    ON documents(company_id, source_url, content_hash);

-- ── Field values (append-only; the heart of the platform) ────────────────────
CREATE TABLE field_values (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id     uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    field_id       text NOT NULL REFERENCES schema_fields(id),
    value_text     text NOT NULL,               -- human-readable value
    value_json     jsonb,                       -- structured form when applicable
    unit           text,                        -- 'USD', 'INR', '%', 'people', …
    confidence     numeric(4,3) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    source_type    text NOT NULL
                   CHECK (source_type IN ('web','document','transcript','founder','internal')),
    source_url     text,
    source_locator text,                        -- page number, timestamp, section
    document_id    uuid REFERENCES documents(id),
    collected_by   text NOT NULL,               -- which worker/agent wrote this
    verified_by    text,                        -- cross-validator, if any
    verified_at    timestamptz,
    status         text NOT NULL DEFAULT 'unverified'
                   CHECK (status IN ('unverified','verified','conflicted','rejected')),
    version        integer NOT NULL DEFAULT 1,
    is_current     boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now()
);
-- exactly one current value per company+field
CREATE UNIQUE INDEX idx_field_values_current
    ON field_values(company_id, field_id) WHERE is_current;
CREATE INDEX idx_field_values_history ON field_values(company_id, field_id, version);

-- Writing a new value: UPDATE ... SET is_current = false on the old row,
-- INSERT the new row with version = old.version + 1. Never UPDATE value rows in place.

-- ── Interactions (founder calls / meetings) ──────────────────────────────────
CREATE TABLE interactions (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id            uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    occurred_at           timestamptz,
    mode                  text,                 -- call / video / in-person / email
    duration              text,
    participants          text,
    transcript_object_key text,                 -- MinIO path if transcript stored
    notes                 text,
    created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_interactions_company ON interactions(company_id);

-- ── Workers & runs (audit trail for the collection layer) ────────────────────
CREATE TABLE workers (
    id          text PRIMARY KEY,               -- 'company-website-worker', 'news-worker', …
    description text NOT NULL DEFAULT '',
    enabled     boolean NOT NULL DEFAULT true
);

CREATE TABLE worker_runs (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    worker_id   text NOT NULL REFERENCES workers(id),
    company_id  uuid REFERENCES companies(id) ON DELETE SET NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','succeeded','failed')),
    stats       jsonb NOT NULL DEFAULT '{}',    -- pages fetched, docs new, facts written…
    error       text
);
CREATE INDEX idx_worker_runs_worker ON worker_runs(worker_id, started_at DESC);

-- ── Convenience views ─────────────────────────────────────────────────────────
CREATE VIEW current_field_values AS
SELECT fv.company_id, c.slug, c.display_name,
       fv.field_id, sf.section_code, sf.section_title, sf.label,
       fv.value_text, fv.value_json, fv.unit,
       fv.confidence, fv.source_type, fv.source_url, fv.source_locator,
       fv.collected_by, fv.verified_by, fv.status, fv.version, fv.created_at
FROM field_values fv
JOIN companies     c  ON c.id = fv.company_id
JOIN schema_fields sf ON sf.id = fv.field_id
WHERE fv.is_current;

CREATE VIEW company_completeness AS
SELECT c.id AS company_id, c.slug, c.display_name,
       count(fv.field_id)                                   AS fields_filled,
       (SELECT count(*) FROM schema_fields)                 AS fields_total,
       round(100.0 * count(fv.field_id)
             / (SELECT count(*) FROM schema_fields), 1)     AS completeness_pct
FROM companies c
LEFT JOIN field_values fv ON fv.company_id = c.id AND fv.is_current
GROUP BY c.id, c.slug, c.display_name;
