#!/usr/bin/env python3
"""验收 runner：把 acceptance/acceptance_cases.json 绑定到真实实现并执行（03 §2 / §8）。

    cd WORK_ROOT
    python3 -m tests.acceptance.runner                 # 全部 110 例
    python3 -m tests.acceptance.runner --case AC-047   # 单例（可重复）
    python3 -m tests.acceptance.runner --stage P1
    python3 -m tests.acceptance.runner --dry-report    # 不执行：生成全部 not_run 的报告（AC-083 用）

- 每例：独立临时 runtime + 冻结 UTC 时钟（默认 2026-10-19T07:10:00Z）+ 固定随机种子 + 网络守卫；
- steps[].operation_id 只按 tests/acceptance/operation_map.json 显式绑定（不模糊匹配端点）；
- 断言只读 driver 从真实 HTTP/DB/字节/浏览器投影出的 result；expect 不会被当成 actual；
- 状态：pass / fail / not_run / not_implemented / blocked；skipped 不存在，也不计入 pass；
- 产物：artifacts/acceptance/<run_id>/{report.json, junit.xml, manifest.sha256, source_hash_before.json,
  source_hash_after.json, cases/<test_id>/{http.ndjson, db_assertions.json, result.json, screenshots/…}}。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tests.support.harness  # noqa: E402,F401  （设置 sys.path：panel 包）
from tests.acceptance.spec import (WORK_ROOT, OPMAP_PATH, SPEC_PATH, fixture_manifest_hash, load_opmap,  # noqa: E402
                                   load_spec, resolve_refs, sha256_file)
from tests.acceptance import asserts                                                                    # noqa: E402
from tests.acceptance.errors import Blocked, DriverNotImplemented, NotRun                               # noqa: E402

ARTIFACTS = WORK_ROOT / "artifacts" / "acceptance"
# 发布候选的代码指纹：验收报告与 release_manifest 用同一份清单（tools/release_manifest.py 复算并比对）。
# 不含：运行数据（runtime/、panel/data/*.json 旧缓存）、artifacts/、docs/、release/、node_modules、__pycache__。
CODE_GLOBS = ["records/roundtable/2026-10-18-圆桌面板设计/panel/**/*.py",
              "records/roundtable/2026-10-18-圆桌面板设计/panel/data/migrations/*.sql",
              "records/roundtable/2026-10-18-圆桌面板设计/panel/domain/*.json",
              "records/roundtable/2026-10-18-圆桌面板设计/panel/web/operations.json",
              "../../integrations/trading-roundtable/frontend/src/**/*",
              "../../integrations/trading-roundtable/frontend/public/**/*",
              "../../integrations/trading-roundtable/frontend/dist/**/*",
              "../../integrations/trading-roundtable/frontend/index.html",
              "../../integrations/trading-roundtable/frontend/package.json",
              "../../integrations/trading-roundtable/frontend/package-lock.json",
              "../../integrations/trading-roundtable/frontend/vite.config.ts",
              "../../integrations/trading-roundtable/frontend/tsconfig.json",
              "skills/**/*",
              "打开交易圆桌.command",
              "contracts/*", "tests/**/*.py", "tests/acceptance/operation_map.json", "tools/*.py"]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def code_files() -> list[Path]:
    out = set()
    for g in CODE_GLOBS:
        for p in WORK_ROOT.glob(g):
            if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith("."):
                out.add(p)
    return sorted(out)


def code_hash() -> tuple[str, int]:
    items = [f"{p.relative_to(WORK_ROOT)}\t{sha256_file(p)}" for p in code_files()]
    h = hashlib.sha256("\n".join(sorted(items)).encode("utf-8")).hexdigest()
    return h, len(items)


def source_root() -> Path:
    from panel.config import get_settings
    return Path(get_settings().source_root)


def byte_manifest(root: Path) -> dict:
    out = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            data = p.read_bytes()
            out[str(p.relative_to(root))] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return out


def environment() -> dict:
    node = None
    try:
        node = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:
        pass
    pw = None
    try:
        import playwright  # noqa: F401
        from importlib.metadata import version
        pw = version("playwright")
    except Exception:
        pass
    return {"python_version": platform.python_version(), "node_version": node, "sqlite_version": sqlite3.sqlite_version,
            "os": f"{platform.system()} {platform.release()} {platform.machine()}", "playwright_version": pw}


def load_driver(binding: dict):
    ref = binding.get("driver")
    if not ref:
        raise DriverNotImplemented(binding["scenario_operation_id"])
    mod_path, _, fn = ref.partition("::")
    mod = mod_path.replace("/", ".").removesuffix(".py")
    m = importlib.import_module(mod)
    f = getattr(m, fn, None)
    if f is None:
        raise DriverNotImplemented(ref)
    return f


def run_case(case: dict, spec: dict, opmap: dict, run_dir: Path, run_id: str) -> dict:
    from tests.acceptance.context import CaseContext
    tid = case["test_id"]
    case_dir = run_dir / "cases" / tid
    rec = {"test_id": tid, "title": case["title"], "stage": case["stage"], "blocker": case["blocker"],
           "status": "not_run", "actual": None, "expected": case["expect"], "error_code": None, "assertions": [],
           "evidence": [], "steps": [], "notes": [], "started_at": utcnow(), "finished_at": None}
    bindings = {o["scenario_operation_id"]: o for o in opmap["operations"]}
    missing = [s["operation_id"] for s in case["steps"] if s["operation_id"] not in bindings]
    if missing:
        rec.update(status="not_implemented", error_code="OPERATION_UNBOUND", notes=[f"未绑定：{missing}"])
        return rec
    if case["stage"] == "R1":
        rec.update(status="not_run", error_code="R1_DEPLOYMENT_STAGE",
                   notes=["R1 上线用例只在用户确认发布候选后到隔离预发/正式目标执行（03 契约定稿补充、05 §1）；"
                          "本次交付停在 READY_FOR_USER_ACCEPTANCE，没有任何部署动作"])
        rec["finished_at"] = utcnow()
        return rec
    ctx = CaseContext(case, spec, case_dir, run_id)
    setup = (opmap.get("case_setup") or {}).get(tid) or {}
    if setup.get("clock"):
        from panel.domain import clock as _clock
        _clock.set_fixed(setup["clock"])
        rec["notes"].append(f"case_setup.clock={setup['clock']}：{setup.get('reason', '')}")
    for fid in case["fixture_ids"]:
        if fid == "clock_monday" and not setup.get("clock"):
            ctx.ensure(fid)
    status, code = None, None
    try:
        for i, step in enumerate(case["steps"]):
            b = bindings[step["operation_id"]]
            inp = resolve_refs(step.get("input") or {}, spec["fixtures"], ctx.result)
            t0 = time.time()
            fn = load_driver(b)
            fn(ctx, inp)
            rec["steps"].append({"index": i, "operation_id": step["operation_id"], "binding_kind": b["binding_kind"],
                                 "openapi_operation_id": b.get("openapi_operation_id"), "driver": b["driver"],
                                 "seconds": round(time.time() - t0, 3)})
    except Blocked as ex:
        status, code = "blocked", ex.code
        rec["notes"].append(ex.reason)
    except NotRun as ex:
        status, code = "not_run", "NOT_RUN"
        rec["notes"].append(ex.reason)
    except DriverNotImplemented as ex:
        status, code = "not_implemented", "DRIVER_NOT_IMPLEMENTED"
        rec["notes"].append(f"driver 未实现：{ex}")
    except Exception as ex:
        status, code = "fail", "DRIVER_ERROR"
        tb = traceback.format_exc()
        rec["notes"].append(f"{type(ex).__name__}: {ex}")
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "driver_error.txt").write_text(tb, encoding="utf-8")
    try:
        rec["notes"] += ctx.notes
        rec["actual"] = ctx.result
        if status is None:
            results = []
            for e in case["expect"]:
                try:
                    v = resolve_refs(e.get("value"), spec["fixtures"], ctx.result)
                except KeyError as ex:
                    results.append({"target": e["target"], "op": e["op"], "expected": e.get("value"), "actual": None,
                                    "ok": False, "detail": f"reference {ex} missing"})
                    continue
                results.append(asserts.evaluate(e, ctx.result, v))
            rec["assertions"] = results
            status = "pass" if results and all(r["ok"] for r in results) else "fail"
            if status == "fail":
                code = "ASSERTION_FAILED"
        ctx.write_evidence()
        if (case_dir / "driver_error.txt").exists():
            ctx.add_evidence(case_dir / "driver_error.txt", "text/plain", "driver 异常栈")
        rec["evidence"] = ctx.evidence
    finally:
        ctx.close()
    rec.update(status=status, error_code=code, finished_at=utcnow())
    return rec


def junit(cases: list[dict]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<testsuite name="roundtable-acceptance" tests="{len(cases)}" '
           f'failures="{sum(c["status"] == "fail" for c in cases)}" '
           f'skipped="{sum(c["status"] in ("not_run", "blocked", "not_implemented") for c in cases)}">']
    for c in cases:
        out.append(f'  <testcase classname="{c["stage"]}.{c["blocker"]}" name="{escape(c["test_id"] + " " + c["title"])}">')
        if c["status"] == "fail":
            msg = "; ".join(f'{a["target"]} {a["op"]} {a["expected"]!r} actual={a["actual"]!r}'
                            for a in c["assertions"] if not a["ok"]) or "; ".join(c["notes"])
            out.append(f'    <failure message="{escape(c["error_code"] or "fail")}">{escape(msg[:4000])}</failure>')
        elif c["status"] != "pass":
            out.append(f'    <skipped message="{escape(c["status"])}: {escape("; ".join(c["notes"])[:500])}"/>')
        out.append("  </testcase>")
    out.append("</testsuite>")
    return "\n".join(out)


def build_report(*, run_id: str, cases: list[dict], started, dry: bool, src_before: dict, src_after: dict,
                 selected: dict) -> dict:
    spec = load_spec()
    chash, nfiles = code_hash()
    summary = {k: sum(1 for c in cases if c["status"] == s) for k, s in
               (("passed", "pass"), ("failed", "fail"), ("not_run", "not_run"), ("blocked", "blocked"),
                ("not_implemented", "not_implemented"))}
    summary["total"] = len(cases)
    by_stage: dict = {}
    for c in cases:
        st = by_stage.setdefault(c["stage"], {"pass": 0, "fail": 0, "not_run": 0, "blocked": 0, "not_implemented": 0})
        st[c["status"]] += 1
    executed = (not dry) and any(c["status"] != "not_run" for c in cases)
    lim = []
    if any(c["status"] == "blocked" for c in cases):
        lim.append("blocked 用例按设计前置条件不满足（见各例 notes），不计为通过")
    if any(c["stage"] == "R1" for c in cases):
        lim.append("R1 上线用例 not_run：本次交付不部署，用户确认发布候选后在隔离预发/正式目标执行")
    if dry:
        lim.append("dry_report：未执行任何用例")
    return {
        "schema_version": "1.0.0", "run_id": run_id, "execution_status": "executed" if executed else "not_run",
        "started_at": started, "finished_at": utcnow() if executed else None, "passed_at": None,
        "build": {"commit": None, "code_hash": chash, "code_files": nfiles, "dirty": None,
                  "note": "工作区不是 git 仓库：用代码文件清单 sha256 代替 commit"},
        "environment": environment(),
        "inputs": {"spec_path": str(SPEC_PATH.relative_to(WORK_ROOT)), "spec_hash": sha256_file(SPEC_PATH),
                   "operation_map_hash": sha256_file(OPMAP_PATH), "fixture_manifest_hash": fixture_manifest_hash(spec),
                   "selected": selected},
        "source_integrity": {"source_root": str(source_root()), "file_count": len(src_before),
                             "before_manifest": "source_hash_before.json" if src_before else None,
                             "after_manifest": "source_hash_after.json" if src_after else None,
                             "byte_equal": (src_before == src_after and bool(src_before)) if src_before else None},
        "summary": summary, "by_stage": by_stage, "cases": cases,
        "acceptance_complete": executed and not selected.get("cases") and not selected.get("stages")
        and all(c["status"] == "pass" for c in cases if c["stage"] != "R1"),
        "known_limitations": lim,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", default=[])
    ap.add_argument("--stage", action="append", default=[])
    ap.add_argument("--run-id")
    ap.add_argument("--dry-report", action="store_true", help="不执行任何用例，只生成 not_run 报告")
    a = ap.parse_args(argv)
    spec = load_spec()
    opmap = load_opmap()
    aliases = {s["operation_id"] for c in spec["cases"] for s in c["steps"]}
    bound = {o["scenario_operation_id"] for o in opmap["operations"]}
    if aliases - bound:
        print(f"operation_map 缺少绑定：{sorted(aliases - bound)}", file=sys.stderr)
        return 2
    run_id = a.run_id or datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    run_dir = ARTIFACTS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("ROUNDTABLE_QUIET", "1")
    started = utcnow()
    chash, nfiles = code_hash()
    src = source_root()
    before = byte_manifest(src)
    (run_dir / "source_hash_before.json").write_text(json.dumps(before, ensure_ascii=False, indent=0), encoding="utf-8")
    from tests.acceptance.context import install_network_guard
    install_network_guard()
    cases = []
    for c in spec["cases"]:
        if a.case and c["test_id"] not in a.case:
            continue
        if a.stage and c["stage"] not in a.stage:
            continue
        if a.dry_report:
            cases.append({"test_id": c["test_id"], "title": c["title"], "stage": c["stage"], "blocker": c["blocker"],
                          "status": "not_run", "actual": None, "expected": c["expect"], "error_code": None,
                          "assertions": [], "evidence": [], "steps": [], "notes": ["dry_report：未执行"],
                          "started_at": None, "finished_at": None})
            continue
        t0 = time.time()
        rec = run_case(c, spec, opmap, run_dir, run_id)
        cases.append(rec)
        print(f"{rec['test_id']:7} {rec['status']:15} {time.time() - t0:6.1f}s  {rec['title'][:40]}"
              + (f"  [{rec['error_code']}]" if rec["status"] not in ("pass",) else ""), flush=True)
    after = byte_manifest(src)
    (run_dir / "source_hash_after.json").write_text(json.dumps(after, ensure_ascii=False, indent=0), encoding="utf-8")
    report = build_report(run_id=run_id, cases=cases, started=started, dry=a.dry_report, src_before=before,
                          src_after=after, selected={"cases": a.case, "stages": a.stage})
    summary, by_stage = report["summary"], report["by_stage"]
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (run_dir / "junit.xml").write_text(junit(cases), encoding="utf-8")
    lines = []
    for p in sorted(run_dir.rglob("*")):
        if p.is_file() and p.name != "manifest.sha256":
            lines.append(f"{sha256_file(p)}  {p.relative_to(run_dir)}")
    (run_dir / "manifest.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": run_id, "summary": summary, "by_stage": by_stage,
                      "source_byte_equal": report["source_integrity"]["byte_equal"]}, ensure_ascii=False))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
