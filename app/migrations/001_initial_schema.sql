-- 001_initial_schema.sql: Initial schema for Inner Circle

CREATE TABLE IF NOT EXISTS owners (
    id TEXT PRIMARY KEY,
    email TEXT,
    focal_person_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    email TEXT,
    profile_url TEXT,
    company TEXT,
    job_title TEXT,
    location TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(trim(first_name)) > 0 OR length(trim(last_name)) > 0)
);

CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    person_a_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    person_b_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    connected_on DATE,
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (person_a_id < person_b_id),
    UNIQUE (owner_id, person_a_id, person_b_id)
);

CREATE TABLE IF NOT EXISTS import_fingerprints (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    fingerprint_hash TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_id, fingerprint_hash)
);

CREATE INDEX IF NOT EXISTS idx_persons_owner ON persons(owner_id);
CREATE INDEX IF NOT EXISTS idx_persons_email ON persons(owner_id, email);
CREATE INDEX IF NOT EXISTS idx_persons_profile_url ON persons(owner_id, profile_url);
CREATE INDEX IF NOT EXISTS idx_relationships_owner ON relationships(owner_id);
CREATE INDEX IF NOT EXISTS idx_relationships_a ON relationships(person_a_id);
CREATE INDEX IF NOT EXISTS idx_relationships_b ON relationships(person_b_id);
CREATE INDEX IF NOT EXISTS idx_import_fingerprints_lookup ON import_fingerprints(owner_id, fingerprint_hash);
