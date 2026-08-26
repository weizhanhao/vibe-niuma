-- 初始 schema。由 core/models.py 在 2026-08-25 生成。
-- 之后所有变更都要新加迁移文件，**不要改这个文件** —— 校验和会拦住。

CREATE TABLE orgs (
	id VARCHAR(32) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE api_tokens (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(120) NOT NULL, 
	display_name VARCHAR(120) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	last_used_at DATETIME(6), 
	PRIMARY KEY (id), 
	UNIQUE (token_hash)
);

CREATE INDEX ix_api_tokens_user_id ON api_tokens (user_id);

CREATE TABLE projects (
	id VARCHAR(32) NOT NULL, 
	org_id VARCHAR(32) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	slug VARCHAR(64) NOT NULL, 
	target_branch VARCHAR(120) NOT NULL, 
	dev_runner VARCHAR(32) NOT NULL, 
	dev_model VARCHAR(128) NOT NULL, 
	review_model VARCHAR(128) NOT NULL, 
	quota_parallel_runs INTEGER NOT NULL, 
	port_min INTEGER NOT NULL, 
	port_max INTEGER NOT NULL, 
	token_budget_per_run INTEGER NOT NULL, 
	workspaces_root VARCHAR(512) NOT NULL, 
	secret_refs JSON NOT NULL, 
	config JSON NOT NULL, 
	req_seq INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id), 
	UNIQUE (slug)
);

CREATE INDEX ix_projects_org_id ON projects (org_id);

CREATE TABLE project_repos (
	id VARCHAR(32) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	url VARCHAR(512) NOT NULL, 
	host_kind VARCHAR(24) NOT NULL, 
	default_branch VARCHAR(120) NOT NULL, 
	pat_ref VARCHAR(120), 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_repo_per_project UNIQUE (project_id, name), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_project_repos_project_id ON project_repos (project_id);

CREATE TABLE members (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(120) NOT NULL, 
	display_name VARCHAR(120) NOT NULL, 
	`role` VARCHAR(24) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_member UNIQUE (project_id, user_id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_members_project_id ON members (project_id);

CREATE TABLE requirements (
	id VARCHAR(32) NOT NULL, 
	seq INTEGER NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	body LONGTEXT NOT NULL, 
	requested_by VARCHAR(120) NOT NULL, 
	stage VARCHAR(40) NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	contracts JSON NOT NULL, 
	sequence_kind VARCHAR(16), 
	attachments JSON NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_req_seq_per_project UNIQUE (project_id, seq), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_requirements_state ON requirements (state);

CREATE INDEX ix_req_project_stage ON requirements (project_id, stage);

CREATE INDEX ix_requirements_project_id ON requirements (project_id);

CREATE INDEX ix_requirements_stage ON requirements (stage);

CREATE TABLE port_leases (
	id VARCHAR(32) NOT NULL, 
	port INTEGER NOT NULL, 
	workspace_id VARCHAR(32), 
	leased_at DATETIME(6) NOT NULL, 
	expires_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_port_per_project UNIQUE (project_id, port), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_port_leases_project_id ON port_leases (project_id);

CREATE TABLE agent_sessions (
	id VARCHAR(32) NOT NULL, 
	requirement_id VARCHAR(32), 
	task_id VARCHAR(32), 
	provider VARCHAR(32) NOT NULL, 
	session_id VARCHAR(128) NOT NULL, 
	parent_session_id VARCHAR(128), 
	purpose VARCHAR(32) NOT NULL, 
	cwd VARCHAR(512) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_agent_sessions_task_id ON agent_sessions (task_id);

CREATE INDEX ix_agent_sessions_requirement_id ON agent_sessions (requirement_id);

CREATE INDEX ix_agent_sessions_project_id ON agent_sessions (project_id);

CREATE TABLE deploy_runs (
	id VARCHAR(32) NOT NULL, 
	env VARCHAR(24) NOT NULL, 
	ref VARCHAR(255) NOT NULL, 
	adapter VARCHAR(32) NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	external_id VARCHAR(255), 
	external_url VARCHAR(512), 
	meta JSON NOT NULL, 
	started_at DATETIME(6), 
	finished_at DATETIME(6), 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_deploy_runs_env ON deploy_runs (env);

CREATE INDEX ix_deploy_runs_project_id ON deploy_runs (project_id);

CREATE INDEX ix_deploy_runs_state ON deploy_runs (state);

CREATE TABLE jobs (
	id VARCHAR(32) NOT NULL, 
	requirement_id VARCHAR(32), 
	run_id VARCHAR(32), 
	kind VARCHAR(48) NOT NULL, 
	lane VARCHAR(16) NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	payload JSON NOT NULL, 
	idempotency_key VARCHAR(160) NOT NULL, 
	attempts INTEGER NOT NULL, 
	max_attempts INTEGER NOT NULL, 
	next_run_at DATETIME(6) NOT NULL, 
	locked_by VARCHAR(64), 
	locked_at DATETIME(6), 
	last_error TEXT, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (idempotency_key), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_jobs_project_id ON jobs (project_id);

CREATE INDEX ix_jobs_lane ON jobs (lane);

CREATE INDEX ix_job_claim ON jobs (state, next_run_at);

CREATE INDEX ix_jobs_requirement_id ON jobs (requirement_id);

CREATE INDEX ix_jobs_state ON jobs (state);

CREATE INDEX ix_jobs_run_id ON jobs (run_id);

CREATE TABLE jobs_archive (
	id VARCHAR(32) NOT NULL, 
	requirement_id VARCHAR(32), 
	run_id VARCHAR(32), 
	kind VARCHAR(48) NOT NULL, 
	lane VARCHAR(16) NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	payload JSON NOT NULL, 
	idempotency_key VARCHAR(160) NOT NULL, 
	attempts INTEGER NOT NULL, 
	last_error TEXT, 
	created_at DATETIME(6) NOT NULL, 
	archived_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_jobs_archive_requirement_id ON jobs_archive (requirement_id);

CREATE INDEX ix_jobs_archive_project_id ON jobs_archive (project_id);

CREATE TABLE events (
	id BIGINT NOT NULL AUTO_INCREMENT, 
	stream VARCHAR(80) NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	payload JSON NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_event_replay ON events (stream, id);

CREATE INDEX ix_events_stream ON events (stream);

CREATE INDEX ix_events_project_id ON events (project_id);

CREATE TABLE messages (
	id VARCHAR(32) NOT NULL, 
	requirement_id VARCHAR(32) NOT NULL, 
	`role` VARCHAR(16) NOT NULL, 
	author VARCHAR(120) NOT NULL, 
	body LONGTEXT NOT NULL, 
	stage VARCHAR(40) NOT NULL, 
	awaiting_answer BOOL NOT NULL, 
	trace JSON NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(requirement_id) REFERENCES requirements (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_msg_req ON messages (requirement_id, created_at);

CREATE INDEX ix_messages_requirement_id ON messages (requirement_id);

CREATE INDEX ix_messages_project_id ON messages (project_id);

CREATE TABLE tasks (
	id VARCHAR(32) NOT NULL, 
	requirement_id VARCHAR(32) NOT NULL, 
	`key` VARCHAR(16) NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	delivers TEXT NOT NULL, 
	repo_names JSON NOT NULL, 
	depends_on JSON NOT NULL, 
	sequence VARCHAR(16), 
	state VARCHAR(24) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(requirement_id) REFERENCES requirements (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_tasks_requirement_id ON tasks (requirement_id);

CREATE INDEX ix_tasks_state ON tasks (state);

CREATE INDEX ix_tasks_project_id ON tasks (project_id);

CREATE TABLE reviews (
	id VARCHAR(32) NOT NULL, 
	requirement_id VARCHAR(32) NOT NULL, 
	reviewer VARCHAR(120) NOT NULL, 
	decision VARCHAR(24) NOT NULL, 
	comment TEXT NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(requirement_id) REFERENCES requirements (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_reviews_requirement_id ON reviews (requirement_id);

CREATE INDEX ix_reviews_project_id ON reviews (project_id);

CREATE TABLE merge_jobs (
	id VARCHAR(32) NOT NULL, 
	requirement_id VARCHAR(32) NOT NULL, 
	repo_name VARCHAR(120) NOT NULL, 
	position INTEGER NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	conflict_ladder JSON NOT NULL, 
	merged_sha VARCHAR(64), 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(requirement_id) REFERENCES requirements (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_merge_jobs_repo_name ON merge_jobs (repo_name);

CREATE INDEX ix_merge_jobs_project_id ON merge_jobs (project_id);

CREATE INDEX ix_merge_jobs_state ON merge_jobs (state);

CREATE INDEX ix_merge_jobs_requirement_id ON merge_jobs (requirement_id);

CREATE TABLE steps (
	id VARCHAR(32) NOT NULL, 
	job_id VARCHAR(32) NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	seq INTEGER NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	input JSON NOT NULL, 
	output JSON, 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_step_per_job UNIQUE (job_id, name), 
	FOREIGN KEY(job_id) REFERENCES jobs (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_steps_project_id ON steps (project_id);

CREATE INDEX ix_steps_job_id ON steps (job_id);

CREATE TABLE signals (
	id VARCHAR(32) NOT NULL, 
	job_id VARCHAR(32) NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	payload JSON NOT NULL, 
	consumed BOOL NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_signals_project_id ON signals (project_id);

CREATE INDEX ix_signals_job_id ON signals (job_id);

CREATE TABLE task_touches (
	task_id VARCHAR(32) NOT NULL, 
	path VARCHAR(400) NOT NULL, 
	repo_name VARCHAR(120) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (task_id, path), 
	FOREIGN KEY(task_id) REFERENCES tasks (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
)ROW_FORMAT=DYNAMIC;

CREATE INDEX ix_task_touches_project_id ON task_touches (project_id);

CREATE INDEX ix_touch_path ON task_touches (project_id, repo_name, path);

CREATE TABLE runs (
	id VARCHAR(32) NOT NULL, 
	task_id VARCHAR(32) NOT NULL, 
	attempt INTEGER NOT NULL, 
	branch VARCHAR(255) NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	commit_shas JSON NOT NULL, 
	fail_reason VARCHAR(500), 
	fail_log LONGTEXT, 
	started_at DATETIME(6), 
	finished_at DATETIME(6), 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES tasks (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_runs_project_id ON runs (project_id);

CREATE INDEX ix_runs_state ON runs (state);

CREATE INDEX ix_runs_task_id ON runs (task_id);

CREATE TABLE workspaces (
	id VARCHAR(32) NOT NULL, 
	run_id VARCHAR(32) NOT NULL, 
	path VARCHAR(512) NOT NULL, 
	container_id VARCHAR(128), 
	image VARCHAR(256), 
	state VARCHAR(24) NOT NULL, 
	repos JSON NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	released_at DATETIME(6), 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (run_id), 
	FOREIGN KEY(run_id) REFERENCES runs (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_workspaces_project_id ON workspaces (project_id);

CREATE INDEX ix_workspaces_state ON workspaces (state);

CREATE TABLE findings (
	id VARCHAR(32) NOT NULL, 
	run_id VARCHAR(32) NOT NULL, 
	axis VARCHAR(16) NOT NULL, 
	severity VARCHAR(16) NOT NULL, 
	category VARCHAR(48) NOT NULL, 
	path VARCHAR(512) NOT NULL, 
	start_line INTEGER NOT NULL, 
	end_line INTEGER NOT NULL, 
	claim TEXT NOT NULL, 
	failure_scenario TEXT NOT NULL, 
	existing_code TEXT NOT NULL, 
	suggestion_code TEXT NOT NULL, 
	kept BOOL NOT NULL, 
	verdict_reason TEXT NOT NULL, 
	confidence VARCHAR(16) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES runs (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_findings_project_id ON findings (project_id);

CREATE INDEX ix_findings_run_id ON findings (run_id);
