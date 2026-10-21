-- 003_tasks_schema.sql: Action items and follow-up tasks table

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    contact_person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
    relationship_id TEXT REFERENCES relationships(id) ON DELETE SET NULL,
    interaction_id TEXT REFERENCES interactions(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    deadline DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    completed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tasks_owner_deadline ON tasks(owner_id, status, deadline ASC);
CREATE INDEX IF NOT EXISTS idx_tasks_contact ON tasks(contact_person_id);
CREATE INDEX IF NOT EXISTS idx_tasks_interaction ON tasks(interaction_id);
