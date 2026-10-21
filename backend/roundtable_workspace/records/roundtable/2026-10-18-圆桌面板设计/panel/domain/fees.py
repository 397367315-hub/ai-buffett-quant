"""版本化费用模型（JOURNAL-006）。

费率只作为「软件兼容配置」或「合成测试样例」，不宣称是现行法定费率；
真实账户条件、券商佣金与适用日期需要用户另行核实。
每个分项在精确成交额上计算后 ROUND_HALF_UP 到分，再相加。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

from .money import minor_str


@dataclass(frozen=True)
class FeeSchedule:
    fee_model_version: str
    commission_ratio: Decimal
    commission_min_minor: int
    stamp_sell_ratio: Decimal
    transfer_ratio: Decimal
    rounding_policy: str
    effective_from: str | None
    effective_to: str | None
    source: str
    is_synthetic: bool
    current_market_rule_claim: bool = False

    def describe(self) -> dict:
        return {
            "fee_model_version": self.fee_model_version,
            "commission_ratio": str(self.commission_ratio),
            "commission_min_minor": str(self.commission_min_minor),
            "stamp_sell_ratio": str(self.stamp_sell_ratio),
            "transfer_ratio": str(self.transfer_ratio),
            "rounding_policy": self.rounding_policy,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "source": self.source,
            "is_synthetic": self.is_synthetic,
            "current_market_rule_claim": self.current_market_rule_claim,
        }


FEE_SCHEDULES: dict[str, FeeSchedule] = {
    # 旧前端 portfolio.ts 的 FEES 常量：只为兼容旧模拟盘的口径
    "legacy-paper-fee-v1": FeeSchedule(
        fee_model_version="legacy-paper-fee-v1",
        commission_ratio=Decimal("0.00025"), commission_min_minor=500,
        stamp_sell_ratio=Decimal("0.0005"), transfer_ratio=Decimal("0.00001"),
        rounding_policy="each_fee_half_up_to_fen",
        effective_from=None, effective_to=None,
        source="legacy frontend/src/portfolio.ts FEES（软件兼容默认，未核验现行法规与账户条件）",
        is_synthetic=False),
    # 验收用合成费率（03 §3 精度基准）
    "fee_fixture_v1": FeeSchedule(
        fee_model_version="fee_fixture_v1",
        commission_ratio=Decimal("0.00025"), commission_min_minor=500,
        stamp_sell_ratio=Decimal("0.0005"), transfer_ratio=Decimal("0.00001"),
        rounding_policy="each_fee_half_up_to_fen",
        effective_from=None, effective_to=None,
        source="acceptance fixture fee_fixture_v1（合成，不是现行规则）",
        is_synthetic=True),
}

DEFAULT_FEE_MODEL = "legacy-paper-fee-v1"


def _fen(yuan: Decimal) -> int:
    with localcontext() as ctx:
        ctx.prec = 60
        return int((yuan * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute_fees(side: str, gross_yuan: Decimal, fee_model_version: str | None = None) -> dict:
    """返回 FeeBreakdown（契约形状，*_minor 为字符串）。"""
    m = FEE_SCHEDULES[fee_model_version or DEFAULT_FEE_MODEL]
    if side not in ("buy", "sell"):
        raise ValueError("side 必须是 buy/sell")
    with localcontext() as ctx:
        ctx.prec = 60
        commission = max(_fen(gross_yuan * m.commission_ratio), m.commission_min_minor)
        stamp = _fen(gross_yuan * m.stamp_sell_ratio) if side == "sell" else 0
        transfer = _fen(gross_yuan * m.transfer_ratio)
    total = commission + stamp + transfer
    return {"commission_minor": minor_str(commission), "stamp_minor": minor_str(stamp),
            "transfer_minor": minor_str(transfer), "total_minor": minor_str(total),
            "fee_model_version": m.fee_model_version}


def fees_total_minor(fees: dict | None) -> int:
    return int(fees["total_minor"]) if fees else 0
