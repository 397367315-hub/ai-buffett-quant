-- 002_impl_additions.sql —— 实现层补充表（契约 DDL 之外、业务需要的持久化）。
-- 这些表不改变任何契约模型的形状；只是把「日历版本、快照血缘、验证停止事件、
-- 任务调度元数据、模型审计、提醒观察会话」落到 SQLite，便于重启后复核。

-- 版本化交易日历（快照与回放引用 calendar_version）
CREATE TABLE calendar_versions (
    calendar_version TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    coverage_start TEXT NOT NULL CHECK (length(coverage_start)=10),
    coverage_end TEXT NOT NULL CHECK (length(coverage_end)=10),
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic IN (0,1)),
    explicit_closed_only INTEGER NOT NULL DEFAULT 0 CHECK (explicit_closed_only IN (0,1)),
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256)=64),
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload))
);
CREATE TRIGGER calendar_no_update BEFORE UPDATE ON calendar_versions
BEGIN SELECT RAISE(ABORT,'CALENDAR_IMMUTABLE'); END;
CREATE TRIGGER calendar_no_delete BEFORE DELETE ON calendar_versions
BEGIN SELECT RAISE(ABORT,'CALENDAR_IMMUTABLE'); END;

-- 快照血缘：同一会话的供应商修订会生成新 snapshot_id，旧快照不变
CREATE TABLE snapshot_lineage (
    snapshot_id TEXT PRIMARY KEY REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    source_identity_sha256 TEXT NOT NULL CHECK (length(source_identity_sha256)=64),
    supersedes_snapshot_id TEXT REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    lineage_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z')
);
CREATE INDEX snapshot_lineage_key_idx ON snapshot_lineage(lineage_key, created_at);
CREATE TRIGGER snapshot_lineage_no_update BEFORE UPDATE ON snapshot_lineage
BEGIN SELECT RAISE(ABORT,'SNAPSHOT_LINEAGE_IMMUTABLE'); END;

-- 验证运行的追加式事件：排队、开始、单项完成、预算/完整性停止、结束
CREATE TABLE validation_events (
    validation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (typeof(sequence)='integer' AND sequence>=1),
    event_type TEXT NOT NULL CHECK (event_type IN ('queued','started','item_completed','item_failed','stopped','finished')),
    reason TEXT,
    recorded_at TEXT NOT NULL CHECK (substr(recorded_at,-1)='Z'),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    PRIMARY KEY (validation_id, sequence)
);
CREATE TRIGGER validation_events_no_update BEFORE UPDATE ON validation_events
BEGIN SELECT RAISE(ABORT,'VALIDATION_EVENT_APPEND_ONLY'); END;
CREATE TRIGGER validation_events_no_delete BEFORE DELETE ON validation_events
BEGIN SELECT RAISE(ABORT,'VALIDATION_EVENT_APPEND_ONLY'); END;

-- 任务调度元数据：延迟重试、父子任务（Case 编排不占工作槽）、超时
ALTER TABLE jobs ADD COLUMN not_before TEXT;
ALTER TABLE jobs ADD COLUMN parent_job_id TEXT;
ALTER TABLE jobs ADD COLUMN timeout_seconds INTEGER;
ALTER TABLE jobs ADD COLUMN worker_id TEXT;
CREATE INDEX jobs_parent_idx ON jobs(parent_job_id);

-- 模型/解释层审计：原始输出放受控审计目录，这里只存指纹与校验结论
CREATE TABLE model_runs (
    model_run_id TEXT PRIMARY KEY,
    case_id TEXT,
    seat_run_id TEXT,
    provider_id TEXT,
    model_id TEXT,
    llm_mode TEXT NOT NULL CHECK (llm_mode IN ('disabled','optional','required')),
    prompt_sha256 TEXT NOT NULL CHECK (length(prompt_sha256)=64),
    output_sha256 TEXT,
    status TEXT NOT NULL CHECK (status IN ('not_called','accepted','invalid_output','failed','degraded')),
    error_codes_json TEXT NOT NULL CHECK (json_valid(error_codes_json)),
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z')
);
CREATE TRIGGER model_runs_no_update BEFORE UPDATE ON model_runs
BEGIN SELECT RAISE(ABORT,'MODEL_RUN_IMMUTABLE'); END;

-- P4 前台观察会话（默认关闭；只有能力闸门通过且用户点击后才会有行）
CREATE TABLE alert_sessions (
    alert_session_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('active','stopped','refused')),
    started_at TEXT NOT NULL CHECK (substr(started_at,-1)='Z'),
    stopped_at TEXT,
    connection_state TEXT NOT NULL CHECK (connection_state IN ('connected','unknown','disconnected','not_connected')),
    capability_json TEXT NOT NULL CHECK (json_valid(capability_json)),
    replay_id TEXT
);

-- 本地版本化配置（看盘窗口模板等）：只追加，不覆盖
CREATE TABLE local_config_versions (
    config_key TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (typeof(version)='integer' AND version>=1),
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    config_sha256 TEXT NOT NULL CHECK (length(config_sha256)=64),
    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
    PRIMARY KEY (config_key, version)
);
CREATE TRIGGER local_config_no_update BEFORE UPDATE ON local_config_versions
BEGIN SELECT RAISE(ABORT,'LOCAL_CONFIG_IMMUTABLE'); END;
