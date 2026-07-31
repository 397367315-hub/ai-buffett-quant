"""Deterministic research guardrails for stock selection and quant studies.

The protocol is deliberately independent from the scoring code. It makes the
assumptions visible, checks when data could have been known, estimates trading
friction, and records reasons a result should not be promoted to a trade.
"""

from __future__ import annotations

import math
from typing import Any


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


class ResearchProtocol:
    """The fixed experiment and execution assumptions used by the system."""

    LOOKBACK_DAYS = 60
    HOLDING_DAYS = 5
    REFERENCE_CAPITAL = 400_000.0
    COMMISSION_RATE = 0.00025
    STAMP_TAX_RATE = 0.001
    SLIPPAGE_RATE = 0.0005

    @classmethod
    def hypothesis_card(
        cls,
        stock: dict,
        *,
        data_date: str | None,
        is_realtime: bool,
        source: str,
        data_quality: str,
    ) -> dict:
        """Return a falsifiable hypothesis instead of a directional promise."""
        name = stock.get("name") or stock.get("code") or "候选标的"
        timing = "盘中快照" if is_realtime else "最近交易日缓存"
        return {
            "observation": f"{name}进入{timing}候选池，价格、成交活跃度和资金方向至少有一项形成可研究信号。",
            "mechanism": "趋势延续、资金参与和流动性可能在短持有期内共同解释相对收益，但行业贝塔、市场状态和估值暴露也可能是替代解释。",
            "signal_definition": {
                "period": f"{cls.LOOKBACK_DAYS}个交易日回看窗口",
                "formula": "综合分只使用信号时点已存在的价格、成交、资金、估值和风险字段；历史验证在T日收盘计算，T+1日开盘成交。",
                "edge_cases": [
                    "关键字段缺失时不填充为零，不计入对应因子。",
                    "不足60条日线时降低数据等级并限制研究仓位。",
                    "停牌、无效价格和无法确认成交的记录不进入可交易样本。",
                ],
            },
            "data_timing": {
                "data_date": data_date,
                "data_source": source,
                "data_source_time": "实时行情快照" if is_realtime else "交易日收盘后的本地缓存",
                "signal_available_time": "当前请求完成时" if is_realtime else "T日收盘后",
                "earliest_trade_time": "下一交易时段开盘（T+1）",
                "timeline_rule": "禁止用T日收盘价计算信号后按T日收盘价成交",
            },
            "prediction_target": {
                "holding_period": f"T+1开盘买入，持有{cls.HOLDING_DAYS}个交易日",
                "return_calc": "净收益 = T+1开盘至持有期结束收盘的价格收益 - 双边佣金 - 卖出印花税 - 滑点 - 估计冲击成本",
            },
            "baselines": [
                "等权市场候选池",
                "行业中性随机组合",
                "简单20日绝对动量",
                "同流动性分位组合",
            ],
            "confounders": ["市场贝塔", "行业暴露", "市值", "波动率", "流动性", "数据发布日期"],
            "failure_criteria": [
                "样本外收益不高于等权基线且扣成本后消失",
                "分组收益不单调或IC接近零",
                "收益只集中在单一年份、行业或少数股票",
                "只有一个极端参数有效",
                "去掉最小市值或最大收益样本后结果显著恶化",
            ],
            "data_quality": data_quality,
        }

    @classmethod
    def data_quality(
        cls,
        stock: dict,
        history: list[dict],
        *,
        source: str,
        news_available: bool,
    ) -> dict:
        """Grade evidence completeness without inventing missing fields."""
        evidence: list[str] = []
        missing: list[str] = []
        score = 0.0

        if _number(stock.get("price")) and _number(stock.get("price")) > 0:
            score += 20
            evidence.append("价格有效")
        else:
            missing.append("有效价格")

        if len(history) >= cls.LOOKBACK_DAYS:
            score += 25
            evidence.append(f"日线样本{len(history)}条")
        elif len(history) >= 20:
            score += 12
            missing.append(f"完整{cls.LOOKBACK_DAYS}日日线（当前{len(history)}条）")
        else:
            missing.append(f"完整{cls.LOOKBACK_DAYS}日日线（当前{len(history)}条）")

        flow = _number(stock.get("main_net_inflow"))
        if flow is not None:
            score += 15
            evidence.append("主力资金字段有效")
        else:
            missing.append("主力资金流")

        fundamental_count = sum(_number(stock.get(key)) is not None for key in ("pe", "pb", "roe"))
        if fundamental_count >= 2:
            score += 15
            evidence.append("估值/盈利字段达到可研究数量")
        elif fundamental_count == 1:
            score += 7
            missing.append("完整估值与盈利字段")
        else:
            missing.append("估值与盈利字段")

        if str(stock.get("sector") or "").strip():
            score += 10
            evidence.append("行业标签有效")
        else:
            missing.append("行业标签")

        if news_available:
            score += 10
            evidence.append("有可核验新闻/公告来源")
        else:
            missing.append("新闻/公告来源")

        if source == "eastmoney":
            evidence.append("行情源可标注更新时间")
        else:
            missing.append("可验证的行情时间戳")

        if score >= 75:
            grade, multiplier = "充分", 1.0
        elif score >= 50:
            grade, multiplier = "一般", 0.6
        else:
            grade, multiplier = "不足", 0.3

        return {
            "grade": grade,
            "score": round(score, 1),
            "position_multiplier": multiplier,
            "evidence": evidence,
            "missing": missing,
            "assessment": (
                "证据足以进行研究，但仍需样本外验证。" if grade == "充分" else
                "可以观察，关键缺口会降低仓位和结论置信度。" if grade == "一般" else
                "关键数据缺失，本轮只能作为观察线索，不能当作交易依据。"
            ),
        }

    @classmethod
    def time_audit(
        cls,
        stock: dict,
        history: list[dict],
        *,
        data_date: str | None,
        updated_at: str,
        source: str,
        is_realtime: bool,
    ) -> dict:
        """Audit event, availability, calculation and earliest-trade times."""
        fields = [
            {
                "field_name": "行情与成交字段",
                "event_time": data_date or "未提供",
                "data_available_time": updated_at if is_realtime else "T日收盘后",
                "signal_calc_time": updated_at if is_realtime else "T日收盘后",
                "earliest_trade_time": "下一交易时段开盘（T+1）",
                "timeline_valid": bool(data_date or not is_realtime),
                "leakage_risk": "低" if (data_date or not is_realtime) else "中",
                "notes": "历史信号不得按同日收盘价成交。",
            },
            {
                "field_name": "日线技术指标",
                "event_time": history[-1].get("date") if history else "未提供",
                "data_available_time": "T日收盘后" if history else "未提供",
                "signal_calc_time": "T日收盘后" if history else "未提供",
                "earliest_trade_time": "下一交易时段开盘（T+1）",
                "timeline_valid": bool(history),
                "leakage_risk": "低" if history else "高",
                "notes": f"使用过去数据点{len(history)}条，不向前填充缺失日线。",
            },
            {
                "field_name": "基本面字段",
                "event_time": "报告期未提供",
                "data_available_time": "披露日期未随行情返回",
                "signal_calc_time": updated_at if is_realtime else "T日收盘后",
                "earliest_trade_time": "下一交易时段开盘（T+1）",
                "timeline_valid": False,
                "leakage_risk": "中",
                "notes": "基本面字段可用于当前研究快照，但不能直接用于历史回测。",
            },
        ]
        red_flags = [
            f"{field['field_name']}存在未来信息风险"
            for field in fields
            if field["leakage_risk"] in ("高", "红灯")
        ]
        warnings = [
            f"{field['field_name']}：{field['notes']}"
            for field in fields
            if field["leakage_risk"] == "中"
        ]
        if source != "eastmoney":
            warnings.append("行情源未提供可验证的精确时间戳，不能标记为严格实时。")
        overall = "存在未来信息泄漏" if red_flags else "有问题需修复" if warnings else "通过"
        return {
            "fields": fields,
            "overall_assessment": overall,
            "red_flags": red_flags,
            "warnings": warnings,
            "data_quality": "不足" if red_flags else "一般" if warnings else "充分",
        }

    @classmethod
    def execution_plan(
        cls,
        stock: dict,
        risk_plan: dict,
        quality: dict,
    ) -> dict:
        """Estimate round-trip friction and apply an evidence-based cap."""
        price = _number(stock.get("price")) or 0.0
        amount = _number(stock.get("amount"))
        base_cap = _number(risk_plan.get("max_research_position_pct")) or 0.0
        position_cap = base_cap * float(quality.get("position_multiplier", 0.3))
        planned_capital = cls.REFERENCE_CAPITAL * position_cap / 100
        if amount and amount > 0:
            impact = _clamp(0.0002 + planned_capital / amount * 0.25, 0.0002, 0.02)
            capacity = "可用成交额字段估算"
        else:
            impact = 0.015
            capacity = "缺少当日成交额，只能采用保守冲击成本"
        round_trip = cls.COMMISSION_RATE * 2 + cls.STAMP_TAX_RATE + cls.SLIPPAGE_RATE * 2 + impact
        stop_price = _number(risk_plan.get("stop_loss_price"))
        target_price = _number(risk_plan.get("reference_target_price"))
        gross_reference = ((target_price / price) - 1) if price > 0 and target_price else 0.0
        net_reference = gross_reference - round_trip
        shares = int(planned_capital / price / 100) * 100 if price > 0 else 0
        if shares < 100 and planned_capital >= price * 100:
            shares = 100
        return {
            "assumptions": {
                "reference_capital": cls.REFERENCE_CAPITAL,
                "commission_rate": cls.COMMISSION_RATE,
                "stamp_tax_rate_on_sell": cls.STAMP_TAX_RATE,
                "slippage_rate_each_side": cls.SLIPPAGE_RATE,
                "t_plus_one": True,
            },
            "friction_cost": {
                "commission_pct": round(cls.COMMISSION_RATE * 2 * 100, 3),
                "stamp_tax_pct": round(cls.STAMP_TAX_RATE * 100, 3),
                "slippage_pct": round(cls.SLIPPAGE_RATE * 2 * 100, 3),
                "impact_cost_pct": round(impact * 100, 3),
                "total_round_trip_pct": round(round_trip * 100, 3),
            },
            "capacity": capacity,
            "position_cap_pct": round(position_cap, 2),
            "planned_capital": round(planned_capital, 2),
            "suggested_shares": shares,
            "stop_loss_price": stop_price,
            "reference_target_price": target_price,
            "reference_gross_return_pct": round(gross_reference * 100, 2),
            "reference_net_return_after_cost_pct": round(net_reference * 100, 2),
            "cost_verdict": "成本可控但仍需样本外验证" if net_reference > 0 else "成本可能吃掉参考收益，观望",
        }

    @classmethod
    def strategy_audit(
        cls,
        stock: dict,
        history: list[dict],
        *,
        timeline: dict,
        quality: dict,
        execution: dict,
        source: str,
        is_realtime: bool,
    ) -> dict:
        """Run the independent ten-category falsification checklist."""
        findings: list[dict] = []

        def add(category: str, level: str, evidence: str, experiment: str, fix: str) -> None:
            findings.append({
                "category": category,
                "risk_level": level,
                "evidence": evidence,
                "additional_experiment": experiment,
                "fix_suggestion": fix,
            })

        timeline_level = "高" if timeline.get("red_flags") else "中" if timeline.get("warnings") else "低"
        add(
            "未来信息泄漏",
            timeline_level,
            "；".join(timeline.get("red_flags") or timeline.get("warnings") or ["历史信号按T+1开盘执行规则构造"]),
            "用冻结的T日数据重放，并逐字段检查可用时间。",
            "禁止同日收盘成交；为财务字段补充真实披露日期。",
        )
        add(
            "时间重叠",
            "中",
            "当前选股快照不是样本外回测，尚未验证标签窗口之间的重叠影响。",
            "按持有期设置隔离区，使用滚动样本外窗口复测。",
            "回测使用固定5日持有和非重叠再平衡。",
        )
        add(
            "幸存者偏差",
            "中",
            "当前候选池来自现存可交易股票，历史缓存未包含完整退市股票池。",
            "接入带退市/停牌历史的点时股票池后重跑。",
            "在报告中保留该风险，未补齐前不宣称样本外可信。",
        )
        add(
            "前视偏差",
            "中",
            "行业标签和部分基本面字段的历史生效日期未完整提供。",
            "冻结每个交易日当时的行业分类和披露字段。",
            "历史回测暂不使用无披露日期的基本面字段。",
        )
        add(
            "参数敏感性",
            "中",
            "本次候选结论未进行参数搜索，参数平滑性尚未被证明。",
            "固定实验协议，比较15/20/25日窗口而不选择最优值。",
            "保留全部参数结果，禁止只展示最佳参数。",
        )
        add(
            "收益集中度",
            "中",
            "当前快照没有足够交易样本计算行业、年份和尾部贡献。",
            "报告按行业、月份和去掉最高5%交易日分别统计。",
            "收益集中时降低可信度和仓位，不修改因子迎合结果。",
        )
        market_cap_level = "低" if _number(stock.get("market_cap")) else "中"
        add(
            "市值暴露",
            market_cap_level,
            "候选股票市值字段" + ("可用，但尚未做市值中性化。" if market_cap_level == "低" else "缺失，无法确认小盘暴露。"),
            "剔除最小20%市值后比较结果，并做市值分组。",
            "把市值作为报告中的暴露项，不把它误称为Alpha。",
        )
        cost_level = "高" if execution["friction_cost"]["total_round_trip_pct"] >= 2.0 else "中" if execution["friction_cost"]["total_round_trip_pct"] >= 0.8 else "低"
        add(
            "交易成本",
            cost_level,
            f"估计双边摩擦成本 {execution['friction_cost']['total_round_trip_pct']:.3f}%。",
            "用不同成交额和滑点假设做成本压力测试。",
            "把佣金、卖出印花税、滑点和冲击成本纳入净收益。",
        )
        capacity_level = "中" if "缺少" in execution.get("capacity", "") else "低"
        add(
            "策略容量",
            capacity_level,
            execution.get("capacity", "未提供容量字段"),
            "按计划资金规模和日均成交额模拟冲击成本曲线。",
            "成交额缺失时采用保守成本并限制仓位。",
        )
        add(
            "多重检验",
            "中",
            "本次研究没有把历史结果用于参数优化，但实验次数尚未形成统一登记。",
            "为每次实验记录假设、参数、数据集和结果，盲测只运行一次。",
            "失败实验保留，不允许删除或只汇报成功结果。",
        )

        penalties = {"低": 2, "中": 8, "高": 20, "致命": 40}
        score = _clamp(100 - sum(penalties[item["risk_level"]] for item in findings), 0, 100)
        if any(item["risk_level"] == "致命" for item in findings):
            overall_risk, verdict = "致命", "不可信"
        elif any(item["risk_level"] == "高" for item in findings):
            overall_risk, verdict = "高", "需修复"
        elif any(item["risk_level"] == "中" for item in findings):
            overall_risk, verdict = "中", "证据不足"
        else:
            overall_risk, verdict = "低", "可信"

        blockers: list[str] = []
        if not history:
            blockers.append("没有历史日线，无法验证技术信号")
        if quality.get("grade") == "不足":
            blockers.append("关键数据完整度不足，只能观察不能据此交易")
        if timeline.get("red_flags"):
            blockers.extend(timeline["red_flags"])
        if execution["friction_cost"]["total_round_trip_pct"] >= 3.0:
            blockers.append("估计摩擦成本过高，参考收益不足以覆盖成本")

        warnings = [item["evidence"] for item in findings if item["risk_level"] in ("中", "高")]
        return {
            "overall_risk": overall_risk,
            "findings": findings,
            "verdict": verdict,
            "blockers": blockers,
            "warnings": warnings[:8],
            "credibility_score": round(score, 1),
            "independent_context": "仅使用数据口径、当前结果和审计规则，不使用研究结论作为证据。",
            "is_realtime_snapshot": is_realtime,
            "source": source,
        }


research_protocol = ResearchProtocol()
