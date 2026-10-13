-- Additive EnnoScholar V4 migration. Existing users table must already exist.
-- No ALTER, DELETE or DROP of legacy tables.
BEGIN;


CREATE TABLE IF NOT EXISTS scholar_v4_sessions (
	id VARCHAR(40) NOT NULL,
	owner_id INTEGER NOT NULL,
	title VARCHAR(160) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	data JSON NOT NULL,
	active_run_id VARCHAR(40),
	lease_until TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE INDEX IF NOT EXISTS ix_scholar_v4_sessions_owner_id ON scholar_v4_sessions (owner_id);

CREATE INDEX IF NOT EXISTS ix_scholar_v4_sessions_updated_at ON scholar_v4_sessions (updated_at);


CREATE TABLE IF NOT EXISTS scholar_v4_articles (
	id VARCHAR(100) NOT NULL,
	session_id VARCHAR(40) NOT NULL,
	article_id VARCHAR(60) NOT NULL,
	doi VARCHAR(500),
	access_status VARCHAR(40) NOT NULL,
	data JSON NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES scholar_v4_sessions (id)
)

;

CREATE INDEX IF NOT EXISTS ix_scholar_v4_articles_doi ON scholar_v4_articles (doi);

CREATE INDEX IF NOT EXISTS ix_scholar_v4_articles_session_id ON scholar_v4_articles (session_id);


CREATE TABLE IF NOT EXISTS scholar_v4_messages (
	id VARCHAR(40) NOT NULL,
	session_id VARCHAR(40) NOT NULL,
	role VARCHAR(20) NOT NULL,
	data JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES scholar_v4_sessions (id)
)

;

CREATE INDEX IF NOT EXISTS ix_scholar_v4_messages_session_id ON scholar_v4_messages (session_id);


CREATE TABLE IF NOT EXISTS scholar_v4_outlines (
	id VARCHAR(100) NOT NULL,
	session_id VARCHAR(40) NOT NULL,
	version INTEGER NOT NULL,
	data JSON NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES scholar_v4_sessions (id)
)

;

CREATE INDEX IF NOT EXISTS ix_scholar_v4_outlines_session_id ON scholar_v4_outlines (session_id);


CREATE TABLE IF NOT EXISTS scholar_v4_runs (
	id VARCHAR(40) NOT NULL,
	session_id VARCHAR(40) NOT NULL,
	owner_id INTEGER NOT NULL,
	status VARCHAR(30) NOT NULL,
	cancel_requested BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	data JSON NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES scholar_v4_sessions (id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE INDEX IF NOT EXISTS ix_scholar_v4_runs_owner_id ON scholar_v4_runs (owner_id);

CREATE INDEX IF NOT EXISTS ix_scholar_v4_runs_session_id ON scholar_v4_runs (session_id);


CREATE TABLE IF NOT EXISTS scholar_v4_analyses (
	id VARCHAR(100) NOT NULL,
	session_id VARCHAR(40) NOT NULL,
	analysis_basis VARCHAR(30) NOT NULL,
	data JSON NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(id) REFERENCES scholar_v4_articles (id),
	FOREIGN KEY(session_id) REFERENCES scholar_v4_sessions (id)
)

;

CREATE INDEX IF NOT EXISTS ix_scholar_v4_analyses_session_id ON scholar_v4_analyses (session_id);


CREATE TABLE IF NOT EXISTS scholar_v4_session_articles (
	id VARCHAR(100) NOT NULL,
	session_id VARCHAR(40) NOT NULL,
	selected BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(id) REFERENCES scholar_v4_articles (id),
	FOREIGN KEY(session_id) REFERENCES scholar_v4_sessions (id)
)

;

CREATE INDEX IF NOT EXISTS ix_scholar_v4_session_articles_session_id ON scholar_v4_session_articles (session_id);

COMMIT;
