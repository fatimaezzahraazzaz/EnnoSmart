-- Additive EnnoScholar V4 spending-controller migration (two new tables).
-- No ALTER, DELETE or DROP. Apply explicitly, then set ENNOSCHOLAR_V4_FINANCE_ENABLED=true
-- and a validated ENNOSCHOLAR_V4_BUDGET_POLICY_FILE. Equivalent: agents.EnnoScholarV4.finance.migrate(engine).
BEGIN;

CREATE TABLE IF NOT EXISTS scholar_v4_budget_counters (
	key VARCHAR(240) NOT NULL, 
	level VARCHAR(20) NOT NULL, 
	scope_id VARCHAR(200) NOT NULL, 
	period VARCHAR(20) NOT NULL, 
	spent_nano BIGINT NOT NULL, 
	reserved_nano BIGINT NOT NULL, 
	calls INTEGER NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (key)
);


CREATE TABLE IF NOT EXISTS scholar_v4_spend_ledger (
	call_id VARCHAR(40) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	organization_id VARCHAR(80) NOT NULL, 
	consultant_id INTEGER NOT NULL, 
	dossier VARCHAR(120) NOT NULL, 
	task_key VARCHAR(64) NOT NULL, 
	task_kind VARCHAR(40) NOT NULL, 
	task_label VARCHAR(240) NOT NULL, 
	run_id VARCHAR(40), 
	session_id VARCHAR(40), 
	counter_keys JSON NOT NULL, 
	reserved_nano BIGINT NOT NULL, 
	settled_nano BIGINT, 
	model_requested VARCHAR(80) NOT NULL, 
	model_resolved VARCHAR(120), 
	service_tier VARCHAR(40), 
	price_version VARCHAR(80), 
	price_status VARCHAR(20), 
	response_id VARCHAR(120), 
	request_id VARCHAR(120), 
	usage JSON, 
	detail JSON, 
	PRIMARY KEY (call_id)
);

CREATE INDEX IF NOT EXISTS ix_scholar_v4_spend_ledger_organization_id ON scholar_v4_spend_ledger (organization_id);
CREATE INDEX IF NOT EXISTS ix_scholar_v4_spend_ledger_task_key ON scholar_v4_spend_ledger (task_key);
CREATE INDEX IF NOT EXISTS ix_scholar_v4_spend_ledger_status ON scholar_v4_spend_ledger (status);
CREATE INDEX IF NOT EXISTS ix_scholar_v4_spend_ledger_consultant_id ON scholar_v4_spend_ledger (consultant_id);
CREATE INDEX IF NOT EXISTS ix_scholar_v4_spend_ledger_run_id ON scholar_v4_spend_ledger (run_id);
CREATE INDEX IF NOT EXISTS ix_scholar_v4_spend_ledger_created_at ON scholar_v4_spend_ledger (created_at);
CREATE INDEX IF NOT EXISTS ix_scholar_v4_spend_ledger_dossier ON scholar_v4_spend_ledger (dossier);

COMMIT;
