#!/usr/bin/env python3
"""由验收报告生成逐阶段验收文档 docs/implementation/p{0..4}-acceptance.md（以及 R1 清单）。

文档里的每个数字都来自 artifacts/acceptance/<run>/report.json，不手写结论；重跑验收后重新生成即可。
用法：cd WORK_ROOT && python3 tools/gen_acceptance_docs.py --run-id <run>
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parents[1]
DOCS = WORK_ROOT / "docs/implementation"
STATUS_ZH = {"pass": "PASS", "fail": "FAIL", "not_run": "NOT_RUN", "blocked": "BLOCKED", "not_implemented": "NOT_IMPLEMENTED"}
QA_TASK = {"P0": "QA-001", "P1": "QA-002", "P2": "QA-003", "P3": "QA-004", "P4": "QA-005", "R1": "RELEASE-001"}
STAGE_TITLE = {"P0": "P0 数据底座、来源与席位边界", "P1": "P1 日常闭环（Case → Plan → 组合/日记 → 复盘）",
               "P2": "P2 历史训练与方法验证", "P3": "P3 学习卡组", "P4": "P4 事件提醒（可行性闸门）",
               "R1": "R1 上线验收（本交付不部署）"}


def short(v, n=90) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args(argv)
    run_dir = WORK_ROOT / "artifacts/acceptance" / a.run_id
    rep = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    tmap = {m["test_id"]: m for m in json.loads((WORK_ROOT / "acceptance/task_test_map.json").read_text(encoding="utf-8"))}
    by_stage: dict[str, list] = {}
    for c in rep["cases"]:
        by_stage.setdefault(c["stage"], []).append(c)
    rel_run = run_dir.relative_to(WORK_ROOT)
    head = [
        f"- 验收运行：`{a.run_id}`（{rep['started_at']} → {rep['finished_at']}，UTC）",
        f"- 命令：`cd WORK_ROOT && python3 -m tests.acceptance.runner --run-id {a.run_id}`；"
        f"退出码 {0 if rep['summary']['failed'] == 0 else 1}（存在 FAIL 即非 0）",
        f"- 代码指纹 `{rep['build']['code_hash']}`（{rep['build']['code_files']} 个文件；工作区不是 git 仓库，用文件清单 SHA-256 代替 commit）",
        f"- 用例规格 `{rep['inputs']['spec_path']}` sha256 `{rep['inputs']['spec_hash'][:16]}…`；"
        f"operation_map `{rep['inputs']['operation_map_hash'][:16]}…`；fixture 清单 `{rep['inputs']['fixture_manifest_hash'][:16]}…`",
        f"- 环境：Python {rep['environment']['python_version']} · SQLite {rep['environment']['sqlite_version']} · "
        f"Node {rep['environment']['node_version']} · Playwright {rep['environment']['playwright_version']} · {rep['environment']['os']}",
        f"- 源包字节一致（运行前后逐文件 SHA-256）：{rep['source_integrity']['byte_equal']}（{rep['source_integrity']['file_count']} 个文件）",
        f"- 报告与证据：`{rel_run}/report.json`、`{rel_run}/junit.xml`、`{rel_run}/manifest.sha256`、`{rel_run}/cases/<用例>/`",
        "- 每个用例独立临时运行目录、冻结时钟 2026-10-19T07:10:00Z、固定随机种子、网络守卫（任何外连计入失败）；"
        "fixture 全部是合成数据（is_synthetic），不是真实行情。",
    ]
    for stage in ("P0", "P1", "P2", "P3", "P4", "R1"):
        cases = sorted(by_stage.get(stage, []), key=lambda c: c["test_id"])
        cnt = Counter(c["status"] for c in cases)
        lines = [f"# {stage} 验收（{QA_TASK[stage]}）· {STAGE_TITLE[stage]}", "",
                 "本文件由 `tools/gen_acceptance_docs.py` 从验收报告生成；状态只取实际运行结果，失败与未运行不改写成通过。", "",
                 *head, "",
                 "## 汇总", "",
                 "| PASS | FAIL | BLOCKED | NOT_RUN | NOT_IMPLEMENTED | 合计 |", "|---|---|---|---|---|---|",
                 f"| {cnt['pass']} | {cnt['fail']} | {cnt['blocked']} | {cnt['not_run']} | {cnt['not_implemented']} | {len(cases)} |", "",
                 "## 逐项", "",
                 "| 用例 | 标题 | 阻断级 | 主任务 | 状态 | 证据 / 说明 |", "|---|---|---|---|---|---|"]
        for c in cases:
            ev = "、".join(f"`{Path(e['path']).name}`" for e in c.get("evidence", [])[:4]) or "—"
            note = ""
            if c["status"] == "fail":
                bad = [x for x in c.get("assertions", []) if not x.get("ok")]
                note = "；".join(f"{x['target']} 期望 {short(x['expected'], 40)} 实际 {short(x['actual'], 40)}" for x in bad[:2])
            elif c["status"] in ("blocked", "not_run", "not_implemented"):
                note = short("；".join(c.get("notes") or []) or (c.get("error_code") or ""), 120)
            lines.append(f"| {c['test_id']} | {short(c['title'], 40)} | {c['blocker']} | {tmap.get(c['test_id'], {}).get('primary_task_id', '—')} | "
                         f"**{STATUS_ZH[c['status']]}** | {ev}{'；' + note if note else ''} |")
        special = [c for c in cases if c["status"] != "pass"]
        if special:
            lines += ["", "## 未通过项的处理", ""]
            for c in special:
                lines.append(f"### {c['test_id']} {c['title']} — {STATUS_ZH[c['status']]}")
                lines.append("")
                if c["status"] == "fail":
                    for x in c.get("assertions", []):
                        mark = "通过" if x.get("ok") else "**不通过**"
                        lines.append(f"- `{x['target']}` {x['op']} {short(x['expected'], 60)} → 实际 {short(x['actual'], 60)}：{mark}")
                    if c["test_id"] == "AC-029":
                        lines.append("- 原因：用例期望 201 与 `contracts/openapi.yaml`（freeze 只定义 200）冲突；实现按契约返回 200。"
                                     "处理见 `docs/implementation/decisions/ADR-002-plan-freeze-status-code.md`（待用户裁决）。")
                for n in c.get("notes") or []:
                    lines.append(f"- {n}")
                lines.append("")
        if stage == "P3":
            lines += ["", "## 不诱导交易审计（QA-004）", "",
                      "- 卡组写入前 `deck_service.incentive_audit` 拒绝稀有度、抽卡、连胜、充值、解锁加仓、签到、必涨、保底盈利、稳赚、"
                      "胜率王、最强席位、战力、段位等文案（422 INCENTIVE_TEXT_REJECTED）；单测 `tests/unit/test_domain_p1_p4.py::TestDeckIncentiveAudit`。",
                      "- 卡面文字全部来自已冻结的 Case / 计划版本 / 复盘结果，不新增结论、不生成仓位；学习记录只在浏览器本地，与盈亏和交易次数无关。",
                      "- 键盘与降动效：对话框 Esc 关闭并归还焦点、所有操作是原生 button；`prefers-reduced-motion` 时关闭过渡（styles.css）。"
                      "390px 宽度下无横向溢出（浏览器用例截图）。"]
        if stage == "P4":
            lines += ["", "## 闸门与引擎（QA-005）", "",
                      "- 闸门三条件：已核验数据权限（含核验记录）＋ 分钟/逐笔粒度 ＋ 用户显式启用；当前只有日级快照，闸门关闭，"
                      "`/api/v1/alerts*` 返回 501 FEATURE_DISABLED；重启不自动恢复任何观察会话；无后台线程、无订阅、无系统通知、无外发。",
                      "- AC-072 / AC-073 的前提（一个已启用的前台观察会话）在关闭的闸门下不能建立，记 BLOCKED 而不是 PASS；"
                      "引擎在「合成已核验能力」下的确定性自检写在各自 `result.json` 的 `engine_selftest`，"
                      "并有单测 `tests/unit/test_domain_p1_p4.py::TestAlertGateAndEngine`。可行性结论见 `alerts-feasibility.md`。"]
        if stage == "R1":
            lines += ["", "## 为什么不运行", "",
                      "R1 是上线验收：需要用户确认的发布候选、选定的部署目标与身份网关。本交付停在 READY_FOR_USER_ACCEPTANCE，"
                      "不创建托管项目、不改线上环境、不推送生产分支，所以这 6 项保持 NOT_RUN。部署 Agent 在用户确认后于预发/正式目标执行。"]
        name = f"p{stage[1]}-acceptance.md" if stage != "R1" else "r1-acceptance.md"
        (DOCS / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {name}: {dict(cnt)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
