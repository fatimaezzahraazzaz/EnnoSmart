-- EnnoSmart Storage V2 — migration PostgreSQL additive et réversible.
-- Cette migration ne supprime ni file_data, ni file_path, ni storage_mode.

BEGIN;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS organisme_id VARCHAR(255);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS subproject VARCHAR(255);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS year VARCHAR(20);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS original_filename VARCHAR(500);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS mime_type VARCHAR(255);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS size_bytes BIGINT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS sha256 VARCHAR(64);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS storage_provider VARCHAR(30);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS storage_key TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_kind VARCHAR(100);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS ix_documents_organisme_id ON documents (organisme_id);
CREATE INDEX IF NOT EXISTS ix_documents_year ON documents (year);
CREATE INDEX IF NOT EXISTS ix_documents_sha256 ON documents (sha256);
CREATE INDEX IF NOT EXISTS ix_documents_storage_provider ON documents (storage_provider);
CREATE INDEX IF NOT EXISTS ix_documents_source_kind ON documents (source_kind);

CREATE TABLE IF NOT EXISTS storage_migration_events (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    event_type VARCHAR(50) NOT NULL,
    status VARCHAR(30) NOT NULL,
    storage_provider VARCHAR(30),
    storage_key TEXT,
    sha256 VARCHAR(64),
    details_json JSON,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_storage_migration_events_document_id
    ON storage_migration_events (document_id);
CREATE INDEX IF NOT EXISTS ix_storage_migration_events_event_type
    ON storage_migration_events (event_type);
CREATE INDEX IF NOT EXISTS ix_storage_migration_events_status
    ON storage_migration_events (status);
CREATE INDEX IF NOT EXISTS ix_storage_migration_events_sha256
    ON storage_migration_events (sha256);

CREATE TABLE IF NOT EXISTS memory_v2_documents (
    id SERIAL PRIMARY KEY,
    memory_id VARCHAR(700) NOT NULL UNIQUE,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    organisme_id VARCHAR(255) NOT NULL,
    project_name VARCHAR(255) NOT NULL,
    subproject VARCHAR(255),
    year VARCHAR(20),
    filename VARCHAR(500) NOT NULL,
    original_filename VARCHAR(500) NOT NULL,
    mime_type VARCHAR(255),
    size_bytes BIGINT NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    storage_provider VARCHAR(30) NOT NULL,
    storage_key TEXT NOT NULL,
    source_kind VARCHAR(100) NOT NULL DEFAULT 'memory_v2_document',
    legacy_path TEXT,
    migration_status VARCHAR(50) NOT NULL DEFAULT 'verified',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_memory_v2_documents_project_id ON memory_v2_documents (project_id);
CREATE INDEX IF NOT EXISTS ix_memory_v2_documents_organisme_id ON memory_v2_documents (organisme_id);
CREATE INDEX IF NOT EXISTS ix_memory_v2_documents_project_name ON memory_v2_documents (project_name);
CREATE INDEX IF NOT EXISTS ix_memory_v2_documents_year ON memory_v2_documents (year);
CREATE INDEX IF NOT EXISTS ix_memory_v2_documents_sha256 ON memory_v2_documents (sha256);
CREATE INDEX IF NOT EXISTS ix_memory_v2_documents_storage_provider ON memory_v2_documents (storage_provider);

COMMIT;

-- Rollback non destructif recommandé : désactiver ENNOSMART_STORAGE_V2_ENABLED.
-- Les anciennes colonnes et les BYTEA/chemins legacy continuent alors à être lus.
-- La suppression physique de ces colonnes n'est volontairement pas automatisée.
