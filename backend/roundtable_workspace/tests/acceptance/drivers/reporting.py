"""报告与部署类 driver。"""
from __future__ import annotations

import json

from ..errors import NotRun


def acceptance_report_generate(ctx, inp):
    """AC-083：不执行用例时生成的报告必须全是 not_run，actual=null，不预置 passed_at，且带规格/fixture/构建哈希。"""
    from ..runner import build_report, code_hash
    from ..spec import fixture_manifest_hash, load_spec
    spec = load_spec()
    if inp.get("tests_executed") is False:
        cases = [{"test_id": c["test_id"], "title": c["title"], "stage": c["stage"], "blocker": c["blocker"],
                  "status": "not_run", "actual": None, "expected": c["expect"], "error_code": None, "assertions": [],
                  "evidence": [], "steps": [], "notes": ["未执行"], "started_at": None, "finished_at": None}
                 for c in spec["cases"]]
    else:
        raise ValueError("tests_executed=true 的报告由 runner 主流程生成")
    rep = build_report(run_id=f"{ctx.run_id}-dry", cases=cases, started=None, dry=True, src_before={}, src_after={},
                       selected={"cases": [], "stages": []})
    ctx.case_dir.mkdir(parents=True, exist_ok=True)
    p = ctx.case_dir / "dry_report.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    ctx.add_evidence(p, "application/json", "未执行时生成的报告（全部 not_run）")
    ctx.result.update({"case_statuses": sorted({c["status"] for c in rep["cases"]}),
                       "actual_values": [None] if all(c["actual"] is None for c in rep["cases"]) else
                       sorted({json.dumps(c["actual"]) for c in rep["cases"]}),
                       "passed_at": rep.get("passed_at"),
                       "spec_fixture_build_hashes_present": bool(rep["inputs"]["spec_hash"] and rep["inputs"]["fixture_manifest_hash"]
                                                                 and rep["build"]["code_hash"]),
                       "claimed_pass_count": rep["summary"]["passed"], "case_count": len(rep["cases"]),
                       "spec_case_count": len(spec["cases"])})


def deployment_not_run(ctx, inp):
    raise NotRun("R1 上线用例：只在用户确认发布候选后到隔离预发/正式目标执行；本交付不部署")
