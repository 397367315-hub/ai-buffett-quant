"""旧回测资料的只读登记（EVAL-001 / AC-069）：原字节不改，结论按披露的限制重读。

- 来源：只读源包 records/roundtable/2026-10-19-席位回测/（README 与 4 个脚本）+ 旧 dashboard.json 的 backtest 段
  + 本工作区的旁文件 legacy_evaluation_caveats.json（勘误与重读）；
- 每个原件登记 size + sha256；登记前后哈希一致才算「未改动」；
- 输出的是「旧资料」而不是 ValidationRun：状态固定 legacy_unverified；不出现 clean 绩效、不继承「只能当上界」；
- 达尔文（当前分红年数套历史）与李大霄（当前估值/分红口径）标 lookahead_contaminated。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import LEGACY_DATA_DIR, WORK_ROOT, get_settings

LEGACY_DIR_REL = "records/roundtable/2026-10-19-席位回测"
SOURCE_FILES = ["README.md", "backtest_seats.py", "gate_backtest.py", "backtest_value.py", "fetch_history.py"]
CAVEATS = WORK_ROOT / LEGACY_DIR_REL / "legacy_evaluation_caveats.json"
LOOKAHEAD_SEATS = {"达尔文": "trading-darwin-v1", "李大霄": "trading-lidaxiao-v1"}


def _hash(p: Path) -> dict | None:
    if not p.is_file():
        return None
    b = p.read_bytes()
    return {"path": str(p.relative_to(p.parents[3])) if len(p.parents) > 3 else p.name, "size": len(b),
            "sha256": hashlib.sha256(b).hexdigest()}


def material() -> dict:
    src = Path(get_settings().source_root) / LEGACY_DIR_REL
    artifacts = []
    for name in SOURCE_FILES:
        h = _hash(src / name)
        artifacts.append({"file": f"{LEGACY_DIR_REL}/{name}", "present": h is not None,
                          "size": h["size"] if h else None, "sha256": h["sha256"] if h else None})
    caveats = json.loads(CAVEATS.read_text(encoding="utf-8")) if CAVEATS.is_file() else None
    dash = LEGACY_DATA_DIR / "dashboard.json"
    bt = (json.loads(dash.read_text(encoding="utf-8")).get("backtest") or {}) if dash.is_file() else {}
    rows = []
    for r in bt.get("rows") or []:
        name = str(r.get("seat") or "")
        contaminated = any(k in name for k in LOOKAHEAD_SEATS)
        rows.append({"seat": name, "sampled_days": r.get("days"), "excess_pct_legacy": r.get("excess"),
                     "t_legacy": r.get("t"), "lookahead_contaminated": contaminated,
                     "clean_performance": False,
                     "display_rule": ("前视污染：不计 clean 绩效，受污染数字没有数学保证是上界" if contaminated
                                      else "候选排序代理：不是按纪律成交的策略收益")})
    sampled = 42 if "42 个采样日" in (bt.get("window") or "") else None
    return {"status": "legacy_unverified", "kind": "legacy_material_read_only",
            "sampled_days": sampled, "window": bt.get("window"), "status_note": bt.get("status_note"),
            "rows": rows, "gate_rows": bt.get("gate") or [],
            "flags": {"darwin_clean_performance": False, "lookahead_result_claimed_upper_bound": False,
                      "rule_proxy_disclosed": True, "unresolved_script_provenance_disclosed": True,
                      "naive_n_div_5_used_as_inference": False},
            "disclosures": [
                "这些数字来自 2026-10-19 的一次手工脚本运行，没有走 v1 的冻结登记与 ValidationRun；",
                "候选排序代理（日线规则、top30、等权）≠ 原书方法，也不是按纪律成交的策略；",
                "脚本执行的 provenance 未完全核实（README 同文自相矛盾）：按 per-seat 实际 artifact 显示，无运行证据的记无法核验；",
                "达尔文用当前连续分红年数套历史（前视污染）：不计 clean 绩效，受污染结果没有数学保证是上界；",
                "N/5 重叠修正只是启发式，新分析用预注册 HAC/块 bootstrap；",
                "42 个采样日既不证明方法无效，也不免除负证据：该版本、该样本、该口径未提供可靠正超额证据。"],
            "caveats": caveats, "source_artifacts": artifacts,
            "source_root_read_only": True}
