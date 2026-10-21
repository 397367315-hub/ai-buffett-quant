-- 001_core_v1.sql —— 由 contracts/initial_schema.sql（契约起始 DDL）逐字派生。
-- 只去掉了 PRAGMA 与 BEGIN/COMMIT：连接级 PRAGMA 由 data/db.py 设置，事务由迁移器包裹。
-- 不要在此文件上做语义修改；契约变化另起新迁移文件并更新 checksum。

-- 交易圆桌 v1：待实现的SQLite起始DDL，非系统升级脚本，非业务实现。
-- 规格版本 1.0.0-draft / 2026-10-19；正式契约见同目录models.schema.json和openapi.yaml。
-- 新运行数据库建议 runtime/roundtable.sqlite3；禁止在源码备份data目录原地建库。
-- 应用前由迁移器备份原字节、验证schema_migrations.checksum，再在新运行目录执行。
-- 每个连接都必须显式启用foreign_keys和busy_timeout；WAL仅对文件数据库生效。
-- 所有金额 *_minor 为数据库INTEGER(分)；API仍传十进制整数字符串。
-- 股价/每股股息/比率为TEXT，服务层Decimal处理；SQL不以REAL保存或计算金额。
-- canonical_payload须为canonical-json-v1完整对象；json_valid仅验证JSON语法，
-- 不证明canonical排序、金额语义、字段与核心列一致、SHA256正确或历史可知性。



CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    checksum TEXT NOT NULL CHECK (length(checksum)=64 AND checksum NOT GLOB '*[^0-9a-f]*'),
    applied_at TEXT NOT NULL CHECK (substr(applied_at,-1)='Z'),
    description TEXT NOT NULL
);

-- Jobs的resource_type/resource_id为多态结果引用；其存在、归属由服务校验。
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('snapshot','seat','case','review','replay','validation')),
    state TEXT NOT NULL CHECK (state IN ('queued','running','succeeded','partial','failed','cancelled')),
    phase TEXT NOT NULL DEFAULT '',
    completed_units INTEGER NOT NULL DEFAULT 0 CHECK (typeof(completed_units)='integer' AND completed_units>=0),
    total_units INTEGER CHECK (total_units IS NULL OR (typeof(total_units)='integer' AND total_units>=completed_units)),
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (typeof(attempt)='integer' AND attempt>=1),
    resource_type TEXT CHECK (resource_type IS NULL OR resource_type IN ('snapshot','seat_run','case','review','replay','validation')),
    resource_id TEXT,
    replay_id TEXT,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    started_at TEXT CHECK (started_at IS NULL OR substr(started_at,-1)='Z'),
    finished_at TEXT CHECK (finished_at IS NULL OR substr(finished_at,-1)='Z'),
    error_json TEXT CHECK (error_json IS NULL OR json_valid(error_json)),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((resource_type IS NULL)=(resource_id IS NULL))
);
CREATE INDEX jobs_queue_idx ON jobs(state,created_at,job_id);
CREATE INDEX jobs_replay_idx ON jobs(replay_id,state);

-- 响应与业务对象必须在同一事务写入；相同key+不同request_sha256由服务返回409。
-- POST普通保留期可过期，journal/冻结/import等资源的键不自动清理。
CREATE TABLE idempotency_keys (
    method TEXT NOT NULL CHECK (method='POST'),
    request_path TEXT NOT NULL CHECK (substr(request_path,1,1)='/'),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    response_status INTEGER NOT NULL CHECK (response_status BETWEEN 200 AND 599),
    response_json TEXT NOT NULL CHECK (json_valid(response_json)),
    resource_type TEXT,
    resource_id TEXT,
    job_id TEXT,
    replay_id TEXT,
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    expires_at TEXT CHECK (expires_at IS NULL OR substr(expires_at,-1)='Z'),
    PRIMARY KEY (method,request_path,idempotency_key),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX idempotency_expiry_idx ON idempotency_keys(expires_at);

-- 可重建投影。paper模式是否允许该笔成交、现金非负、T+1等仍由服务在事务中检查。
CREATE TABLE portfolios (
    portfolio_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('paper','user_reported')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (typeof(revision)='integer' AND revision>=0),
    cash_minor INTEGER NOT NULL CHECK (typeof(cash_minor)='integer'),
    market_value_minor INTEGER CHECK (market_value_minor IS NULL OR (typeof(market_value_minor)='integer' AND market_value_minor>=0)),
    total_equity_minor INTEGER CHECK (total_equity_minor IS NULL OR typeof(total_equity_minor)='integer'),
    quality TEXT NOT NULL CHECK (quality IN ('complete','partial','unavailable')),
    last_event_sequence INTEGER NOT NULL DEFAULT 0 CHECK (typeof(last_event_sequence)='integer' AND last_event_sequence>=0),
    replay_id TEXT UNIQUE,
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    updated_at TEXT NOT NULL CHECK (substr(updated_at,-1)='Z'),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED
);

-- replay和其isolated_portfolio相互引用：必须在同一个显式事务创建。
-- jobs可先创建(resource_id预分配)，再portfolios、replay_sessions；外键延至COMMIT。
CREATE TABLE replay_sessions (
    replay_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('queued','active','completed','revealed','failed')),
    start_session_date TEXT NOT NULL CHECK (length(start_session_date)=10),
    end_session_date TEXT NOT NULL CHECK (length(end_session_date)=10 AND end_session_date>=start_session_date),
    cursor_session_date TEXT NOT NULL CHECK (length(cursor_session_date)=10 AND cursor_session_date BETWEEN start_session_date AND end_session_date),
    knowledge_cutoff_at TEXT NOT NULL CHECK (substr(knowledge_cutoff_at,-1)='Z'),
    strict_as_of INTEGER NOT NULL DEFAULT 1 CHECK (strict_as_of=1),
    revealed INTEGER NOT NULL DEFAULT 0 CHECK (revealed IN (0,1)),
    visible_snapshot_id TEXT,
    calendar_version TEXT NOT NULL,
    data_archive_fingerprint TEXT NOT NULL CHECK (length(data_archive_fingerprint)=64 AND data_archive_fingerprint NOT GLOB '*[^0-9a-f]*'),
    isolated_portfolio_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    revealed_at TEXT CHECK (revealed_at IS NULL OR substr(revealed_at,-1)='Z'),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (isolated_portfolio_id) REFERENCES portfolios(portfolio_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (visible_snapshot_id) REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((revealed=0 AND revealed_at IS NULL) OR (revealed=1 AND revealed_at IS NOT NULL AND state='revealed'))
);
CREATE INDEX replay_state_idx ON replay_sessions(state,created_at);
CREATE TRIGGER replay_window_immutable BEFORE UPDATE ON replay_sessions
WHEN NEW.replay_id IS NOT OLD.replay_id
  OR NEW.start_session_date IS NOT OLD.start_session_date
  OR NEW.end_session_date IS NOT OLD.end_session_date
  OR NEW.data_archive_fingerprint IS NOT OLD.data_archive_fingerprint
  OR NEW.isolated_portfolio_id IS NOT OLD.isolated_portfolio_id
  OR NEW.calendar_version IS NOT OLD.calendar_version
BEGIN SELECT RAISE(ABORT,'REPLAY_FROZEN_CONFIGURATION'); END;
CREATE TRIGGER replay_reveal_terminal BEFORE UPDATE ON replay_sessions
WHEN OLD.revealed=1
BEGIN SELECT RAISE(ABORT,'REPLAY_ALREADY_REVEALED'); END;

CREATE TABLE snapshots (
    snapshot_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL CHECK (length(fingerprint)=64 AND fingerprint NOT GLOB '*[^0-9a-f]*'),
    session_date TEXT NOT NULL CHECK (length(session_date)=10),
    quote_date TEXT CHECK (quote_date IS NULL OR length(quote_date)=10),
    latest_completed_session_date TEXT CHECK (latest_completed_session_date IS NULL OR length(latest_completed_session_date)=10),
    captured_at TEXT NOT NULL CHECK (substr(captured_at,-1)='Z'),
    generated_at TEXT NOT NULL CHECK (substr(generated_at,-1)='Z'),
    quality TEXT NOT NULL CHECK (quality IN ('complete','partial','unavailable')),
    freshness TEXT NOT NULL CHECK (freshness IN ('fresh','stale','unknown')),
    feed_precision TEXT NOT NULL CHECK (feed_precision IN ('daily_bar','date_snapshot','intraday_snapshot','tick','mixed','unknown')),
    source_mode TEXT NOT NULL CHECK (source_mode IN ('provider_api','file_import','user_manual','fixture','mixed')),
    universe_policy_id TEXT NOT NULL,
    universe_fingerprint TEXT NOT NULL CHECK (length(universe_fingerprint)=64 AND universe_fingerprint NOT GLOB '*[^0-9a-f]*'),
    price_basis TEXT NOT NULL CHECK (price_basis IN ('raw','as_of_adjusted','mixed','unknown')),
    adjustment_policy_version TEXT,
    calendar_version TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic IN (0,1)),
    replay_id TEXT,
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX snapshots_session_idx ON snapshots(replay_id,session_date,captured_at);
CREATE INDEX snapshots_fingerprint_idx ON snapshots(fingerprint);
CREATE TRIGGER snapshots_no_update BEFORE UPDATE ON snapshots
BEGIN SELECT RAISE(ABORT,'SNAPSHOT_IMMUTABLE'); END;
CREATE TRIGGER snapshots_no_delete BEFORE DELETE ON snapshots
BEGIN SELECT RAISE(ABORT,'SNAPSHOT_IMMUTABLE'); END;

-- latest可变指针与不可变快照分离；同事务先插快照再切head。
CREATE TABLE snapshot_heads (
    namespace TEXT PRIMARY KEY,
    replay_id TEXT UNIQUE,
    snapshot_id TEXT NOT NULL,
    updated_at TEXT NOT NULL CHECK (substr(updated_at,-1)='Z'),
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((replay_id IS NULL AND namespace='live') OR (replay_id IS NOT NULL AND namespace='replay:'||replay_id))
);

CREATE TABLE evidence (
    evidence_id TEXT PRIMARY KEY,
    snapshot_id TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('api_fact','user_statement','book_principle','rule_derivation','research_hypothesis')),
    symbol TEXT,
    source_id TEXT NOT NULL,
    authority_type TEXT NOT NULL CHECK (authority_type IN ('exchange','issuer','vendor','user','synthetic')),
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    observed_at TEXT CHECK (observed_at IS NULL OR substr(observed_at,-1)='Z'),
    available_at TEXT CHECK (available_at IS NULL OR substr(available_at,-1)='Z'),
    first_observed_at TEXT CHECK (first_observed_at IS NULL OR substr(first_observed_at,-1)='Z'),
    captured_at TEXT NOT NULL CHECK (substr(captured_at,-1)='Z'),
    time_precision TEXT NOT NULL CHECK (time_precision IN ('exact','date_only','unknown')),
    availability_basis TEXT NOT NULL CHECK (availability_basis IN ('recorded_observation','authoritative_publication','unknown')),
    quality TEXT NOT NULL CHECK (quality IN ('verified','unverified','missing','conflict')),
    replay_id TEXT,
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX evidence_snapshot_time_idx ON evidence(snapshot_id,available_at);
CREATE INDEX evidence_namespace_time_idx ON evidence(replay_id,available_at,first_observed_at);
CREATE INDEX evidence_symbol_idx ON evidence(symbol,available_at);
CREATE TRIGGER evidence_no_update BEFORE UPDATE ON evidence
BEGIN SELECT RAISE(ABORT,'EVIDENCE_IMMUTABLE'); END;
CREATE TRIGGER evidence_no_delete BEFORE DELETE ON evidence
BEGIN SELECT RAISE(ABORT,'EVIDENCE_IMMUTABLE'); END;

CREATE TABLE seat_registry_versions (
    registry_version TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE CHECK (length(fingerprint)=64 AND fingerprint NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    CHECK (json_type(canonical_payload,'$.seats')='array' AND json_array_length(canonical_payload,'$.seats')=13)
);
CREATE TRIGGER registry_no_update BEFORE UPDATE ON seat_registry_versions
BEGIN SELECT RAISE(ABORT,'REGISTRY_IMMUTABLE'); END;
CREATE TRIGGER registry_no_delete BEFORE DELETE ON seat_registry_versions
BEGIN SELECT RAISE(ABORT,'REGISTRY_IMMUTABLE'); END;

CREATE TABLE cases (
    case_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    cycle_id TEXT NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL,
    registry_version TEXT NOT NULL,
    primary_strategy_id TEXT NOT NULL,
    supersedes_case_id TEXT REFERENCES cases(case_id) DEFERRABLE INITIALLY DEFERRED,
    state TEXT NOT NULL CHECK (state IN ('queued','preparing','initial_round','challenge_round','synthesizing','succeeded','partial','failed')),
    replay_id TEXT,
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    finished_at TEXT CHECK (finished_at IS NULL OR substr(finished_at,-1)='Z'),
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    final_json TEXT CHECK (final_json IS NULL OR json_valid(final_json)),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (registry_version) REFERENCES seat_registry_versions(registry_version) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX cases_state_idx ON cases(state,created_at);
CREATE INDEX cases_namespace_idx ON cases(replay_id,snapshot_id);

CREATE TABLE seat_runs (
    seat_run_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    case_id TEXT,
    seat_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    registry_version TEXT NOT NULL,
    round TEXT NOT NULL CHECK (round IN ('initial','challenge','final')),
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (typeof(attempt)='integer' AND attempt>=1),
    run_state TEXT NOT NULL CHECK (run_state IN ('queued','running','succeeded','partial','failed','skipped')),
    applicability TEXT NOT NULL CHECK (applicability IN ('applicable','inapplicable','unknown')),
    verdict TEXT NOT NULL CHECK (verdict IN ('conditions_met','conditions_failed','watch','unknown')),
    candidate_count INTEGER CHECK (candidate_count IS NULL OR (typeof(candidate_count)='integer' AND candidate_count>=0)),
    replay_id TEXT,
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    finished_at TEXT CHECK (finished_at IS NULL OR substr(finished_at,-1)='Z'),
    fingerprints_json TEXT NOT NULL CHECK (json_valid(fingerprints_json)),
    error_json TEXT CHECK (error_json IS NULL OR json_valid(error_json)),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    UNIQUE (case_id,round,seat_id,attempt),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (registry_version) REFERENCES seat_registry_versions(registry_version) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (run_state NOT IN ('queued','running','failed') OR verdict='unknown'),
    CHECK (applicability<>'inapplicable' OR (verdict='unknown' AND candidate_count IS NULL))
);
CREATE INDEX seat_runs_case_idx ON seat_runs(case_id,round,seat_id);
CREATE INDEX seat_runs_job_idx ON seat_runs(job_id);
CREATE INDEX seat_runs_snapshot_idx ON seat_runs(replay_id,snapshot_id,seat_id);

CREATE TABLE plan_versions (
    plan_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (typeof(version)='integer' AND version>=1),
    previous_version INTEGER CHECK (previous_version IS NULL OR (typeof(previous_version)='integer' AND previous_version>=1 AND previous_version<version)),
    change_reason TEXT NOT NULL,
    effective_from TEXT NOT NULL CHECK(substr(effective_from,-1)='Z'),
    portfolio_id TEXT REFERENCES portfolios(portfolio_id) DEFERRABLE INITIALLY DEFERRED,
    basis_portfolio_revision INTEGER CHECK(basis_portfolio_revision IS NULL OR (typeof(basis_portfolio_revision)='integer' AND basis_portfolio_revision>=0)),
    case_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    primary_strategy_id TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('research','simulation','user_trade_record')),
    status TEXT NOT NULL CHECK (status IN ('draft','frozen')),
    execution_eligibility TEXT NOT NULL CHECK (execution_eligibility IN ('eligible','ineligible','unknown')),
    planned_shares INTEGER CHECK (planned_shares IS NULL OR (typeof(planned_shares)='integer' AND planned_shares>0)),
    risk_budget_minor INTEGER CHECK (risk_budget_minor IS NULL OR (typeof(risk_budget_minor)='integer' AND risk_budget_minor>=0)),
    max_position_ratio TEXT CHECK (max_position_ratio IS NULL OR (typeof(max_position_ratio)='text' AND length(max_position_ratio)>0)),
    exit_deadline TEXT CHECK (exit_deadline IS NULL OR substr(exit_deadline,-1)='Z'),
    start_session_date TEXT NOT NULL CHECK (length(start_session_date)=10),
    end_session_date TEXT NOT NULL CHECK (length(end_session_date)=10 AND end_session_date>=start_session_date),
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    frozen_at TEXT CHECK (frozen_at IS NULL OR substr(frozen_at,-1)='Z'),
    frozen_hash TEXT UNIQUE CHECK (frozen_hash IS NULL OR (length(frozen_hash)=64 AND frozen_hash NOT GLOB '*[^0-9a-f]*')),
    replay_id TEXT,
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    PRIMARY KEY (plan_id,version),
    UNIQUE (plan_id,version,frozen_hash),
    FOREIGN KEY (plan_id,previous_version) REFERENCES plan_versions(plan_id,version) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((status='draft' AND frozen_at IS NULL AND frozen_hash IS NULL) OR (status='frozen' AND frozen_at IS NOT NULL AND frozen_hash IS NOT NULL)),
    CHECK ((version=1 AND previous_version IS NULL) OR (version>1 AND previous_version IS NOT NULL))
);
CREATE INDEX plan_case_idx ON plan_versions(case_id,plan_id,version);
CREATE INDEX plan_namespace_idx ON plan_versions(replay_id,status,created_at);
CREATE TRIGGER plan_frozen_no_update BEFORE UPDATE ON plan_versions
WHEN OLD.status='frozen'
BEGIN SELECT RAISE(ABORT,'PLAN_FROZEN_IMMUTABLE'); END;
CREATE TRIGGER plan_frozen_no_delete BEFORE DELETE ON plan_versions
WHEN OLD.status='frozen'
BEGIN SELECT RAISE(ABORT,'PLAN_FROZEN_IMMUTABLE'); END;
CREATE TRIGGER plan_identity_no_update BEFORE UPDATE ON plan_versions
WHEN NEW.plan_id IS NOT OLD.plan_id OR NEW.version IS NOT OLD.version OR NEW.previous_version IS NOT OLD.previous_version
BEGIN SELECT RAISE(ABORT,'PLAN_VERSION_IDENTITY_IMMUTABLE'); END;

CREATE TABLE reviews (
    review_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL CHECK (typeof(plan_version)='integer' AND plan_version>=1),
    frozen_hash TEXT,
    review_kind TEXT NOT NULL CHECK (review_kind IN ('preflight','post_trade')),
    preflight_eligibility TEXT CHECK (preflight_eligibility IS NULL OR preflight_eligibility IN ('eligible','ineligible','unknown')),
    as_of_snapshot_id TEXT NOT NULL,
    run_state TEXT NOT NULL CHECK (run_state IN ('queued','running','succeeded','partial','failed','skipped')),
    directional_price_return_ratio TEXT,
    total_return_ratio TEXT,
    executed_return_ratio TEXT,
    benchmark_return_ratio TEXT,
    max_drawdown_ratio TEXT,
    replay_id TEXT,
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (plan_id,plan_version) REFERENCES plan_versions(plan_id,version) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (plan_id,plan_version,frozen_hash) REFERENCES plan_versions(plan_id,version,frozen_hash) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (as_of_snapshot_id) REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (review_kind<>'post_trade' OR (frozen_hash IS NOT NULL AND preflight_eligibility IS NULL)),
    CHECK (review_kind<>'preflight' OR (directional_price_return_ratio IS NULL AND total_return_ratio IS NULL AND executed_return_ratio IS NULL AND benchmark_return_ratio IS NULL AND max_drawdown_ratio IS NULL))
);
CREATE INDEX reviews_plan_idx ON reviews(plan_id,plan_version,created_at);
CREATE INDEX reviews_namespace_idx ON reviews(replay_id,as_of_snapshot_id);

CREATE TABLE journal_events (
    event_id TEXT PRIMARY KEY,
    portfolio_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (typeof(sequence)='integer' AND sequence>=1),
    event_type TEXT NOT NULL CHECK (event_type IN ('opening_balance','cash_adjustment','paper_fill','manual_trade_record','watch_added','watch_removed','note','correction')),
    mode TEXT NOT NULL CHECK (mode IN ('paper','user_reported','imported_legacy')),
    effective_at TEXT NOT NULL CHECK (substr(effective_at,-1)='Z'),
    recorded_at TEXT NOT NULL CHECK (substr(recorded_at,-1)='Z'),
    session_date TEXT NOT NULL CHECK (length(session_date)=10),
    symbol TEXT,
    side TEXT CHECK (side IS NULL OR side IN ('buy','sell')),
    shares INTEGER CHECK (shares IS NULL OR (typeof(shares)='integer' AND shares>0)),
    price_decimal TEXT CHECK (price_decimal IS NULL OR (typeof(price_decimal)='text' AND length(price_decimal)>0)),
    gross_amount_minor INTEGER CHECK (gross_amount_minor IS NULL OR (typeof(gross_amount_minor)='integer' AND gross_amount_minor>=0)),
    net_cash_delta_minor INTEGER CHECK (net_cash_delta_minor IS NULL OR typeof(net_cash_delta_minor)='integer'),
    commission_minor INTEGER CHECK (commission_minor IS NULL OR (typeof(commission_minor)='integer' AND commission_minor>=0)),
    stamp_minor INTEGER CHECK (stamp_minor IS NULL OR (typeof(stamp_minor)='integer' AND stamp_minor>=0)),
    transfer_minor INTEGER CHECK (transfer_minor IS NULL OR (typeof(transfer_minor)='integer' AND transfer_minor>=0)),
    fee_total_minor INTEGER CHECK (fee_total_minor IS NULL OR (typeof(fee_total_minor)='integer' AND fee_total_minor>=0)),
    fee_model_version TEXT,
    snapshot_id TEXT,
    plan_id TEXT,
    plan_version INTEGER CHECK (plan_version IS NULL OR (typeof(plan_version)='integer' AND plan_version>=1)),
    corrects_event_id TEXT,
    correction_action TEXT CHECK (correction_action IS NULL OR correction_action IN ('void','replace')),
    corrected_event_type TEXT CHECK (corrected_event_type IS NULL OR corrected_event_type IN ('opening_balance','cash_adjustment','paper_fill','manual_trade_record','watch_added','watch_removed','note')),
    legacy_source_sha256 TEXT CHECK (legacy_source_sha256 IS NULL OR (length(legacy_source_sha256)=64 AND legacy_source_sha256 NOT GLOB '*[^0-9a-f]*')),
    legacy_trade_id TEXT,
    prev_event_hash TEXT,
    event_hash TEXT NOT NULL UNIQUE CHECK (length(event_hash)=64 AND event_hash NOT GLOB '*[^0-9a-f]*'),
    replay_id TEXT,
    idempotency_method TEXT NOT NULL DEFAULT 'POST',
    idempotency_path TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    UNIQUE (portfolio_id,sequence),
    UNIQUE (portfolio_id,event_id),
    UNIQUE (portfolio_id,event_hash),
    UNIQUE (legacy_source_sha256,legacy_trade_id),
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (plan_id,plan_version) REFERENCES plan_versions(plan_id,version) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (portfolio_id,corrects_event_id) REFERENCES journal_events(portfolio_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (portfolio_id,prev_event_hash) REFERENCES journal_events(portfolio_id,event_hash) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (idempotency_method,idempotency_path,idempotency_key) REFERENCES idempotency_keys(method,request_path,idempotency_key) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((plan_id IS NULL)=(plan_version IS NULL)),
    CHECK ((legacy_source_sha256 IS NULL)=(legacy_trade_id IS NULL)),
    CHECK (corrects_event_id IS NULL OR corrects_event_id<>event_id),
    CHECK ((event_type='correction' AND corrects_event_id IS NOT NULL AND correction_action IS NOT NULL AND corrected_event_type IS NOT NULL)
        OR (event_type<>'correction' AND corrects_event_id IS NULL AND correction_action IS NULL AND corrected_event_type IS NULL))
);
CREATE INDEX journal_portfolio_time_idx ON journal_events(portfolio_id,effective_at,sequence);
CREATE INDEX journal_plan_idx ON journal_events(plan_id,plan_version,session_date);
CREATE INDEX journal_namespace_idx ON journal_events(replay_id,portfolio_id,sequence);
CREATE TRIGGER journal_no_update BEFORE UPDATE ON journal_events
BEGIN SELECT RAISE(ABORT,'JOURNAL_APPEND_ONLY'); END;
CREATE TRIGGER journal_no_delete BEFORE DELETE ON journal_events
BEGIN SELECT RAISE(ABORT,'JOURNAL_APPEND_ONLY'); END;
CREATE TRIGGER journal_sequence_contiguous BEFORE INSERT ON journal_events
WHEN NEW.sequence <> COALESCE((SELECT MAX(sequence) FROM journal_events WHERE portfolio_id=NEW.portfolio_id),0)+1
BEGIN SELECT RAISE(ABORT,'JOURNAL_SEQUENCE_CONFLICT'); END;
CREATE TRIGGER journal_hash_chain BEFORE INSERT ON journal_events
WHEN NEW.prev_event_hash IS NOT (SELECT event_hash FROM journal_events WHERE portfolio_id=NEW.portfolio_id ORDER BY sequence DESC LIMIT 1)
BEGIN SELECT RAISE(ABORT,'JOURNAL_PREV_HASH_CONFLICT'); END;

CREATE TABLE positions_projection (
    portfolio_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    shares INTEGER NOT NULL CHECK (typeof(shares)='integer' AND shares>=0),
    available_shares INTEGER NOT NULL CHECK (typeof(available_shares)='integer' AND available_shares BETWEEN 0 AND shares),
    cost_basis_minor INTEGER NOT NULL CHECK (typeof(cost_basis_minor)='integer' AND cost_basis_minor>=0),
    last_price_decimal TEXT CHECK (last_price_decimal IS NULL OR (typeof(last_price_decimal)='text' AND length(last_price_decimal)>0)),
    market_value_minor INTEGER CHECK (market_value_minor IS NULL OR (typeof(market_value_minor)='integer' AND market_value_minor>=0)),
    unrealized_pnl_minor INTEGER CHECK (unrealized_pnl_minor IS NULL OR typeof(unrealized_pnl_minor)='integer'),
    unrealized_return_ratio TEXT,
    valuation_snapshot_id TEXT,
    projection_revision INTEGER NOT NULL CHECK (typeof(projection_revision)='integer' AND projection_revision>=0),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    PRIMARY KEY (portfolio_id,symbol),
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (valuation_snapshot_id) REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (last_price_decimal IS NOT NULL OR (market_value_minor IS NULL AND unrealized_pnl_minor IS NULL AND unrealized_return_ratio IS NULL))
);

CREATE TABLE import_manifests (
    import_id TEXT PRIMARY KEY,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256)=64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('uploaded_json','browser_local_storage','panel_legacy_files')),
    portfolio_id TEXT,
    dry_run INTEGER NOT NULL CHECK (dry_run IN (0,1)),
    status TEXT NOT NULL CHECK (status IN ('validated','committed','rejected')),
    accepted_event_count INTEGER NOT NULL CHECK (typeof(accepted_event_count)='integer' AND accepted_event_count>=0),
    duplicate_event_count INTEGER NOT NULL CHECK (typeof(duplicate_event_count)='integer' AND duplicate_event_count>=0),
    cash_reconciliation_delta_minor INTEGER CHECK (cash_reconciliation_delta_minor IS NULL OR typeof(cash_reconciliation_delta_minor)='integer'),
    backup_manifest_json TEXT CHECK (backup_manifest_json IS NULL OR json_valid(backup_manifest_json)),
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    UNIQUE (source_sha256,portfolio_id),
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (status<>'committed' OR (dry_run=0 AND portfolio_id IS NOT NULL AND cash_reconciliation_delta_minor=0 AND backup_manifest_json IS NOT NULL))
);
CREATE UNIQUE INDEX imports_one_commit_per_source_idx ON import_manifests(source_sha256) WHERE status='committed';
CREATE INDEX imports_status_idx ON import_manifests(status,created_at);

CREATE TABLE validation_runs (
    validation_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    target_type TEXT NOT NULL CHECK (target_type IN ('seat','plan','case')),
    target_id TEXT NOT NULL,
    target_version TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued','running','succeeded','partial','failed','skipped')),
    dataset_fingerprint TEXT NOT NULL CHECK (length(dataset_fingerprint)=64 AND dataset_fingerprint NOT GLOB '*[^0-9a-f]*'),
    rule_version TEXT NOT NULL,
    calendar_version TEXT NOT NULL,
    fee_model_version TEXT NOT NULL,
    execution_model_version TEXT NOT NULL,
    seed INTEGER NOT NULL CHECK (typeof(seed)='integer' AND seed>=0),
    start_session_date TEXT NOT NULL CHECK (length(start_session_date)=10),
    end_session_date TEXT NOT NULL CHECK (length(end_session_date)=10 AND end_session_date>=start_session_date),
    split_policy TEXT NOT NULL CHECK (split_policy IN ('walk_forward','holdout','full_sample_descriptive')),
    replay_id TEXT,
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    finished_at TEXT CHECK (finished_at IS NULL OR substr(finished_at,-1)='Z'),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json)),
    result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED
    -- target是多态引用；服务层必须校验具体席位/计划版本/Case实际存在且同namespace。
);
CREATE INDEX validation_target_idx ON validation_runs(target_type,target_id,target_version);
CREATE INDEX validation_state_idx ON validation_runs(state,created_at);

-- P4可选：未启用时表存在也不自动轮询或外发。
CREATE TABLE alert_events (
    alert_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    plan_id TEXT,
    plan_version INTEGER CHECK (plan_version IS NULL OR (typeof(plan_version)='integer' AND plan_version>=1)),
    snapshot_id TEXT NOT NULL,
    symbol TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('condition_met','invalidation_met','data_stale','review_due')),
    dedupe_key TEXT NOT NULL UNIQUE CHECK (length(dedupe_key)=64 AND dedupe_key NOT GLOB '*[^0-9a-f]*'),
    state TEXT NOT NULL CHECK (state IN ('unread','acknowledged')),
    replay_id TEXT,
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    acknowledged_at TEXT CHECK (acknowledged_at IS NULL OR substr(acknowledged_at,-1)='Z'),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    FOREIGN KEY (plan_id,plan_version) REFERENCES plan_versions(plan_id,version) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((plan_id IS NULL)=(plan_version IS NULL)),
    CHECK ((state='unread' AND acknowledged_at IS NULL) OR (state='acknowledged' AND acknowledged_at IS NOT NULL))
);
CREATE INDEX alerts_inbox_idx ON alert_events(replay_id,state,created_at);

-- P3轻量卡牌：source_type/source_id/source_version多态来源由服务逐类校验。
CREATE TABLE card_decks (
    deck_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (typeof(version)='integer' AND version>=1),
    source_type TEXT NOT NULL CHECK (source_type IN ('case','plan','review')),
    source_id TEXT NOT NULL,
    source_version TEXT,
    replay_id TEXT,
    created_at TEXT NOT NULL CHECK (substr(created_at,-1)='Z'),
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    PRIMARY KEY (deck_id,version),
    FOREIGN KEY (replay_id) REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX decks_source_idx ON card_decks(source_type,source_id,source_version);

-- 迁移器在确认此文件SHA256后插入schema_migrations，而非在这里自造checksum。

-- 服务层写journal的事务模板（示意；不要直接执行）：
-- BEGIN IMMEDIATE;
-- 1. 查询(method,path,key)，匹配request_sha256则返回已保存响应；不匹配返回409。
-- 2. 校验portfolio.revision，读取事件和当时可见snapshot，Decimal算金额与费用。
-- 3. INSERT idempotency_keys(...response_json为此次确定响应...);
-- 4. INSERT journal_events(...sequence=old+1,prev_event_hash=old_hash...);
-- 5. UPDATE portfolios SET revision=revision+1,cash_minor=?,last_event_sequence=?,...
--      WHERE portfolio_id=? AND revision=?; 必须检查受影响行数=1。
-- 6. 同事务更新positions_projection；任何错误ROLLBACK，不单独提交事件或幂等响应。
-- COMMIT;
-- 源文件导入：同事务写import_manifests+portfolio+opening/trade事件+投影+幂等响应，
-- 但原字节备份先落盘并校验哈希；SQL COMMIT失败时留下可追溯未提交备份，不删原文件。


-- Contract additions after independent specification review. This is a starting DDL,
-- not a completed application or migration of the supplied source system.
CREATE TRIGGER cases_terminal_immutable BEFORE UPDATE ON cases
WHEN OLD.state IN ('succeeded','partial','failed')
BEGIN SELECT RAISE(ABORT,'CASE_TERMINAL_IMMUTABLE'); END;
CREATE TRIGGER plan_simulation_binding_insert BEFORE INSERT ON plan_versions
WHEN NEW.purpose='simulation' AND (NEW.portfolio_id IS NULL OR NEW.basis_portfolio_revision IS NULL OR json_array_length(NEW.canonical_payload,'$.symbols')<>1)
BEGIN SELECT RAISE(ABORT,'SIMULATION_PORTFOLIO_BINDING_REQUIRED'); END;
CREATE TRIGGER plan_simulation_binding_update BEFORE UPDATE ON plan_versions
WHEN NEW.purpose='simulation' AND (NEW.portfolio_id IS NULL OR NEW.basis_portfolio_revision IS NULL OR json_array_length(NEW.canonical_payload,'$.symbols')<>1)
BEGIN SELECT RAISE(ABORT,'SIMULATION_PORTFOLIO_BINDING_REQUIRED'); END;

CREATE TABLE local_sessions (
    session_id TEXT PRIMARY KEY,
    session_token_hash TEXT NOT NULL UNIQUE CHECK(length(session_token_hash)=64),
    csrf_token TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('live','replay')),
    replay_id TEXT REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED,
    knowledge_cutoff_at TEXT,
    context_revision INTEGER NOT NULL CHECK(typeof(context_revision)='integer' AND context_revision>=0),
    created_at TEXT NOT NULL CHECK(substr(created_at,-1)='Z'),
    expires_at TEXT NOT NULL CHECK(substr(expires_at,-1)='Z'),
    CHECK((mode='live' AND replay_id IS NULL AND knowledge_cutoff_at IS NULL) OR (mode='replay' AND replay_id IS NOT NULL AND knowledge_cutoff_at IS NOT NULL))
);
CREATE INDEX local_session_expiry_idx ON local_sessions(expires_at);

CREATE TABLE evaluation_manifests (
    manifest_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=1),
    created_at TEXT NOT NULL CHECK(substr(created_at,-1)='Z'),
    frozen_at TEXT NOT NULL CHECK(substr(frozen_at,-1)='Z'),
    frozen_hash TEXT NOT NULL UNIQUE CHECK(length(frozen_hash)=64),
    content_json TEXT NOT NULL CHECK(json_valid(content_json)),
    canonical_payload TEXT NOT NULL CHECK(json_valid(canonical_payload)),
    PRIMARY KEY(manifest_id,version),
    UNIQUE(manifest_id,version,frozen_hash)
);
CREATE TRIGGER evaluation_manifest_no_update BEFORE UPDATE ON evaluation_manifests
BEGIN SELECT RAISE(ABORT,'EVALUATION_MANIFEST_IMMUTABLE'); END;
CREATE TRIGGER evaluation_manifest_no_delete BEFORE DELETE ON evaluation_manifests
BEGIN SELECT RAISE(ABORT,'EVALUATION_MANIFEST_IMMUTABLE'); END;
ALTER TABLE validation_runs ADD COLUMN manifest_id TEXT;
ALTER TABLE validation_runs ADD COLUMN manifest_version INTEGER;
CREATE INDEX validation_manifest_idx ON validation_runs(manifest_id,manifest_version);
-- SQLite cannot add a composite FK with ALTER. Enforce reference on insert here,
-- and include the actual composite FK when implementation consolidates migration 001.
CREATE TRIGGER validation_manifest_required BEFORE INSERT ON validation_runs
WHEN NEW.manifest_id IS NULL OR NEW.manifest_version IS NULL OR NOT EXISTS(SELECT 1 FROM evaluation_manifests m WHERE m.manifest_id=NEW.manifest_id AND m.version=NEW.manifest_version)
BEGIN SELECT RAISE(ABORT,'FROZEN_EVALUATION_MANIFEST_REQUIRED'); END;
CREATE TRIGGER validation_terminal_immutable BEFORE UPDATE ON validation_runs
WHEN OLD.state IN ('succeeded','partial','failed','skipped')
BEGIN SELECT RAISE(ABORT,'VALIDATION_RESULT_IMMUTABLE'); END;

CREATE TABLE plan_lifecycle_events (
    event_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    frozen_hash TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('activated','trigger_observed','cancelled','expired','execution_unfilled','execution_filled')),
    effective_at TEXT NOT NULL CHECK(substr(effective_at,-1)='Z'),
    recorded_at TEXT NOT NULL CHECK(substr(recorded_at,-1)='Z'),
    snapshot_id TEXT REFERENCES snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    journal_event_id TEXT REFERENCES journal_events(event_id) DEFERRABLE INITIALLY DEFERRED,
    previous_event_id TEXT REFERENCES plan_lifecycle_events(event_id) DEFERRABLE INITIALLY DEFERRED,
    replay_id TEXT REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED,
    reason TEXT NOT NULL,
    canonical_payload TEXT NOT NULL CHECK(json_valid(canonical_payload)),
    FOREIGN KEY(plan_id,plan_version,frozen_hash) REFERENCES plan_versions(plan_id,version,frozen_hash) DEFERRABLE INITIALLY DEFERRED,
    CHECK(event_type<>'execution_filled' OR journal_event_id IS NOT NULL)
);
CREATE UNIQUE INDEX plan_first_lifecycle_idx ON plan_lifecycle_events(plan_id,plan_version) WHERE previous_event_id IS NULL;
CREATE UNIQUE INDEX plan_lifecycle_next_idx ON plan_lifecycle_events(previous_event_id) WHERE previous_event_id IS NOT NULL;
CREATE INDEX plan_lifecycle_time_idx ON plan_lifecycle_events(plan_id,plan_version,recorded_at);
CREATE TRIGGER plan_lifecycle_no_update BEFORE UPDATE ON plan_lifecycle_events
BEGIN SELECT RAISE(ABORT,'PLAN_EVENT_APPEND_ONLY'); END;
CREATE TRIGGER plan_lifecycle_no_delete BEFORE DELETE ON plan_lifecycle_events
BEGIN SELECT RAISE(ABORT,'PLAN_EVENT_APPEND_ONLY'); END;

CREATE TABLE control_policy_versions (
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK(substr(created_at,-1)='Z'),
    policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256)=64),
    config_json TEXT NOT NULL CHECK(json_valid(config_json)),
    PRIMARY KEY(policy_id,policy_version)
);
CREATE TRIGGER control_policy_no_update BEFORE UPDATE ON control_policy_versions
BEGIN SELECT RAISE(ABORT,'CONTROL_POLICY_IMMUTABLE'); END;
CREATE TRIGGER control_policy_no_delete BEFORE DELETE ON control_policy_versions
BEGIN SELECT RAISE(ABORT,'CONTROL_POLICY_IMMUTABLE'); END;
CREATE TABLE portfolio_control_events (
    event_id TEXT PRIMARY KEY,
    portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id) DEFERRABLE INITIALLY DEFERRED,
    control_type TEXT NOT NULL CHECK(control_type='cooldown'),
    event_type TEXT NOT NULL CHECK(event_type IN ('enabled','released','expired')),
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    effective_at TEXT NOT NULL CHECK(substr(effective_at,-1)='Z'),
    scheduled_end_at TEXT,
    recorded_at TEXT NOT NULL CHECK(substr(recorded_at,-1)='Z'),
    related_control_event_id TEXT REFERENCES portfolio_control_events(event_id) DEFERRABLE INITIALLY DEFERRED,
    previous_event_id TEXT REFERENCES portfolio_control_events(event_id) DEFERRABLE INITIALLY DEFERRED,
    replay_id TEXT REFERENCES replay_sessions(replay_id) DEFERRABLE INITIALLY DEFERRED,
    reason TEXT NOT NULL,
    canonical_payload TEXT NOT NULL CHECK(json_valid(canonical_payload)),
    FOREIGN KEY(policy_id,policy_version) REFERENCES control_policy_versions(policy_id,policy_version) DEFERRABLE INITIALLY DEFERRED,
    CHECK(event_type='enabled' OR related_control_event_id IS NOT NULL)
);
CREATE UNIQUE INDEX portfolio_control_first_idx ON portfolio_control_events(portfolio_id,control_type) WHERE previous_event_id IS NULL;
CREATE UNIQUE INDEX portfolio_control_next_idx ON portfolio_control_events(previous_event_id) WHERE previous_event_id IS NOT NULL;
CREATE INDEX portfolio_control_time_idx ON portfolio_control_events(portfolio_id,recorded_at);
CREATE TRIGGER portfolio_control_no_update BEFORE UPDATE ON portfolio_control_events
BEGIN SELECT RAISE(ABORT,'CONTROL_EVENT_APPEND_ONLY'); END;
CREATE TRIGGER portfolio_control_no_delete BEFORE DELETE ON portfolio_control_events
BEGIN SELECT RAISE(ABORT,'CONTROL_EVENT_APPEND_ONLY'); END;

CREATE TRIGGER validation_manifest_binding_immutable BEFORE UPDATE ON validation_runs
WHEN NEW.manifest_id IS NOT OLD.manifest_id OR NEW.manifest_version IS NOT OLD.manifest_version
BEGIN SELECT RAISE(ABORT,'VALIDATION_MANIFEST_BINDING_IMMUTABLE'); END;
CREATE TRIGGER cases_terminal_no_delete BEFORE DELETE ON cases
WHEN OLD.state IN ('succeeded','partial','failed')
BEGIN SELECT RAISE(ABORT,'CASE_TERMINAL_IMMUTABLE'); END;
