#!/usr/bin/env python3
"""按闸门过滤重算选股超额（修正 backtest_seats.py 的口径缺口）。

原脚本每天记录了 gate.open，但计算候选超额时把「闸门关着」那天的候选也算进去了——
而真实纪律是闸门关着不出手。这里不再重跑席位（避免与技能代码走样），
只按已保存的逐日记录重新聚合：只用 gate.open == True 的采样日。

输入：SOURCE_ROOT/records/.../data/results.json（只读，不改）
输出：本目录 gate_filtered.json
"""
from __future__ import annotations

import json
import math
from pathlib import Path

SRC = Path("/sessions/sharp-dreamy-pasteur/mnt/Desktop/交易圆桌_AI交接包_20261019/records/roundtable/2026-10-19-席位回测/data/results.json")
COST = 0.0006   # 双边合计，与原报告口径一致：只在候选腿上扣一次
OUT = Path(__file__).resolve().parent / "gate_filtered.json"


def stats(xs):
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean": None, "win": None, "t": None}
    m = sum(xs) / n
    if n < 2:
        return {"n": n, "mean": m, "win": None, "t": None}
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    t = m / (sd / math.sqrt(n)) if sd > 0 else None
    win = 100.0 * sum(1 for x in xs if x > 0) / n
    return {"n": n, "mean": m, "win": win, "t": t}


def main():
    data = json.loads(SRC.read_text())
    out = {"source": str(SRC), "cost": COST,
           "method": "按逐日 gate.open 过滤后重聚合；不重跑席位代码；候选腿扣双边 0.06%",
           "seats": {}}
    for key, rows in data.items():
        # 候选净超额 = (候选收益 − 双边成本) − 同日全市场等权收益
        all_ex = [(r["ret"] - COST - r["base"]) for r in rows if r.get("ret") is not None]
        open_rows = [r for r in rows if r.get("open") and r.get("ret") is not None]
        open_ex = [(r["ret"] - COST - r["base"]) for r in open_rows]
        out["seats"][key] = {
            "sample_days_total": len(rows),
            "gate_open_days": sum(1 for r in rows if r.get("open")),
            "unfiltered": stats(all_ex),
            "gate_filtered": stats(open_ex),
            "avg_candidates_open": (sum(r["n"] for r in open_rows) / len(open_rows)) if open_rows else None,
        }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    hdr = f"{'席位':<24}{'采样':>4}{'开闸':>4}{'未过滤超额':>11}{'t':>7}{'仅开闸超额':>12}{'t':>7}{'胜率':>7}"
    print(hdr)
    print("-" * len(hdr) * 1)
    for k, v in out["seats"].items():
        u, g = v["unfiltered"], v["gate_filtered"]
        f = lambda x: "—" if x is None else f"{x*100:+.2f}%"
        t = lambda x: "—" if x is None else f"{x:+.2f}"
        w = "—" if g["win"] is None else f"{g['win']:.0f}%"
        print(f"{k:<24}{v['sample_days_total']:>4}{v['gate_open_days']:>4}{f(u['mean']):>11}{t(u['t']):>7}{f(g['mean']):>12}{t(g['t']):>7}{w:>7}")
    print("\n→", OUT)


if __name__ == "__main__":
    main()
