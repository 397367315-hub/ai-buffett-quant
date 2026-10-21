#!/usr/bin/env python3
"""生成逐任务执行记录 docs/implementation/task-status.md（BACKLOG 59 项）。

执行状态（todo / in_progress / blocked / done）与交付证据在下面的 STATUS 表里逐项登记——没有证据不能标 done；
验收状态从验收报告按 acceptance/task_test_map.json 的映射计算（PASS / FAIL / BLOCKED / NOT_RUN 计数），不手写。
用法：cd WORK_ROOT && python3 tools/gen_task_status.py --run-id <最终验收 run>
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parents[1]
WB = "records/roundtable/2026-10-18-圆桌面板设计"

# task_id: (执行状态, 交付证据, 说明)
STATUS = {
    "BASE-001": ("done", "docs/implementation/baseline.md", "只读 SOURCE_ROOT 与独立 WORK_ROOT；源包验收前后逐文件字节一致"),
    "BASE-002": ("done", f"{WB}/panel/domain/{{money,canonical,enums,schema}}.py；{WB}/panel/web/operations.json", "金额整数分、canonical-json-v1、拒浮点；路由由 openapi.yaml 生成"),
    "BASE-003": ("done", f"{WB}/panel/data/db.py；{WB}/panel/data/migrations/001_core_v1.sql、002_impl_additions.sql、003_card_decks_immutable.sql", "迁移带校验和、升级前一致性备份；不可变表有触发器"),
    "BASE-004": ("done", f"{WB}/panel/api.py；{WB}/panel/web/；{WB}/panel/jobs/", "127.0.0.1、会话 Cookie + CSRF + Host/Origin 白名单、202 Job、幂等键、队列上限、SIGTERM 干净停止"),
    "DATA-001": ("done", f"{WB}/panel/services/snapshot_service.py；frontend StatusBand", "报价日、最近完整交易日、采集时间、新鲜度分开；采集按服务端时钟先要今天，没有行情时按服务端给的最近完整交易日（ADR-003）"),
    "DATA-002": ("done", f"{WB}/panel/services/calendar_service.py；{WB}/panel/domain/calendar.py；docs/implementation/decisions/ADR-003-first-run-calendar-and-collect.md；tests/integration/test_first_run.py", "日历缺失明说不可判定；首次启动从数据源取日历（含未来区间），没返回的日子是未知而不是休市"),
    "DATA-003": ("done", f"{WB}/panel/services/journal_service.py（portfolio_view）", "无报价＝未知，不计入市值，不显示 0 盈亏"),
    "DATA-004": ("done", f"{WB}/panel/services/snapshot_build.py、snapshot_service.py；{WB}/panel/data/evidence_store.py", "内容寻址、不可变、修订另起 lineage"),
    "DATA-005": ("done", f"{WB}/panel/domain/universe.py", "候选池与环境分母两个政策分开显示"),
    "SEAT-001": ("done", f"{WB}/panel/domain/seat_registry.py", "run_state / applicability / verdict 正交"),
    "SEAT-002": ("done", "docs/implementation/chenkai-calibration.md", "书中小周期是小时线；4 日线≠小时线"),
    "SEAT-003": ("done", "skills/trading-chenkai-v1/；docs/implementation/chenkai-calibration.md", "三周期默认阻断；旧结果不重写"),
    "SEAT-004": ("done", "docs/implementation/seat-capabilities.{md,json}", "每席输入/输出/时间尺度/数据等级/不可判定项"),
    "EVAL-001": ("done", f"{WB}/panel/services/legacy_evaluation.py；frontend ValidationPage", "旧回测只作 legacy_unverified 资料"),
    "QA-001": ("done", "docs/implementation/p0-acceptance.md", ""),
    "HANDOFF-001": ("done", "docs/implementation/P0_HANDOFF.md", ""),
    "BASE-005": ("done", f"{WB}/frontend/src/{{App.tsx,legacy/,app/,lib/route.ts}}", "按用户决定：默认上一版外壳，环境概览/案例研究嵌在其中，新版七工作区在 #/lab 实验入口；390px 无横向溢出（ADR-004）"),
    "DATA-006": ("done", f"{WB}/frontend/src/pages/EnvironmentPage.tsx；{WB}/panel/domain/metrics_dictionary.py", "按维度分组的市场矩阵，带证据与时间尺度（在环境概览页，主界面与实验功能都有）"),
    "SEAT-005": ("done", f"{WB}/panel/services/{{seat_runner,case_orchestrator}}.py", "主方法 + 反证/风控/纪律，3–4 席"),
    "SEAT-006": ("done", f"{WB}/panel/adapters/llm_provider.py", "默认 disabled；模型输出过事实与政策校验"),
    "CASE-001": ("done", f"{WB}/panel/services/case_orchestrator.py", "supersedes 链，不覆盖旧 Case"),
    "CASE-002": ("done", f"{WB}/frontend/src/pages/CasesPage.tsx", "各席独立结论；汇总不计票"),
    "CASE-003": ("done", f"{WB}/frontend/src/pages/CasesPage.tsx（比较、Markdown 导出）", ""),
    "PLAN-001": ("done", f"{WB}/panel/services/plan_service.py", "唯一主方法；冻结后只追加版本"),
    "PLAN-002": ("done", f"{WB}/frontend/src/pages/PlansPage.tsx", "冻结对话框、版本比较、快照差异"),
    "PLAN-003": ("done", f"{WB}/panel/services/review_service.py", "事前审查不判未发生的交易违纪"),
    "JOURNAL-001": ("done", f"{WB}/panel/services/journal_service.py", "只追加事件 + 哈希链"),
    "JOURNAL-002": ("done", f"{WB}/panel/services/review_service.py", "事后复盘与纪律证据"),
    "JOURNAL-003": ("done", f"{WB}/frontend/src/pages/{{EnvironmentPage,PortfolioPage}}.tsx", "固定看盘窗口（前台、用户开始/结束）与自愿冷却"),
    "JOURNAL-004": ("done", f"{WB}/panel/services/journal_service.py", "服务端单一真源；费用服务端计算"),
    "JOURNAL-005": ("done", f"{WB}/panel/services/legacy_importer.py；PortfolioPage LegacyImport", "预览→确认导入为新组合；原字节哈希登记"),
    "JOURNAL-006": ("done", f"{WB}/panel/domain/{{fees,trading_rules}}.py", "费用与证券规则版本化，不宣称现行法定费率"),
    "JOURNAL-007": ("done", f"{WB}/frontend/src/pages/PortfolioPage.tsx", "模拟成交、心理自检、冷却、日记、行为记录"),
    "QA-002": ("done", "docs/implementation/p1-acceptance.md", "AC-029 规格冲突见 ADR-002"),
    "HANDOFF-002": ("done", f"docs/implementation/P1_HANDOFF.md；docs/USER_GUIDE.md；{WB}/panel/README.md；打开交易圆桌.command", "双击启动器；Python 3.10 / 3.14 实测"),
    "REPLAY-001": ("done", "docs/implementation/replay-data-eligibility.md", ""),
    "REPLAY-002": ("done", f"{WB}/panel/services/replay_service.py；{WB}/panel/web/{{security.py,handlers/session.py}}", "服务端截点绑定；漏 header 不退回真实盘"),
    "REPLAY-003": ("done", f"{WB}/frontend/src/pages/TrainingPage.tsx", "先记决定再推进；揭晓终态"),
    "EVAL-002": ("done", f"{WB}/panel/services/validation_service.py", "排队前冻结 manifest"),
    "EVAL-003": ("done", f"{WB}/panel/services/{{validation_service,stats}}.py；{WB}/panel/domain/fill_model.py", "HAC / 块 bootstrap；日线成交假设"),
    "EVAL-004": ("done", f"{WB}/panel/services/validation_service.py（prospective）", "冻结后首个可追溯报价起算"),
    "EVAL-005": ("done", f"{WB}/frontend/src/pages/ValidationPage.tsx", "三类口径分开；旧资料待复核"),
    "QA-003": ("done", "docs/implementation/p2-acceptance.md", ""),
    "HANDOFF-003": ("done", "docs/implementation/P2_HANDOFF.md；docs/TRAINING_AND_VALIDATION.md", ""),
    "CARD-001": ("done", f"{WB}/panel/services/deck_service.py；{WB}/frontend/src/pages/SeatsPage.tsx", "卡组由 Case/计划/复盘派生，版本化"),
    "CARD-002": ("done", f"{WB}/panel/services/deck_service.py（incentive_audit）；SeatsPage 学习记录", "无稀有度/抽卡/连胜；学习记录只在本地"),
    "QA-004": ("done", "docs/implementation/p3-acceptance.md", ""),
    "HANDOFF-004": ("done", "docs/implementation/P3_HANDOFF.md", ""),
    "ALERT-001": ("done", "docs/implementation/alerts-feasibility.md；alert_service.capability_gate", "结论：不可行，闸门关闭"),
    "ALERT-002": ("blocked", f"{WB}/panel/services/alert_service.py（AlertObservation 已实现并自检）", "受 ALERT-001 阻塞：无已核验分钟/逐笔权限，产品入口不开"),
    "ALERT-003": ("blocked", f"{WB}/panel/services/alert_service.py（list_events / acknowledge）", "同上；/api/v1/alerts* 返回 501"),
    "QA-005": ("done", "docs/implementation/p4-acceptance.md", "AC-074 PASS；AC-072/073 BLOCKED（前提不成立）"),
    "HANDOFF-005": ("done", "docs/implementation/FINAL_HANDOFF.md", ""),
    "RELEASE-001": ("done", "release/release_manifest.json（ready_for_user_acceptance）", "首发 P0–P3，P4 标未启用"),
    "RELEASE-002": ("blocked", "docs/implementation/FINAL_HANDOFF.md §部署适配", "本地部分完成（入口、探针、配置、持久目录）；生产入口/TLS/身份网关等部署目标确定后实施"),
    "RELEASE-003": ("done", "docs/implementation/evidence/backup-restore-drill-20261020.json", "本机演练通过并实测 RTO；目标环境复测属 R1"),
    "RELEASE-004": ("in_progress", "release/release_manifest.json；本地预览 http://127.0.0.1:8787/", "等待用户确认（confirmation=pending）"),
    "RELEASE-005": ("todo", "—", "用户确认并告知部署目标后执行；不是当前开发的发布授权"),
    "RELEASE-006": ("todo", "docs/implementation/FINAL_HANDOFF.md §回滚与运维", "回滚演练需要目标环境与前一有效版本"),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args(argv)
    rep = json.loads((WORK_ROOT / "artifacts/acceptance" / a.run_id / "report.json").read_text(encoding="utf-8"))
    status = {c["test_id"]: c["status"] for c in rep["cases"]}
    tmap = json.loads((WORK_ROOT / "acceptance/task_test_map.json").read_text(encoding="utf-8"))
    rel = defaultdict(list)
    for m in tmap:
        for t in {m["primary_task_id"], *[x for x in m["related_task_ids"].split(";") if x], m["independent_acceptance_task_id"]}:
            rel[t].append(m["test_id"])
    bl = json.loads((WORK_ROOT / "BACKLOG.json").read_text(encoding="utf-8"))
    tasks = bl["tasks"] if isinstance(bl, dict) else bl
    missing = [t["task_id"] for t in tasks if t["task_id"] not in STATUS]
    if missing:
        raise SystemExit(f"STATUS 缺少任务：{missing}")
    lines = ["# 逐任务执行记录（BACKLOG 59 项）", "",
             f"由 `tools/gen_task_status.py --run-id {a.run_id}` 生成。执行状态 todo / in_progress / blocked / done 按交付证据登记；"
             "验收列由验收报告按 `acceptance/task_test_map.json` 的映射计算（一个用例可能同时映射到多个相关任务）。", "",
             f"验收运行 `{a.run_id}`：代码指纹 `{rep['build']['code_hash'][:16]}…`，PASS {rep['summary']['passed']} / FAIL {rep['summary']['failed']} / "
             f"BLOCKED {rep['summary']['blocked']} / NOT_RUN {rep['summary']['not_run']}。", ""]
    exec_cnt = Counter()
    for phase in ("P0", "P1", "P2", "P3", "P4", "R1"):
        lines += [f"## {phase}", "", "| 任务 | 标题 | 执行 | 验收（映射用例） | 交付证据 | 说明 |", "|---|---|---|---|---|---|"]
        for t in [x for x in tasks if x["phase"] == phase]:
            st, ev, note = STATUS[t["task_id"]]
            exec_cnt[st] += 1
            cs = sorted(set(rel.get(t["task_id"], [])))
            cnt = Counter(status.get(c, "not_run") for c in cs)
            if not cs:
                acc = "—（交付物任务，无映射用例）"
            else:
                parts = [f"{k.upper()} {v}" for k, v in sorted(cnt.items(), key=lambda kv: ("pass", "fail", "blocked", "not_run").index(kv[0])
                                                                     if kv[0] in ("pass", "fail", "blocked", "not_run") else 9)]
                bad = [c for c in cs if status.get(c) in ("fail",)]
                acc = f"{' / '.join(parts)}（{len(cs)} 例）" + (f"；FAIL：{', '.join(bad)}" if bad else "")
            lines.append(f"| {t['task_id']} | {t['title']} | **{st}** | {acc} | {ev} | {note} |")
        lines.append("")
    lines += ["## 汇总", "", "| done | in_progress | blocked | todo |", "|---|---|---|---|",
              f"| {exec_cnt['done']} | {exec_cnt['in_progress']} | {exec_cnt['blocked']} | {exec_cnt['todo']} |", ""]
    (WORK_ROOT / "docs/implementation/task-status.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(dict(exec_cnt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
