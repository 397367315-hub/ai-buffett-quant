#!/usr/bin/env python3
"""从 panel/domain/seat_registry.py 生成席位能力表（SEAT-004）：
    docs/implementation/seat-capabilities.md   人读
    docs/implementation/seat-capabilities.json 机读（registry 指纹 + 每席能力边界）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK_ROOT / "records/roundtable/2026-10-18-圆桌面板设计"))

from panel.domain import seat_registry as sr   # noqa: E402

LEVEL_NAMES = {"a_level": "A 级（日线/日终可判）", "b_level": "B 级（需分钟线，待核）", "c_level": "C 级（需 Level2/逐笔，待核）"}


def main() -> int:
    reg = sr.registry_payload(WORK_ROOT / "skills")
    rows = []
    md = ["# 十三席能力边界表（SEAT-004）", "",
          f"注册表版本 `{reg['registry_version']}` · 指纹 `{reg['fingerprint'][:16]}…` · 由 `tools/gen_seat_capabilities.py` 生成，勿手改。",
          "", "人物名是方法标签，不代表作者本人参与或背书。来源存在 ≠ 实现忠实；实现可运行 ≠ 有收益优势。",
          "B/C 级条件一律「待核」，不用日线代理冒充。", "",
          "| # | seat_id | 名称 | role | 周期 | 能力 | 必需输入 | 规则版本 |", "|---|---|---|---|---|---|---|---|"]
    for s in reg["seats"]:
        cap = sr.CAPABILITIES[s["seat_id"]]
        md.append(f"| {s['registry_order']} | `{s['seat_id']}` | {s['name']} | {s['role']} | {'/'.join(s['horizons'])} | "
                  f"{', '.join(s['capabilities'])} | {', '.join(s['required_inputs'])} | `{s['rule_version']}` |")
        rows.append({**s, "capability": cap})
    md.append("")
    for s in reg["seats"]:
        cap = sr.CAPABILITIES[s["seat_id"]]
        md += [f"## {s['registry_order']:02d} {s['name']} · `{s['seat_id']}`", "",
               f"- **岗位**：{cap['job']}", f"- **脚本**：`skills/{s['seat_id']}/{cap['script']}`",
               f"- **输出**：{cap['outputs']}", f"- **来源**：{cap['source']}",
               f"- **产候选**：{'是' if sr.produces_candidates(s['seat_id']) else '否（不出独立候选）'}"]
        for k in ("a_level", "b_level", "c_level"):
            items = cap.get(k) or []
            md.append(f"- **{LEVEL_NAMES[k]}**：{'；'.join(items) if items else '无'}")
        md.append(f"- **不可判定 / 不采纳**：{'；'.join(cap['undecidable'])}")
        md.append(f"- **实现偏差（工程取值、代理）**：{'；'.join(cap['deviations'])}")
        md.append("")
    out = WORK_ROOT / "docs/implementation"
    (out / "seat-capabilities.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "seat-capabilities.json").write_text(json.dumps({"registry_version": reg["registry_version"],
                                                            "fingerprint": reg["fingerprint"], "seats": rows},
                                                           ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(rows)} seats → {out}/seat-capabilities.md/.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
