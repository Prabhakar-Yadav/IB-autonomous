-- Phase 6: schema discovery — fields the DATA suggests we should be tracking,
-- beyond the 103 in the original intake form.

CREATE TABLE IF NOT EXISTS discovered_fields (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id   uuid REFERENCES companies(id) ON DELETE CASCADE,
    field_key    text NOT NULL,            -- proposed snake_case id
    label        text NOT NULL,            -- human label
    section_hint text,                     -- where it would belong (A..N or 'NEW')
    rationale    text NOT NULL,            -- why it matters for an IB/PE decision
    example_value text,                    -- value observed for this company
    source_url   text,
    confidence   numeric(4,3) NOT NULL DEFAULT 0.5,
    occurrences  integer NOT NULL DEFAULT 1,   -- how many companies suggested it
    status       text NOT NULL DEFAULT 'proposed'
                 CHECK (status IN ('proposed','accepted','rejected')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, field_key)
);

CREATE INDEX IF NOT EXISTS idx_discovered_status ON discovered_fields(status);

-- roll-up: which proposals appear across multiple companies (strongest candidates)
CREATE OR REPLACE VIEW discovered_field_rollup AS
SELECT field_key,
       min(label)                       AS label,
       min(section_hint)                AS section_hint,
       count(DISTINCT company_id)       AS companies,
       round(avg(confidence), 3)        AS avg_confidence,
       (array_agg(rationale ORDER BY confidence DESC))[1]     AS top_rationale,
       (array_agg(example_value ORDER BY confidence DESC))[1] AS example_value,
       bool_or(status = 'accepted')     AS accepted
FROM discovered_fields
GROUP BY field_key
ORDER BY companies DESC, avg_confidence DESC;
