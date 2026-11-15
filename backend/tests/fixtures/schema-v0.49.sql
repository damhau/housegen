-- The database as v0.49.0 left it (before accounts, #104): what dev and prod start from.
CREATE TABLE projects (
	id VARCHAR(16) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at DATETIME NOT NULL, 
	plan_filename VARCHAR(300), 
	plan_pages INTEGER NOT NULL, 
	current_version INTEGER NOT NULL, 
	brief TEXT, 
	intake_json TEXT, 
	settings_json TEXT, 
	share_token VARCHAR(64), 
	share_version INTEGER, 
	report_json TEXT, 
	PRIMARY KEY (id), 
	UNIQUE (share_token)
);
CREATE TABLE photos (
	id VARCHAR(16) NOT NULL, 
	project_id VARCHAR(16) NOT NULL, 
	side VARCHAR(20) NOT NULL, 
	filename VARCHAR(300) NOT NULL, 
	original_name VARCHAR(300) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);
CREATE INDEX ix_photos_project_id ON photos (project_id);
CREATE TABLE plan_documents (
	id VARCHAR(16) NOT NULL, 
	project_id VARCHAR(16) NOT NULL, 
	number INTEGER NOT NULL, 
	label VARCHAR(200) NOT NULL, 
	original_name VARCHAR(300) NOT NULL, 
	pages INTEGER NOT NULL, 
	pages_total INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);
CREATE INDEX ix_plan_documents_project_id ON plan_documents (project_id);
CREATE TABLE scene_versions (
	id VARCHAR(16) NOT NULL, 
	project_id VARCHAR(16) NOT NULL, 
	number INTEGER NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	label VARCHAR(200) NOT NULL, 
	summary TEXT NOT NULL, 
	critic_score INTEGER, 
	critique_json TEXT, 
	suggestions_json TEXT, 
	questions_json TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);
CREATE INDEX ix_scene_versions_project_id ON scene_versions (project_id);
CREATE TABLE jobs (
	id VARCHAR(16) NOT NULL, 
	project_id VARCHAR(16) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	request_text TEXT NOT NULL, 
	attachments_json TEXT, 
	error TEXT, 
	result_version INTEGER, 
	metrics_json TEXT, 
	settings_json TEXT, 
	created_at DATETIME NOT NULL, 
	finished_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);
CREATE INDEX ix_jobs_project_id ON jobs (project_id);
CREATE TABLE chat_messages (
	id VARCHAR(16) NOT NULL, 
	project_id VARCHAR(16) NOT NULL, 
	role VARCHAR(10) NOT NULL, 
	content TEXT NOT NULL, 
	job_id VARCHAR(16), 
	version_number INTEGER, 
	attachments_json TEXT, 
	point_json TEXT, 
	pictures_json TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);
CREATE INDEX ix_chat_messages_project_id ON chat_messages (project_id);
CREATE TABLE job_events (
	id INTEGER NOT NULL, 
	job_id VARCHAR(16) NOT NULL, 
	seq INTEGER NOT NULL, 
	type VARCHAR(40) NOT NULL, 
	payload_json TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);
CREATE INDEX ix_job_events_job_id ON job_events (job_id);
