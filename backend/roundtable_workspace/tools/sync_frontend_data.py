#!/usr/bin/env python3
"""把前端需要的静态说明数据同步进 frontend（构建前运行；文件是生成物，勿手改）：

    frontend/src/data/seat-capabilities.json   席位能力边界（job / A/B/C 级 / 不可判定 / 偏差 / 资料来源），来自 seat_registry
    frontend/public/psychology_rules.v1.json   下单前心理自检规则集，来自 panel/domain/psychology_rules.v1.json

注册表的指纹、skill_sha256、rule_version 等运行时信息不在这里：前端运行时从 GET /api/v1/seats/registry 读。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WB = ROOT / "records/roundtable/2026-10-18-圆桌面板设计"
sys.path.insert(0, str(WB))

from panel.domain import seat_registry as sr   # noqa: E402

AVATAR = {"wildman": "wildman.webp", "jianmen": "jianmen.webp", "yangnan": "yangnan.webp", "yangjia": "chaogu.webp",
          "tuixue": "seat_05_tuixue.webp", "gushiguifei": "seat_06_yilubenxing.webp", "wyckoff": "seat_07_wyckoff.webp",
          "candle": "seat_08_candle.webp", "livermore": "seat_09_livermore.webp", "darwin": "seat_10_darwin.webp",
          "chenkai": "seat_11_chenkai.webp", "lidaxiao": "seat_12_lidaxiao.webp", "boardtech": "seat_13_boardtech.webp"}


def main() -> int:
    out = {}
    for s in sr.definitions(ROOT / "skills"):
        cap = sr.CAPABILITIES[s["seat_id"]]
        out[s["seat_id"]] = {"name": s["name"], "legacy_seat_id": s["legacy_seat_id"],
                             "avatar": f"/avatars/{AVATAR.get(s['legacy_seat_id'], '')}" if s["legacy_seat_id"] in AVATAR else None,
                             **{k: cap.get(k) for k in ("job", "script", "outputs", "a_level", "b_level", "c_level",
                                                        "undecidable", "deviations", "source")}}
    d = WB / "frontend/src/data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "seat-capabilities.json").write_text(json.dumps({"generated_by": "tools/sync_frontend_data.py", "seats": out},
                                                         ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    pub = WB / "frontend/public"
    shutil.copyfile(WB / "panel/domain/psychology_rules.v1.json", pub / "psychology_rules.v1.json")
    print(f"synced {len(out)} seats, psychology rules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
