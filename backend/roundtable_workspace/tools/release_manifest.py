#!/usr/bin/env python3
"""生成发布候选清单 release/release_manifest.json（RELEASE-001），状态停在 ready_for_user_acceptance。

- 代码指纹与最终验收报告、备份恢复演练证据三者必须一致：验收之后再改过代码就拒绝生成（先重跑验收与演练）；
- 按 contracts/release_manifest.schema.json 校验；
- 同时写 release/source_files.sha256（指纹的逐文件清单，便于用户核对确认的是哪一版）；
- 不部署、不填部署目标、不写确认记录：confirmation=pending，deployment_target 全部为空。
用法：cd WORK_ROOT && python3 tools/release_manifest.py --run-id <最终验收 run> --drill docs/implementation/evidence/<演练>.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK_ROOT))
sys.path.insert(0, str(WORK_ROOT / "records/roundtable/2026-10-18-圆桌面板设计"))

from tests.acceptance.runner import code_files, code_hash, sha256_file   # noqa: E402

RELEASE = WORK_ROOT / "release"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--drill", required=True)
    a = ap.parse_args(argv)
    run_dir = WORK_ROOT / "artifacts/acceptance" / a.run_id
    rep = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    drill_path = (WORK_ROOT / a.drill) if not Path(a.drill).is_absolute() else Path(a.drill)
    drill = json.loads(drill_path.read_text(encoding="utf-8"))
    h, n = code_hash()
    problems = []
    if rep["build"]["code_hash"] != h:
        problems.append(f"验收报告代码指纹 {rep['build']['code_hash'][:12]}… ≠ 当前 {h[:12]}…：验收之后改过代码，先重跑验收")
    if drill.get("code_hash") != h:
        problems.append(f"演练证据代码指纹 {str(drill.get('code_hash'))[:12]}… ≠ 当前 {h[:12]}…：先重跑 tools/backup_restore_drill.py")
    if drill.get("result") != "PASS":
        problems.append("备份恢复演练不是 PASS")
    if not rep["source_integrity"]["byte_equal"]:
        problems.append("验收前后源包字节不一致")

    import yaml
    info = yaml.safe_load((WORK_ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"))["info"]
    from panel.data.db import list_migrations
    migs = list_migrations()
    s = rep["summary"]
    fails = [c for c in rep["cases"] if c["status"] == "fail"]
    blocked = [c["test_id"] for c in rep["cases"] if c["status"] == "blocked"]
    not_run = [c["test_id"] for c in rep["cases"] if c["status"] == "not_run"]
    stage_ok = {st: v for st, v in rep["by_stage"].items()}
    known = [
        f"最终验收 {a.run_id}：PASS {s['passed']} / FAIL {s['failed']} / BLOCKED {s['blocked']} / NOT_RUN {s['not_run']} / "
        f"NOT_IMPLEMENTED {s['not_implemented']}（共 {s['total']}）。",
        *[f"{c['test_id']} FAIL：只有 http_status 一条断言不通过（期望 201，契约 openapi.yaml 只定义 200，实现按契约返回 200）；"
          "其余断言通过。见 docs/implementation/decisions/ADR-002-plan-freeze-status-code.md，待用户裁决 A（改用例为 200）或 B（改契约为 201）。"
          for c in fails if c["test_id"] == "AC-029"],
        *[f"{c['test_id']} FAIL：见验收报告。" for c in fails if c["test_id"] != "AC-029"],
        f"BLOCKED {', '.join(blocked)}：P4 能力闸门按设计关闭（无已核验分钟/逐笔权限），前提的前台观察会话不能建立；"
        "引擎自检结果在各自 result.json 的 engine_selftest。",
        f"NOT_RUN {', '.join(not_run)}：R1 上线验收，本交付不部署，用户确认发布候选并选定目标后执行。",
        "工作区不是 git 仓库：source_revision 为空，source_sha256 是代码文件清单（release/source_files.sha256）的 SHA-256。",
        "契约 1.0.0-draft 之上有一个只读扩展 GET /api/v1/workspace/index（ADR-001）；规格包原 openapi.yaml 未改，WORK_ROOT 副本已同步。",
        "数据：行情只在用户点击「采集」时取（provider_api 经本机 meoz CLI；开发环境没有用户的 API Key，未用真实数据源联网实测，"
        "首次启动与每日采集路径已用真实 NumcatAdapter + 假 meoz CLI 测过：tests/integration/test_first_run.py）；"
        "历史训练与方法验证只用只读日线归档（panel/data/daily，日线 OHLC，date_only 时点）。分钟线、Level2、小时线、公告、财务、"
        "指数成分与退市历史不在归档内，相关判据一律「不可判定 / 待核」，不用日线代理冒充。",
        "rc4 之后修复（ADR-003）：空库首次启动时「采集最新快照」因没有交易日历而死锁、日历只覆盖到今天导致每天第一次采集卡住、"
        "盘中采不到当天快照；新增双击启动器 打开交易圆桌.command。已在 Python 3.10.12 与 3.14.8（用户机器同版本）上验证。",
        "主界面按用户决定退回上一版（ADR-004）：默认是上一版五个页面（漏斗/席位/候选池/个人池/回测，读 refresh.py 生成的 dashboard.json），"
        "加新版的环境概览、案例研究两页（读 /api/v1 快照）；新版其余功能在侧栏底部「实验」入口（#/lab/...）。"
        "两套数据并存、数字可能不一致；上一版页面在 /api/dashboard 失败时会按上一版行为回退静态演示数据并在页面顶部明确标注"
        "（新版页面绝不回退）。个人池是浏览器本地账本；正式账本在实验功能的模拟仓与日记里。",
        "13 席中 B/C 级判据（分时承接、盘口、小时线等）永远 unknown 并列出 missing_inputs；陈凯三周期默认阻断（4 日线≠小时线）。",
        "模型解释层：仓内不内置任何模型供应商、不读密钥；llm_mode=required 返回 422，optional 降级为仅规则并写明。",
        "旧回测资料只作 legacy_unverified 资料展示；达尔文、李大霄标 lookahead_contaminated；不当作验证通过。",
        "历史归档是事后下载的数据：模型可能记得这段行情，训练成绩只是练习，不是前瞻能力证明。",
        "本机单用户：只监听 127.0.0.1；--allow-remote 之外不接受非回环地址。远程访问需要前置 HTTPS 身份网关（R1 部署阶段实施并验收）。",
        "备份与恢复：tools/runtime_backup.py（SQLite 在线一致性备份＋证据仓）；演练 RTO 实测见演练证据（演练库很小，真实库需按同一流程复测）。"
        "周期备份任务属于部署维护配置，开发阶段没有注册任何后台任务。",
        "没有前一个有效 v1 版本可回滚：rollback_evidence_ref 为空；数据回退用备份恢复；界面本身已经是上一版（ADR-004），"
        "单文件快照 frontend/standalone.html 仍可只读打开（训练上下文中 legacy 路由返回 409）。",
        "主界面按用户决定是上一版（ADR-004）：上一版源码的 live 副本在 frontend/src/legacy/，原始备份在 frontend/legacy_src_20261019/"
        "（只读参考）；panel/tests/ui_smoke.js 针对旧界面，不属于本版验收。",
    ]
    from datetime import datetime, timedelta, timezone
    day = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")     # 上海日期
    manifest = {
        "release_id": f"rc-{day}-{h[:12]}",
        "state": "ready_for_user_acceptance",
        "source_revision": None,
        "source_sha256": h,
        "contract_version": f"{info['version']}+ADR-001",
        "database_migration_version": f"{migs[-1][0]:03d} ({', '.join(f'{v:03d}_{nm}' for v, nm, _ in migs)})",
        "enabled_phases": ["P0", "P1", "P2", "P3"],
        "disabled_features": [
            "P4 事件提醒：能力闸门关闭，/api/v1/alerts* 返回 501 FEATURE_DISABLED（需要已核验分钟/逐笔权限＋用户显式启用）",
            "券商连接与真实下单：不在范围，系统只记录模拟成交与用户自报记录",
            "后台自动刷新、行情订阅、系统通知、对外消息、开机自启：均未实现且默认不注册",
            "模型解释层：未配置任何模型供应商（llm_mode=disabled 路径可用）",
            "远程访问与多用户：未实现身份网关，服务只监听本机回环",
            "分钟/逐笔/Level2/小时线判据：数据权限不足，界面与 API 标「不可判定」",
        ],
        "acceptance_report_refs": [
            f"artifacts/acceptance/{a.run_id}/report.json", f"artifacts/acceptance/{a.run_id}/junit.xml",
            f"artifacts/acceptance/{a.run_id}/manifest.sha256",
            "docs/implementation/p0-acceptance.md", "docs/implementation/p1-acceptance.md",
            "docs/implementation/p2-acceptance.md", "docs/implementation/p3-acceptance.md",
            "docs/implementation/p4-acceptance.md", "docs/implementation/r1-acceptance.md",
        ],
        "preview_url": "http://127.0.0.1:8787/",
        "confirmation": {"state": "pending", "release_id": None, "confirmed_at": None, "reference": None},
        "deployment_target": {"provider": None, "project_id": None, "production_url": None, "private_backend": True,
                              "persistent_storage": None, "authentication": None},
        "backup_restore_evidence_ref": str(drill_path.relative_to(WORK_ROOT)),
        "rollback_evidence_ref": None,
        "known_limitations": known,
    }
    schema = json.loads((WORK_ROOT / "contracts/release_manifest.schema.json").read_text(encoding="utf-8"))
    import jsonschema
    # 本机 jsonschema 3.x 没有 2020-12 草案类；该 schema 只用 type/enum/pattern/required/additionalProperties/items，
    # 这些关键字在 Draft 7 中语义相同
    validator_cls = getattr(jsonschema, "Draft202012Validator", jsonschema.Draft7Validator)
    errors = sorted(validator_cls(schema).iter_errors(manifest), key=lambda e: list(e.path))
    if errors:
        problems += [f"schema：{'/'.join(map(str, e.path)) or '<root>'} {e.message}" for e in errors]
    if problems:
        print("拒绝生成发布清单：\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    RELEASE.mkdir(exist_ok=True)
    (RELEASE / "release_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"{sha256_file(p)}  {p.relative_to(WORK_ROOT)}" for p in code_files()]
    (RELEASE / "source_files.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"release_id": manifest["release_id"], "state": manifest["state"], "source_sha256": h,
                      "code_files": n, "stages": stage_ok}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
