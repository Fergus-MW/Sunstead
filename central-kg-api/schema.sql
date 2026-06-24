CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS sources (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind        TEXT NOT NULL,
    uri         TEXT,
    title       TEXT,
    content     TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sources_kind_idx ON sources(kind);
CREATE INDEX IF NOT EXISTS sources_created_idx ON sources(created_at DESC);

CREATE TABLE IF NOT EXISTS nodes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type        TEXT NOT NULL,
    name        TEXT NOT NULL,
    properties  JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding   vector(1536),
    source_id   UUID REFERENCES sources(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS nodes_type_idx ON nodes(type);
CREATE INDEX IF NOT EXISTS nodes_name_trgm_idx ON nodes USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS nodes_embedding_idx ON nodes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE UNIQUE INDEX IF NOT EXISTS nodes_type_name_uniq ON nodes (type, lower(name));

CREATE TABLE IF NOT EXISTS edges (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_node_id  UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_node_id  UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    type            TEXT NOT NULL,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    weight          REAL NOT NULL DEFAULT 1.0,
    source_id       UUID REFERENCES sources(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS edges_source_idx ON edges(source_node_id);
CREATE INDEX IF NOT EXISTS edges_target_idx ON edges(target_node_id);
CREATE INDEX IF NOT EXISTS edges_type_idx ON edges(type);
CREATE UNIQUE INDEX IF NOT EXISTS edges_uniq ON edges (source_node_id, target_node_id, type);

CREATE TABLE IF NOT EXISTS events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id      UUID REFERENCES nodes(id) ON DELETE SET NULL,
    source_id    UUID REFERENCES sources(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS events_occurred_idx ON events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS events_node_idx ON events(node_id);
CREATE INDEX IF NOT EXISTS events_kind_idx ON events(kind);
