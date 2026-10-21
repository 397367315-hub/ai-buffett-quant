"""统计口径（EVAL-003）：重叠前瞻收益的推断用预注册的 HAC 或移动块 bootstrap，不用「样本数除以 5」。

定义与验收 fixture serial_returns 的 golden oracle 完全一致（AC-084）：
  - HAC：均值回归（仅截距），Newey-West Bartlett 核，滞后 L，自协方差分母 n，无小样本修正，正态 z=1.959963984540054；
  - 块 bootstrap：非循环移动块，块长 b，重复 B 次；起点用 Python random.Random(seed).randrange(0, n-b+1)，
    每次取 ceil(n/b) 个块拼接后截断到 n；分位数按 (B-1)*q 线性插值。
这些只是给定公式的实现；HAC 滞后与块长是否适合某份数据，需要在 EvaluationManifest 里预注册并做敏感性说明。
"""
from __future__ import annotations

import math
import random
from decimal import Decimal

Z95 = 1.959963984540054


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def hac_se(xs: list[float], lag: int) -> float:
    n = len(xs)
    m = mean(xs)

    def gamma(k: int) -> float:
        return sum((xs[t] - m) * (xs[t - k] - m) for t in range(k, n)) / n

    s = gamma(0) + 2 * sum((1 - k / (lag + 1)) * gamma(k) for k in range(1, lag + 1))
    return math.sqrt(max(s, 0.0) / n)


def block_bootstrap_ci(xs: list[float], block: int, replicates: int, seed: int, alpha: float = 0.05) -> tuple[float, float]:
    n = len(xs)
    rng = random.Random(seed)
    per = math.ceil(n / block)
    means = []
    for _ in range(replicates):
        s: list[float] = []
        for _ in range(per):
            st = rng.randrange(0, n - block + 1)
            s.extend(xs[st:st + block])
        s = s[:n]
        means.append(sum(s) / n)
    means.sort()

    def q(p: float) -> float:
        pos = (replicates - 1) * p
        lo, hi = math.floor(pos), math.ceil(pos)
        return means[lo] + (means[hi] - means[lo]) * (pos - lo)
    return q(alpha / 2), q(1 - alpha / 2)


def fmt(x: float | None, places: int = 12) -> str | None:
    if x is None or math.isnan(x):
        return None
    d = Decimal(repr(x)).quantize(Decimal(1).scaleb(-places))
    s = format(d, "f")
    return "0" if Decimal(s) == 0 else s


def report(xs: list[float], *, lag: int = 10, block: int = 10, replicates: int = 2000, seed: int = 0) -> dict:
    """HAC 与块 bootstrap 两种推断 + 非重叠子样本敏感性。样本太少时返回 inconclusive。"""
    n = len(xs)
    if n < max(2 * block, lag + 2):
        return {"n": n, "status": "inconclusive", "reason": f"样本 {n} 太少，不足以做 HAC(lag={lag}) / 块长 {block} 的推断"}
    m = mean(xs)
    se = hac_se(xs, lag)
    lo, hi = block_bootstrap_ci(xs, block, replicates, seed)
    nonover = xs[::block]
    m2 = mean(nonover) if nonover else None
    return {"n": n, "status": "computed", "mean": fmt(m), "hac_lag": lag, "hac_se": fmt(se),
            "hac_normal_ci": [fmt(m - Z95 * se), fmt(m + Z95 * se)],
            "block_length": block, "replicates": replicates, "seed": seed, "block_percentile_ci": [fmt(lo), fmt(hi)],
            "nonoverlap_subsample": {"every": block, "n": len(nonover), "mean": fmt(m2)},
            "naive_n_div_5_used_as_inference": False,
            "note": "统计只评价该实现、该数据与该口径；样本外无可靠超额不能判定原书或作者方法无效"}
