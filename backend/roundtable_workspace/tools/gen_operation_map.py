#!/usr/bin/env python3
"""生成 tests/acceptance/operation_map.json：136 个用例层动作别名 → 真实 operationId / 内部服务 / 浏览器 driver。

    cd WORK_ROOT && python3 tools/gen_operation_map.py

绑定是显式、逐条的（不模糊匹配端点）；每条写明 driver 位置与「结果投影从哪里来」。
运行本脚本会校验：目录里的每个别名都有绑定、每个 driver 函数存在、HTTP 别名的 operationId 与目录一致。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "records/roundtable/2026-10-18-圆桌面板设计"))

MODULES = {
    "infra": ["clock_set", "process_restart", "db_reopen", "fault_inject", "stage_modules_disable", "calendar_fault_set",
              "provider_fault_set", "http_fault_set", "database_backup", "database_restore_to_temp",
              "database_integrity_assert"],
    "data": ["snapshot_classify", "session_resolve", "snapshot_ingest", "normalize_provider_rows", "snapshot_request",
             "direction_return_calculate", "money_round_trip", "snapshot_build", "health_get", "business_readiness_check",
             "async_job_submit", "job_poll", "candidate_universe_build", "price_series_normalize", "price_series_adjust",
             "future_action_patch", "corporate_action_calculate", "return_calculate"],
    "security": ["server_config_inspect", "untrusted_origin_post", "csrf_write_attack_matrix",
                 "unauthenticated_data_request_matrix", "session_authenticate", "replay_data_request",
                 "replay_scoped_attack_matrix", "legacy_data_request_matrix", "adversarial_http_requests",
                 "secret_canary_inject", "force_provider_error", "acceptance_artifact_scan"],
    "seats": ["snapshot_get", "seat_run", "case_create", "case_supersede", "case_history_get", "seat_registry_import",
              "seat_adapter_run", "roundtable_summarize", "llm_response_validate"],
    "plans": ["plan_draft_create", "plan_preflight", "plan_freeze", "plan_freeze_matrix", "plan_freeze_retry",
              "plan_version_get", "plan_amend", "concurrent_plan_amend", "plan_amend_validate_matrix",
              "plan_effective_version_resolve", "rule_origin_validate", "discipline_review", "review_create"],
    "ledger": ["journal_event_append", "behavior_summary", "user_position_import", "simulation_sell_preflight",
               "simulation_preflight_matrix", "simulation_preflight", "instrument_status_set", "simulation_execute",
               "simulation_resolve", "simulation_fee_round_trip", "portfolio_mark", "simulation_reset",
               "risk_action_preflight_matrix", "cooldown_rule_register", "cooldown_activate",
               "control_state_rebuild_from_events", "cooldown_override", "checklist_submit",
               "simulation_command_validate_matrix", "simulation_commands_concurrent"],
    "legacy": ["source_manifest_capture", "source_registration_dry_run", "legacy_import", "legacy_import_resume",
               "legacy_evaluation_import"],
    "replay": ["replay_payload_get", "replay_universe_get", "replay_run", "future_fixture_replace", "replay_fact_inject"],
    "evaluation": ["validation_protocol_freeze", "validation_protocol_update", "quote_append",
                   "prospective_observation_settle", "evaluation_split_validate", "evaluation_enqueue",
                   "evaluation_manifest_freeze", "evaluation_worker_inspect", "evaluation_manifest_freeze_matrix",
                   "evaluation_manifest_patch", "evaluation_run_to_budget", "evaluation_status_get",
                   "evaluation_integrity_fault", "evaluation_schedule_next", "statistical_report_build",
                   "statistical_oracle_run", "validation_batch_run", "validation_claim_check", "ablation_protocol_validate",
                   "cost_matched_evaluation_validate", "historical_llm_validation_run"],
    "alerts": ["alert_capability_preflight", "alert_session_enable", "alert_feed_disconnect", "alert_clock_advance",
               "alert_feed_reconnect"],
    "reporting": ["acceptance_report_generate"],
}
BROWSER = ["browser_open", "browser_reload", "browser_replay_open", "browser_network_cache_export_audit",
           "browser_acceptance_flow", "card_page_render", "market_width_render", "psychology_rules_render",
           "snapshot_render_projection", "validation_page_render"]
DEPLOY = ["deployment_persistence_restore", "deployment_rollback", "production_data_acceptance",
          "release_confirmation_binding", "release_without_confirmation", "remote_auth_boundary"]

# 结果投影来源（审阅用）：HTTP=真实 App 管线（cookie/Origin/CSRF/幂等键）；DB=SQLite 查询；
# 服务=panel/services|domain 的产品函数；BROWSER=Playwright 下的真实页面 DOM/网络/存储。
PROJECTION = {
    "clock_set": "注入时钟 panel.domain.clock.set_fixed（服务端唯一时钟源）",
    "process_restart": "同一 runtime 重新 build_app：迁移幂等、recover_on_boot、观察会话停止；随后执行登记的重启探针",
    "db_reopen": "关闭并重开连接 → PRAGMA integrity_check/foreign_key_check；DB 查询冻结版本、幂等记录、孤立事件；同键重试",
    "fault_inject": "panel.domain.faults.arm(point)：事件已写、投影/提交之前抛 InjectedFault（外层事务回滚）",
    "stage_modules_disable": "App.feature_gate 关闭指定阶段（501）；给 P1 服务函数装调用计数器",
    "calendar_fault_set": "calendar_service.set_override(unavailable=True)：current()/load() 一律 None",
    "provider_fault_set": "真实 NumcatAdapter + 假 meoz CLI（按剧本返回 429/超时/认证失败）；provider_max_retry 取 fixture",
    "http_fault_set": "BROWSER：Playwright route 对 /api/v1/** 返回指定状态码",
    "database_backup": "HTTP 写入 20 条账本事件（WAL 模式）→ Database.backup_now（SQLite backup API，含 WAL 已提交页）",
    "database_restore_to_temp": "panel.data.db.restore_to 到空目录（拒绝覆盖现用库）",
    "database_integrity_assert": "恢复库 PRAGMA integrity_check / foreign_key_check；末事件 ID、事件数、幂等记录逐条比对",
    "snapshot_classify": "服务 snapshot_service.current_freshness / snapshot_build.classify_freshness + HTTP GET latest?allow_stale",
    "session_resolve": "服务 TradingCalendar.resolve(clock.now())（fixture 日历）",
    "snapshot_ingest": "服务 fixtures.ingest_fixture_snapshot → snapshot_build.build + store_snapshot；DB 读回摘要与血缘；"
                       "invalid_rows 走 snapshot_service.ingest_explicit（SnapshotRejected=422）",
    "normalize_provider_rows": "服务 snapshot_build.normalize_units_demo",
    "snapshot_request": "HTTP POST /api/v1/snapshots（非交易日 422 details.session_date）；DB 快照行数差",
    "direction_return_calculate": "服务 money.ratio_str（缺价 → null）",
    "money_round_trip": "HTTP POST/GET /api/v1/portfolios；DB typeof(cash_minor)；node 执行前端 src/lib/money.ts 的解析",
    "snapshot_build": "HTTP POST /api/v1/snapshots → 轮询 jobs；假 CLI 调用日志计重试；DB 旧快照摘要前后比对；GET latest",
    "health_get": "HTTP GET /api/v1/health/data 与 /api/health",
    "business_readiness_check": "HTTP health/data：status=ready 才算业务就绪；验收是否通过只由报告决定",
    "async_job_submit": "HTTP POST /api/v1/snapshots（202 JobAccepted）；受控 provider 制造 worker 内失败",
    "job_poll": "HTTP GET /api/v1/jobs/{job_id} 直到终态；DB 确认 resource 已提交",
    "candidate_universe_build": "服务 trading_rules.classify_board + universe.build(universe_cn_mainboard_v1)",
    "price_series_normalize": "服务 corporate_actions.normalize_series",
    "price_series_adjust": "服务 corporate_actions.adjust_as_of（只用截点前已知行动）",
    "future_action_patch": "修改 fixture 中的未来公司行动（截点之后），供第二次 adjust_as_of 对照",
    "corporate_action_calculate": "服务 corporate_actions.cash_dividend（总额边界 HALF_UP）",
    "return_calculate": "服务 corporate_actions.return_basis（重复计息 → ApiError 422 DOUBLE_COUNTED_CORPORATE_ACTION）",
    "server_config_inspect": "panel.api.build_parser() 默认值、host_allowed()；Settings.allowed_origins()",
    "untrusted_origin_post": "HTTP POST /api/v1/portfolios（evil Origin）与 OPTIONS；DB 写入类表行数差",
    "csrf_write_attack_matrix": "HTTP POST /api/v1/portfolios（有效会话 + 可信 Origin，CSRF 缺失/他人/伪造）；DB 行数差",
    "unauthenticated_data_request_matrix": "HTTP GET snapshots/latest、replays/{id}、snapshots/{id}（无/过期/伪造会话）",
    "session_authenticate": "选择 fixture 会话：真实 bootstrap 的会话，服务端 bind_session_to_replay 绑定训练上下文",
    "replay_data_request": "HTTP GET latest/context/index/未来 live 快照/health（可选 X-Replay-Id、伪造 query）；扫描未来事实标记",
    "replay_scoped_attack_matrix": "HTTP：外来 replay 头、未来快照 ID、伪造 cutoff 请求体、伪造上下文头/第二个 cookie",
    "legacy_data_request_matrix": "HTTP GET /api/dashboard、/api/portfolio、/standalone.html（+ /data、/legacy 旁路）",
    "adversarial_http_requests": "HTTP：路径穿越、绝对路径、2 MiB 请求体、Origin: null；响应体查源包字节；DB 行数差",
    "secret_canary_inject": "把假 canary 放进 NUMCAT_API_KEY（只有被测 adapter 子进程能看到）",
    "force_provider_error": "HTTP POST /api/v1/snapshots；假 CLI 回显 canary 并认证失败",
    "acceptance_artifact_scan": "扫描本例 HTTP 记录、runtime 全部文件、产物目录、服务 stderr、frontend/dist",
    "snapshot_get": "HTTP GET /api/v1/snapshots/{id}；DB canonical_payload 哈希比对",
    "seat_run": "HTTP POST /api/v1/seat-runs → 轮询 → GET seat-runs/{id}；条件名 intraday_support→minute_bars，level2_intent→level2",
    "case_create": "HTTP POST /api/v1/cases → 轮询 → GET cases/{id} 与各 seat-run",
    "case_supersede": "HTTP POST /api/v1/cases（supersedes_case_id）",
    "case_history_get": "HTTP GET /api/v1/cases/{id}（新旧两个）+ GET plans/{id}",
    "seat_registry_import": "HTTP GET /api/v1/seats/registry（legacy_seat_id → canonical）",
    "seat_adapter_run": "服务 seat_runner.execute（周期不适用 → skipped）/ finalize_from_raw（受控原始输出）；"
                        "UI 标签由 node 执行前端 seatStatusLabel()",
    "roundtable_summarize": "服务 case_orchestrator._synthesize（同一事实多席引用）",
    "llm_response_validate": "服务 llm_provider.validate_claims/validate_text；事实表 = 快照证据 + 服务器上的截点后事实；DB 计划数差",
    "plan_draft_create": "HTTP POST /api/v1/plans（fixture plan_valid 映射为 PlanCreateRequest）",
    "plan_preflight": "HTTP POST /api/v1/plans（价位冲突 422 逐字段）或 POST /api/v1/reviews preflight",
    "plan_freeze": "HTTP POST /api/v1/plans/{id}/freeze（契约成功码 200）；预算变化 = 新版本 + 同键冻结 → 409",
    "plan_freeze_matrix": "HTTP POST plans + freeze（逐项缺失）；DB 新增冻结版本数",
    "plan_freeze_retry": "HTTP POST freeze ×N 同一幂等键；DB 冻结次数与版本数",
    "plan_version_get": "HTTP GET /api/v1/plans/{id}/versions/{v}（响应哈希）",
    "plan_amend": "HTTP POST /api/v1/plans/{id}/versions；DB 版本数差",
    "concurrent_plan_amend": "两个线程、两个会话同时 POST versions（同 base_version）",
    "plan_amend_validate_matrix": "HTTP POST versions：缺 change_reason / 缺 effective_from / 早于冻结时刻",
    "plan_effective_version_resolve": "服务 plan_service.effective_version(as_of)",
    "rule_origin_validate": "服务 psychology.validate_origin + HTTP POST /api/v1/plans（同一来源校验）",
    "discipline_review": "服务 review_service.discipline_review（规则=计划/登记的用户规则；事件=DB 日志）",
    "review_create": "HTTP POST /api/v1/reviews post_trade → 轮询 → GET reviews/{id}；as_of 快照为窗口终点收盘",
    "journal_event_append": "HTTP POST /api/v1/journal/events（diary_v1 观察 / user_reported 手工成交）",
    "behavior_summary": "HTTP GET journal/events + 服务 journal_service.behavior_summary",
    "user_position_import": "HTTP POST portfolios(user_reported) + journal manual_trade_record；网络守卫计数",
    "simulation_sell_preflight": "服务 journal_service.sell_preflight + 一次真实 HTTP 卖出（T1_NOT_SELLABLE）",
    "simulation_preflight_matrix": "HTTP POST journal paper_fill 于周末/节假日/午休；TradingCalendar.can_simulate_new_trade；DB 成交数差",
    "simulation_preflight": "HTTP POST journal paper_fill（日历不可用 → 422 CALENDAR_UNAVAILABLE）",
    "instrument_status_set": "合成盘中快照（suspend 数据集标记停牌）",
    "simulation_execute": "HTTP POST paper_fill（422 details.fill_status）+ DB 现金/持仓；或服务 fill_model.resolve_exit_on_daily",
    "simulation_resolve": "服务 fill_model.resolve_daily_bar",
    "simulation_fee_round_trip": "两笔真实 HTTP paper_fill（10/19 买、10/20 卖），从事件 net_cash_delta_minor 复算",
    "portfolio_mark": "HTTP GET /api/v1/portfolios/{id}?snapshot_id=（缺价快照）",
    "simulation_reset": "HTTP POST /api/v1/portfolios（同一幂等键 ×2）；DB 原账本事件哈希前后；verify_chain",
    "risk_action_preflight_matrix": "服务 journal_service.risk_action_allowed（冷却中）+ HTTP 记日记",
    "cooldown_rule_register": "control_policy_versions 登记用户规则（版本化、只追加）",
    "cooldown_activate": "HTTP POST plans/{id}/events(activated) + portfolios/{id}/controls/events(enabled) + "
                         "plans/{id}/events(execution_unfilled, 引用冷却事件)",
    "control_state_rebuild_from_events": "HTTP GET controls/events 与 plans/{id}/events + 服务 cooldown_state 重建",
    "cooldown_override": "HTTP POST controls/events(released) 同一幂等键 ×2；重启探针读 cooldown_state",
    "checklist_submit": "服务 psychology.submit（规则来自页面渲染的规则集或 psychology_rules.v1.json）",
    "simulation_command_validate_matrix": "HTTP POST paper_fill（缺 portfolio_id / 旧 revision / 多标的）；DB 事件数与 revision",
    "simulation_commands_concurrent": "两个线程同时 POST paper_fill（同 expected_portfolio_revision）",
    "source_manifest_capture": "读取只读源包全部文件原字节（size + sha256）",
    "source_registration_dry_run": "服务 source_registry.dry_run（MANIFEST-20261019.sha256 声明 vs 实际字节）；写入守卫计数",
    "legacy_import": "HTTP POST /api/v1/portfolio/import-legacy（第一批后故障注入）；另跑一份无冲突账本的中断恢复",
    "legacy_import_resume": "HTTP POST import-legacy 同键重试；DB 提交次数、冲突成交是否入账；源包字节",
    "legacy_evaluation_import": "服务 legacy_evaluation.material()（原件 sha256 登记）；源包字节前后",
    "replay_payload_get": "服务 replay_service.facts_payload（pit_visibility_policy_v1）；载荷存为不可变快照并复核",
    "replay_universe_get": "服务 universe.historical_members",
    "replay_run": "HTTP POST /api/v1/replays（合成归档）→ 可见快照 → 训练上下文 POST seat-runs；输入哈希只取原始数据集",
    "future_fixture_replace": "改写合成归档里截点之后的日线文件",
    "replay_fact_inject": "HTTP POST journal（训练上下文，引用截点后证据）→ 422 SOURCE_TIME_CONFLICT；扫描可见载荷",
    "validation_protocol_freeze": "HTTP POST /api/v1/validations（冻结 + 排队同一事务）；DB frozen_hash",
    "validation_protocol_update": "服务 validation_service.update_manifest（409）+ HTTP 重新提交得到新 validation_id",
    "quote_append": "服务 validation_service.quote_append（冻结后的报价登记为证据）",
    "prospective_observation_settle": "服务 validation_service.prospective_settle；DB frozen_hash；网络守卫计数",
    "evaluation_split_validate": "服务 validation_service.split_check + HTTP POST validations（training_window 重叠 → 422）",
    "evaluation_enqueue": "HTTP POST /api/v1/validations；草稿另经 validation_service.enqueue_frozen（未冻结 409）",
    "evaluation_manifest_freeze": "服务 validation_service.freeze_manifest（content-addressed，不可改）",
    "evaluation_worker_inspect": "轮询 job → validation_events.started.worker_manifest_hash",
    "evaluation_manifest_freeze_matrix": "HTTP POST validations（逐项删字段）；DB manifest/任务数差",
    "evaluation_manifest_patch": "服务 validation_service.update_manifest（409）；DB frozen_hash",
    "evaluation_run_to_budget": "HTTP POST validations（候选研究，trial_budget=2，合成归档）→ 轮询",
    "evaluation_status_get": "HTTP GET /api/v1/validations/{id} + validation_events（stopped）+ result_json",
    "evaluation_integrity_fault": "validation_service.INTEGRITY_HOOK 向截点输入注入未来日期行 → HTTP POST validations",
    "evaluation_schedule_next": "worker.drain 后 DB 任务数；validation_events/result_json",
    "statistical_report_build": "服务 stats.report（HAC / 移动块 bootstrap）",
    "statistical_oracle_run": "服务 stats.mean/hac_se/block_bootstrap_ci 对 serial_returns golden",
    "validation_batch_run": "服务 validation_service.record_outcomes；DB 追加式事件（DELETE 被触发器拒绝）",
    "validation_claim_check": "服务 validation_service.claim_check",
    "ablation_protocol_validate": "服务 validation_service.ablation_protocol（同快照/任务/截点）",
    "cost_matched_evaluation_validate": "三个臂真跑：HTTP seat-run（固定规则）、离线替身模型（单助手）、HTTP Case（完整圆桌）",
    "historical_llm_validation_run": "服务 validation_service.historical_llm_run（离线替身模型）；DB model_runs",
    "alert_capability_preflight": "服务 alert_service.evaluate + HTTP GET /api/v1/alerts（501）；线程/会话计数；重启探针",
    "alert_session_enable": "服务 AlertObservation.start → 闸门拒绝 → blocked；合成已核验能力下的引擎自检写入 result",
    "alert_feed_disconnect": "同上（前提会话不存在 → blocked）",
    "alert_clock_advance": "clock.advance",
    "alert_feed_reconnect": "同上（前提会话不存在 → blocked）",
    "acceptance_report_generate": "tests/acceptance/runner.build_report（dry：全部 not_run）",
    "browser_open": "BROWSER：Chromium 打开本地服务首页（真实会话 bootstrap）",
    "browser_reload": "BROWSER：重新加载；读取数据状态条、演示模式标记、执行按钮可用性",
    "browser_replay_open": "BROWSER：训练上下文会话 cookie 打开训练工作区；记录全部网络响应",
    "browser_network_cache_export_audit": "BROWSER：网络响应体、localStorage/sessionStorage/CacheStorage、DOM、导出文件中查未来事实",
    "browser_acceptance_flow": "BROWSER：1440×900 与 390×844 走主闭环；横向溢出、焦点陷阱、文字状态、截图",
    "card_page_render": "BROWSER：卡组页 DOM（无收益/连胜解锁、来源与分歧可见）",
    "market_width_render": "BROWSER：环境工作区两种分母与股票池 ID",
    "psychology_rules_render": "BROWSER：下单前自检的来源徽标（route 注入 fixture 规则集）",
    "snapshot_render_projection": "BROWSER：环境工作区行情表格（0 / 负值 / 缺值显示）+ HTTP 快照原值",
    "validation_page_render": "BROWSER：验证工作区旧资料卡（披露文字、无上界说法）",
}


def main() -> int:
    cat = json.loads((ROOT / "acceptance/operation_catalog.json").read_text(encoding="utf-8"))
    by = {o["scenario_operation_id"]: o for o in cat["operations"]}
    entries = []
    seen = set()
    for mod, names in MODULES.items():
        m = importlib.import_module(f"tests.acceptance.drivers.{mod}")
        for n in names:
            assert hasattr(m, n), f"missing driver {mod}.{n}"
            entries.append((n, f"tests/acceptance/drivers/{mod}.py::{n}"))
    pm = importlib.import_module("tests.acceptance.browser.pages")
    for n in BROWSER:
        assert hasattr(pm, n), f"missing browser driver {n}"
        entries.append((n, f"tests/acceptance/browser/pages.py::{n}"))
    for n in DEPLOY:
        entries.append((n, "tests/acceptance/drivers/reporting.py::deployment_not_run"))
    ops = []
    for n, drv in entries:
        if n in seen:
            raise SystemExit(f"duplicate binding {n}")
        seen.add(n)
        c = by.get(n)
        if c is None:
            raise SystemExit(f"alias {n} not in operation_catalog.json")
        ops.append({"scenario_operation_id": n, "binding_kind": c["binding_kind"],
                    "openapi_operation_id": c["openapi_operation_id"], "wire": c["wire"], "driver": drv,
                    "catalog_driver_path": c["driver_to_implement"],
                    "projection": PROJECTION.get(n, "部署阶段才执行（R1）" if n in DEPLOY else ""),
                    "status": "not_run_until_deployment" if n in DEPLOY else "implemented"})
    missing = sorted(set(by) - seen)
    if missing:
        raise SystemExit(f"unbound aliases: {missing}")
    out = {"schema_version": "1.0.0", "generated_by": "tools/gen_operation_map.py",
           "catalog": "acceptance/operation_catalog.json", "operation_count": len(ops),
           "rule": "每个别名显式绑定一个 driver；HTTP 别名在 driver 内调用目录指定的 operationId；"
                   "结果投影只来自真实 HTTP/DB/字节/浏览器，不读取 expect 值",
           "case_setup": {
               "AC-010": {"clock": "2026-10-19T08:00:00Z",
                          "reason": "请求 2026-10-19 会话快照的场景发生在该会话收盘并过发布延迟之后（16:00 上海）；"
                                    "此时 10/16 的旧快照按 freshness_policy_v1 已不是最新，失败后应标为陈旧"}},
           "case_notes": {
               "AC-029": "AC-029 期望 http_status=201，但 contracts/openapi.yaml 中 POST /api/v1/plans/{plan_id}/freeze 只定义 200；"
                         "实现按契约返回 200，driver 如实投影（规格冲突，待用户裁决）",
               "AC-072": "P4 能力闸门关闭：前提（已启用的观察会话）不可建立 → blocked",
               "AC-073": "同 AC-072"},
           "operations": sorted(ops, key=lambda o: o["scenario_operation_id"])}
    p = ROOT / "tests/acceptance/operation_map.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {p} ({len(ops)} operations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
