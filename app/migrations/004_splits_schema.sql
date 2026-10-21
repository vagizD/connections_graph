-- 004_splits_schema.sql: AI-driven custom network splits & group statistics

CREATE TABLE IF NOT EXISTS network_splits (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    title TEXT NOT NULL,
    split_data TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS active_splits (
    owner_id TEXT PRIMARY KEY REFERENCES owners(id) ON DELETE CASCADE,
    split_id TEXT REFERENCES network_splits(id) ON DELETE SET NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_network_splits_owner ON network_splits(owner_id);
