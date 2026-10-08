-- On-prem RAG schema. All confidential-document data lives here, on the box only.
CREATE EXTENSION IF NOT EXISTS vector;

-- one row per source PDF
CREATE TABLE IF NOT EXISTS zt_documents (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company     text NOT NULL,
    filename    text NOT NULL,
    pages       integer,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company, filename)
);

-- chunks with embeddings (bge-m3 = 1024 dims)
CREATE TABLE IF NOT EXISTS zt_chunks (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL REFERENCES zt_documents(id) ON DELETE CASCADE,
    company     text NOT NULL,
    chunk_index integer NOT NULL,
    page_from   integer,
    section     text,
    content     text NOT NULL,
    embedding   vector(1024),
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_zt_chunks_vec
    ON zt_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_zt_chunks_company ON zt_chunks(company);

-- extracted field values (the 103-field schema, filled locally from the PDFs)
CREATE TABLE IF NOT EXISTS zt_field_values (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company      text NOT NULL,
    field_id     text NOT NULL,
    label        text NOT NULL,
    section_code text,
    value_text   text NOT NULL,
    confidence   numeric(4,3) NOT NULL DEFAULT 0.5,
    source_file  text,
    source_page  integer,
    quote        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company, field_id)
);
