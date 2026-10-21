-- 002_milestone2_enrichment.sql: Schema updates for Milestone 2 (node facts, edge total state, and interaction sequence)

-- Node enrichment (facts about the person)
ALTER TABLE persons ADD COLUMN education TEXT;
ALTER TABLE persons ADD COLUMN interests TEXT;

-- Edge enrichment (facts about the relationship / total state)
ALTER TABLE relationships ADD COLUMN context_labels TEXT;
ALTER TABLE relationships ADD COLUMN shared_activities TEXT;
ALTER TABLE relationships ADD COLUMN last_interaction_date DATE;
ALTER TABLE relationships ADD COLUMN summary TEXT;

-- Interactions sequence (discrete conversation and interaction event log)
CREATE TABLE IF NOT EXISTS interactions (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    relationship_id TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    interaction_date DATE NOT NULL,
    interaction_type TEXT NOT NULL DEFAULT 'note', -- 'meeting', 'call', 'audio_transcript', 'note', 'email'
    title TEXT,
    content TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_interactions_rel_date ON interactions(relationship_id, interaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_interactions_owner ON interactions(owner_id);
