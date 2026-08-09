"""Evidence-gated strategic analysis using the user's Mao-strategy framework.

The service keeps facts and interpretation separate. It never fills a missing
market field with zero, never upgrades a cached snapshot to realtime, and
blocks directional trade language when the evidence pack is incomplete.
"""

from __future__ import annotations

import math
from statistics import pstdev
from typing import Any

from services.ai_assistant import ai_assistant_service
from services.macro_policy_news import macro_policy_news_collector
from services.quant_scorer import MarketRegime


def _number(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _signed_pct(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2f}%"


def _money_yi(value: float | None) -> str:
    return "--" if value is None else f"{value / 1e8:+.2f}亿元"


def _latest_date(rows: list[dict[str, Any]]) -> str | None:
    return max((str(row.get("date") or "")[:10] for row in rows if row.get("date")), default=None)


class MaoStrategyAgent:
    schema_version = "mao_strategy_v1"

    async def analyze(self, message: str, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        evidence = context if context is not None else await ai_assistant_service.build_strategy_context(message)
        try:
            regime = await MarketRegime.detect()
        except Exception as exc:
            regime = {
                "regime": "未知",
                "bias": "neutral",
                "confidence": 0.0,
                "error": type(exc).__name__,
            }
        return self.analyze_context(message, evidence, regime=regime)

    @staticmethod
    def _daily_metrics(quote: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(
            (row for row in rows if _number(row.get("close")) not in (None, 0)),
            key=lambda row: str(row.get("date") or ""),
        )
        closes = [_number(row.get("close")) for row in ordered]
        close_values = [value for value in closes if value is not None]
        price = _number(quote.get("price")) or (close_values[-1] if close_values else None)

        def moving_average(window: int) -> float | None:
            if len(close_values) < window:
                return None
            return round(sum(close_values[-window:]) / window, 3)

        def period_return(window: int) -> float | None:
            if price is None or len(close_values) <= window or close_values[-window - 1] == 0:
                return None
            return round((price / close_values[-window - 1] - 1) * 100, 2)

        returns = []
        for previous, current in zip(close_values[-21:-1], close_values[-20:]):
            if previous:
                returns.append((current / previous - 1) * 100)
        volatility = round(pstdev(returns), 3) if len(returns) >= 5 else None

        volume_ratio = _number(quote.get("volume_ratio"))
        if volume_ratio is None:
            volumes = [_number(row.get("volume")) for row in ordered]
            valid_volumes = [value for value in volumes if value is not None and value > 0]
            if len(valid_volumes) >= 6:
                baseline = _mean(valid_volumes[-6:-1])
                if baseline:
                    volume_ratio = round(valid_volumes[-1] / baseline, 3)

        recent_lows = [
            value for row in ordered[-10:]
            if (value := _number(row.get("low"))) is not None and value > 0
        ]
        recent_highs = [
            value for row in ordered[-20:]
            if (value := _number(row.get("high"))) is not None and value > 0
        ]
        prior_highs = [
            value for row in ordered[-21:-1]
            if (value := _number(row.get("high"))) is not None and value > 0
        ]
        latest_row = ordered[-1] if ordered else {}
        latest_open = _number(latest_row.get("open"))
        latest_high = _number(latest_row.get("high"))
        latest_low = _number(latest_row.get("low"))
        latest_close = _number(latest_row.get("close"))
        close_location = None
        lower_shadow_ratio = None
        if latest_high is not None and latest_low is not None and latest_high > latest_low and latest_close is not None:
            close_location = round((latest_close - latest_low) / (latest_high - latest_low), 3)
        if latest_open is not None and latest_low is not None and latest_close not in (None, 0):
            lower_shadow_ratio = round((min(latest_open, latest_close) - latest_low) / latest_close, 4)

        raw_volumes = [_number(row.get("volume")) for row in ordered]
        volume_to_recent_peak = None
        if len(raw_volumes) >= 6 and raw_volumes[-1] is not None:
            comparison = [value for value in raw_volumes[-6:-1] if value is not None and value > 0]
            if comparison:
                volume_to_recent_peak = round(raw_volumes[-1] / max(comparison), 3)

        pullback_days = None
        pullback_from_20d_high = None
        recent_closes = close_values[-20:]
        if price is not None and recent_closes:
            peak = max(recent_closes)
            peak_index = max(index for index, value in enumerate(recent_closes) if value == peak)
            pullback_days = len(recent_closes) - 1 - peak_index
            if peak:
                pullback_from_20d_high = round((price / peak - 1) * 100, 2)

        prior_high_20d = max(prior_highs) if prior_highs else None
        breakout_pct = (
            round((price / prior_high_20d - 1) * 100, 2)
            if price is not None and prior_high_20d not in (None, 0) else None
        )
        ma5, ma10, ma20, ma60 = (moving_average(window) for window in (5, 10, 20, 60))
        score = 0.0
        evidence: list[str] = []
        risks: list[str] = []
        if price is not None and ma20 is not None:
            if price > ma20:
                score += 22
                evidence.append(f"现价{price:.2f}高于MA20 {((price / ma20 - 1) * 100):.1f}%")
            else:
                score -= 22
                risks.append(f"现价{price:.2f}低于MA20 {((1 - price / ma20) * 100):.1f}%")
        if all(value is not None for value in (ma5, ma10, ma20)):
            if ma5 > ma10 > ma20:
                score += 22
                evidence.append("MA5 > MA10 > MA20，短中期趋势共振")
            elif ma5 < ma10 < ma20:
                score -= 22
                risks.append("MA5 < MA10 < MA20，短中期趋势偏弱")
        return_20d = period_return(20)
        if return_20d is not None:
            score += _clamp(return_20d * 1.5, -20, 20)
            (evidence if return_20d >= 0 else risks).append(f"20日价格变化 {_signed_pct(return_20d)}")
        if volume_ratio is not None:
            if 1.2 <= volume_ratio <= 3.5:
                score += 12
                evidence.append(f"量比 {volume_ratio:.2f}，成交参与有效但未过热")
            elif volume_ratio > 5:
                score -= 8
                risks.append(f"量比 {volume_ratio:.2f}，存在情绪过热或对倒噪声")
        turnover_values = [
            value for row in ordered
            if (value := _number(row.get("turnover"))) is not None and value >= 0
        ]
        amount_values = [
            value for row in ordered
            if (value := _number(row.get("amount"))) is not None and value > 0
        ]
        latest_turnover = _number(quote.get("turnover")) or (turnover_values[-1] if turnover_values else None)
        latest_amount = _number(quote.get("amount")) or (amount_values[-1] if amount_values else None)

        def percentile(value: float | None, values: list[float]) -> float | None:
            if value is None or len(values) < 10:
                return None
            return round(sum(item <= value for item in values[-60:]) / len(values[-60:]) * 100, 2)

        return {
            "price": round(price, 3) if price is not None else None,
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "return_5d": period_return(5),
            "return_20d": return_20d,
            "return_60d": period_return(60),
            "volume_ratio": round(volume_ratio, 3) if volume_ratio is not None else None,
            "volatility_20d_pct": volatility,
            "support_10d": round(min(recent_lows), 3) if recent_lows else None,
            "resistance_20d": round(max(recent_highs), 3) if recent_highs else None,
            "prior_high_20d": round(prior_high_20d, 3) if prior_high_20d is not None else None,
            "breakout_pct": breakout_pct,
            "pullback_days": pullback_days,
            "pullback_from_20d_high_pct": pullback_from_20d_high,
            "volume_to_recent_peak": volume_to_recent_peak,
            "close_location": close_location,
            "lower_shadow_ratio": lower_shadow_ratio,
            "turnover_percentile_60d": percentile(latest_turnover, turnover_values),
            "amount_percentile_60d": percentile(latest_amount, amount_values),
            "history_count": len(ordered),
            "history_start": str(ordered[0].get("date")) if ordered else None,
            "history_end": str(ordered[-1].get("date")) if ordered else None,
            "score": round(_clamp(score, -100, 100), 1),
            "evidence": evidence,
            "risks": risks,
        }

    @staticmethod
    def _capital_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(rows, key=lambda row: str(row.get("date") or ""))
        valid = [row for row in ordered if _number(row.get("main_net_inflow")) is not None]
        latest = valid[-1] if valid else {}

        def sum_window(window: int) -> float | None:
            if len(valid) < window:
                return None
            values = [_number(row.get("main_net_inflow")) for row in valid[-window:]]
            numbers = [value for value in values if value is not None]
            return sum(numbers) if len(numbers) == window else None

        latest_main = _number(latest.get("main_net_inflow"))
        flow_5d = sum_window(5)
        flow_10d = sum_window(10)
        positive_days = sum(
            1 for row in valid[-5:]
            if (_number(row.get("main_net_inflow")) or 0) > 0
        ) if len(valid) >= 5 else None
        score = 0.0
        evidence: list[str] = []
        risks: list[str] = []
        if latest_main is not None:
            score += 25 if latest_main > 0 else -25 if latest_main < 0 else 0
            text = f"最近一日主力净流入 {_money_yi(latest_main)}"
            (evidence if latest_main > 0 else risks if latest_main < 0 else evidence).append(text)
        if flow_5d is not None:
            score += 20 if flow_5d > 0 else -20 if flow_5d < 0 else 0
            text = f"近5日主力累计 {_money_yi(flow_5d)}，正流入{positive_days}/5日"
            (evidence if flow_5d > 0 else risks if flow_5d < 0 else evidence).append(text)
        return {
            "data_date": str(latest.get("date") or "")[:10] or None,
            "latest_main_net_inflow": latest_main,
            "main_net_inflow_5d": flow_5d,
            "main_net_inflow_10d": flow_10d,
            "positive_days_5d": positive_days,
            "history_count": len(valid),
            "score": round(_clamp(score, -100, 100), 1),
            "evidence": evidence,
            "risks": risks,
        }

    @staticmethod
    def _announcement_metrics(rows: list[dict[str, Any]], source_available: bool) -> dict[str, Any]:
        recent = [
            item for item in rows
            if macro_policy_news_collector.is_recent(item.get("published_at"), days=90)
        ]
        evidence: list[str] = []
        risks: list[str] = []
        score = 0.0
        for item in recent[:6]:
            value, impact = macro_policy_news_collector.announcement_impact(str(item.get("title") or ""))
            score += value
            text = f"{item.get('published_at') or '--'} {item.get('title') or '未命名公告'}"
            if impact == "positive":
                evidence.append(text)
            elif impact == "negative":
                risks.append(text)
        return {
            "source_available": source_available,
            "latest_date": max((str(item.get("published_at") or "")[:10] for item in rows), default=None),
            "recent_count": len(recent),
            "score": round(_clamp(score, -25, 25), 1),
            "positive": evidence[:3],
            "risks": risks[:3],
            "items": rows[:6],
        }

    def _stock_reports(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        stocks = context.get("stocks") or {}
        quotes = {
            str(item.get("code") or ""): item
            for item in stocks.get("quotes") or []
            if item.get("code")
        }
        daily = stocks.get("daily_bars") or {}
        flow_series = (context.get("stock_fund_flow") or {}).get("series") or {}
        announcements = context.get("announcements") or {}
        announcements_by_code = announcements.get("announcements") or {}
        announcement_status = announcements.get("status") or {}
        dragon_by_code = {
            str(item.get("code") or ""): item
            for item in (context.get("dragon_board") or {}).get("stocks") or []
        }
        personal_by_code = {
            str(item.get("code") or ""): item
            for item in (context.get("personal_pool") or {}).get("items") or []
        }
        codes = list(context.get("stock_codes") or [])
        if not codes:
            codes = list(dict.fromkeys([*quotes, *daily]))
        quote_meta = stocks.get("quote_metadata") or {}
        reports = []
        for code in codes:
            quote = quotes.get(code, {})
            history = list(daily.get(code) or [])
            technical = self._daily_metrics(quote, history)
            capital = self._capital_metrics(list(flow_series.get(code) or []))
            status = announcement_status.get(code) or {}
            announcement = self._announcement_metrics(
                list(announcements_by_code.get(code) or []),
                bool(status.get("available")),
            )
            dragon = dragon_by_code.get(code) or {}
            personal = personal_by_code.get(code) or {}
            name = str(
                quote.get("name")
                or (history[-1].get("name") if history else "")
                or personal.get("name")
                or code
            )
            sector = str(quote.get("sector") or personal.get("industry") or "").strip()
            evidence = [*technical["evidence"], *capital["evidence"]]
            risks = [*technical["risks"], *capital["risks"], *announcement["risks"]]
            if dragon:
                net_amount = _number(dragon.get("net_amount"))
                institution_count = int(_number(dragon.get("institution_count")) or 0)
                dragon_text = (
                    f"龙虎榜净额 {_money_yi(net_amount)}，机构席位{institution_count}个"
                )
                (evidence if (net_amount or 0) > 0 else risks).append(dragon_text)
            if personal.get("risk_note"):
                risks.append(f"个人池风险备注：{personal['risk_note']}")
            score = technical["score"] * 0.48 + capital["score"] * 0.37 + announcement["score"] * 0.15
            if dragon:
                score += _clamp((_number(dragon.get("net_amount")) or 0) / 1e8, -8, 8)
            reports.append({
                "code": code,
                "name": name,
                "sector": sector or None,
                "price": technical["price"],
                "change_pct": _number(quote.get("change_pct")) or (
                    _number(history[-1].get("change_pct")) if history else None
                ),
                "market_cap": _number(quote.get("market_cap")),
                "pe": _number(quote.get("pe")),
                "pb": _number(quote.get("pb")),
                "turnover": _number(quote.get("turnover")) or (
                    _number(history[-1].get("turnover")) if history else None
                ),
                "data_date": quote_meta.get("data_date") or technical["history_end"],
                "is_realtime": bool(quote_meta.get("is_realtime")),
                "technical": technical,
                "capital": capital,
                "announcements": announcement,
                "dragon_board": dragon or None,
                "signal_score": round(_clamp(score, -100, 100), 1),
                "evidence": list(dict.fromkeys(evidence))[:8],
                "risks": list(dict.fromkeys(risks))[:8],
            })
        return reports

    @staticmethod
    def _data_audit(context: dict[str, Any], stock_reports: list[dict[str, Any]]) -> dict[str, Any]:
        audits = [dict(item) for item in context.get("source_audit") or [] if isinstance(item, dict)]
        by_name = {str(item.get("name") or ""): item for item in audits}
        if stock_reports:
            weights = {
                "个股行情": 15, "个股日线": 20, "个股资金流": 15,
                "市场总览": 5, "指数日线": 10, "板块资金流": 10,
                "龙虎榜": 5, "宏观与政策": 10, "公司公告": 10,
            }
        else:
            weights = {
                "市场总览": 25, "指数日线": 25,
                "板块资金流": 25, "龙虎榜": 5, "宏观与政策": 20,
            }
        score = sum(weight for name, weight in weights.items() if by_name.get(name, {}).get("available"))
        missing = [name for name in weights if not by_name.get(name, {}).get("available")]
        if stock_reports:
            minimum_history = min((item["technical"]["history_count"] for item in stock_reports), default=0)
            if minimum_history < 20:
                score -= 12
            elif minimum_history < 60:
                score -= 5
            incomplete_flow = [
                item["code"]
                for item in stock_reports
                if int(item["capital"].get("history_count") or 0) < 5
            ]
            if incomplete_flow:
                score = min(score, 70)
                missing.extend(f"{code}至少5日主力资金流" for code in incomplete_flow)
        score = round(_clamp(float(score), 0, 100), 1)
        grade = "充分" if score >= 75 else "一般" if score >= 50 else "不足"
        if stock_reports:
            for item in stock_reports:
                if item["technical"]["history_count"] < 20:
                    missing.append(f"{item['code']}至少20日日线")
        available_dates = [str(item.get("data_date") or "")[:10] for item in audits if item.get("available") and item.get("data_date")]
        realtime_count = sum(bool(item.get("available") and item.get("is_realtime")) for item in audits)
        available_count = sum(bool(item.get("available")) for item in audits)
        data_mode = "mixed" if realtime_count else "cache_or_history"
        return {
            "grade": grade,
            "score": score,
            "decision_gate": "conditional_research" if grade != "不足" else "observe_only",
            "data_mode": data_mode,
            "is_realtime": bool(available_count and realtime_count == available_count),
            "realtime_sources": realtime_count,
            "available_sources": available_count,
            "data_date": max(available_dates, default=None),
            "missing": list(dict.fromkeys(missing)),
            "sources": audits,
            "warning": (
                "证据达到条件研究门槛，仍需实时盘面复核。" if grade == "充分" else
                "可以形成条件性假设，缺失项会降低仓位上限。" if grade == "一般" else
                "关键证据不足，本轮只能观察，不生成确定性买卖结论。"
            ),
        }

    @staticmethod
    def _market_cycle(context: dict[str, Any], regime: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
        score = 0.0
        evidence: list[str] = []
        points = 0
        index_rows = sorted(
            (
                row for row in (context.get("index_history") or {}).get("history") or []
                if _number(row.get("close")) not in (None, 0)
            ),
            key=lambda row: str(row.get("date") or ""),
        )
        index_closes = [_number(row.get("close")) for row in index_rows]
        index_values = [value for value in index_closes if value is not None]
        index_close = index_values[-1] if index_values else None
        index_ma20 = _mean(index_values[-20:]) if len(index_values) >= 20 else None
        index_ma60 = _mean(index_values[-60:]) if len(index_values) >= 60 else None

        def index_return(window: int) -> float | None:
            if len(index_values) <= window or index_values[-window - 1] == 0:
                return None
            return round((index_values[-1] / index_values[-window - 1] - 1) * 100, 2)

        index_return_5d = index_return(5)
        index_return_20d = index_return(20)
        if index_close is not None and index_ma20 is not None:
            score += 18 if index_close > index_ma20 else -18
            points += 1
            evidence.append(
                f"上证{index_close:.2f}，{'高于' if index_close > index_ma20 else '低于'}MA20({index_ma20:.2f})"
            )
        if index_close is not None and index_ma60 is not None:
            score += 14 if index_close > index_ma60 else -14
            points += 1
            evidence.append(
                f"上证{'高于' if index_close > index_ma60 else '低于'}MA60({index_ma60:.2f})"
            )
        if len(index_values) >= 25:
            recent_ma20 = [_mean(index_values[index - 19:index + 1]) for index in range(len(index_values) - 5, len(index_values))]
            valid_ma20 = [value for value in recent_ma20 if value is not None]
            if len(valid_ma20) == 5:
                slope_up = valid_ma20[-1] > valid_ma20[0]
                score += 8 if slope_up else -8
                evidence.append(f"MA20近5日{'向上' if slope_up else '向下'}")

        bias = str(regime.get("bias") or "neutral")
        regime_name = str(regime.get("regime") or "未知")
        regime_has_observations = any(
            regime.get(key) is not None
            for key in ("positive_days_10", "total_inflow_10d", "avg_daily_change_pct")
        )
        if regime_name != "未知" and regime_has_observations:
            score += 18 if bias == "bullish" else -18 if bias == "bearish" else 0
            points += 1
            evidence.append(
                f"板块历史识别为{regime_name}，置信度{float(regime.get('confidence') or 0) * 100:.0f}%"
            )

        macro = context.get("macro") or {}
        outlook = macro.get("a_share_outlook") or {}
        outlook_score = _number(outlook.get("score"))
        if outlook_score is not None:
            score += _clamp(outlook_score * 0.25, -18, 18)
            points += 1
            evidence.append(str(outlook.get("headline") or f"宏观综合分{outlook_score:+.1f}"))

        market = context.get("market_overview") or {}
        market_summary = (context.get("market_evidence") or {}).get("summary") or {}
        evidence_current = bool(market_summary.get("is_current", True))
        breadth_ratio = _number(market_summary.get("breadth_ratio"))
        if evidence_current and market_summary.get("breadth_complete") and breadth_ratio is not None:
            score += _clamp((breadth_ratio - 50) * 0.6, -12, 12)
            points += 1
            evidence.append(f"全市场上涨宽度{breadth_ratio:.1f}%")
        amount_vs_ma5 = _number(market_summary.get("market_amount_vs_ma5_pct")) if evidence_current else None
        if amount_vs_ma5 is not None:
            score += _clamp(amount_vs_ma5 * 0.35, -10, 10)
            points += 1
            evidence.append(f"市场成交额较5日均值{_signed_pct(amount_vs_ma5)}")
        market_index = market.get("market_index") or {}
        sh_change = _number(market_index.get("sh_change_pct"))
        if sh_change is not None:
            score += _clamp(sh_change * 5, -10, 10)
            points += 1
            evidence.append(f"上证指数最近变化 {_signed_pct(sh_change)}")
        limit_board = market.get("limit_board") or {}
        latest_sentiment = (market_summary.get("latest") or {}) if evidence_current else {}
        limit_up = _number(limit_board.get("limit_up"))
        if limit_up is None:
            limit_up = _number(latest_sentiment.get("limit_up_count"))
        limit_down = _number(limit_board.get("limit_down"))
        if limit_down is None:
            limit_down = _number(latest_sentiment.get("limit_down_count"))
        if limit_up is not None and limit_down is not None:
            spread = limit_up - limit_down
            score += _clamp(spread / 5, -12, 12)
            points += 1
            evidence.append(f"涨停{int(limit_up)}只、跌停{int(limit_down)}只")
        score = round(_clamp(score, -100, 100), 1)
        if points == 0:
            stage, label = "unknown", "周期证据不足"
        elif score >= 24:
            stage, label = "counteroffensive", "战略反攻期"
        elif score <= -20:
            stage, label = "defense", "战略防御期"
        else:
            stage, label = "stalemate", "战略相持期"
        confidence = min(90.0, 25.0 + points * 14.0)
        confidence *= max(0.35, audit["score"] / 100)
        return {
            "stage": stage,
            "label": label,
            "score": score if points else None,
            "confidence": round(confidence, 1),
            "evidence": evidence,
            "index_metrics": {
                "data_date": str(index_rows[-1].get("date") or "")[:10] if index_rows else None,
                "close": round(index_close, 3) if index_close is not None else None,
                "ma20": round(index_ma20, 3) if index_ma20 is not None else None,
                "ma60": round(index_ma60, 3) if index_ma60 is not None else None,
                "return_5d": index_return_5d,
                "return_20d": index_return_20d,
                "history_count": len(index_values),
            },
            "falsification": [
                "市场广度与指数方向连续两日反转",
                "主力资金与当前阶段方向连续三日背离",
                "高影响政策或外部事件改变风险偏好",
            ],
        }

    @staticmethod
    def _strategy_factors(
        context: dict[str, Any],
        stocks: list[dict[str, Any]],
        cycle: dict[str, Any],
    ) -> list[dict[str, Any]]:
        market = context.get("market_overview") or {}
        sector = context.get("sector_flow") or {}
        inflows = [
            row for payload in sector.values() if isinstance(payload, dict)
            for row in payload.get("top_net_inflow") or []
            if _number(row.get("main_net_inflow")) is not None
        ]
        outflows = [
            row for payload in sector.values() if isinstance(payload, dict)
            for row in payload.get("top_net_outflow") or []
            if _number(row.get("main_net_inflow")) is not None
        ]

        regime_missing = []
        index_metrics = cycle.get("index_metrics") or {}
        market_summary = (context.get("market_evidence") or {}).get("summary") or {}
        evidence_current = bool(market_summary.get("is_current", True))
        if int(index_metrics.get("history_count") or 0) < 60:
            regime_missing.append("完整60日指数日线")
        if (not evidence_current or not market_summary.get("breadth_complete")) and not market.get("market_breadth"):
            regime_missing.append("全市场真实涨跌宽度")
        if not evidence_current or int(market_summary.get("amount_history_count") or 0) < 5:
            regime_missing.append("市场成交额历史趋势")
        market_regime = {
            "id": "market_regime_score",
            "name": "市场状态分",
            "score": cycle.get("score"),
            "status": "available" if cycle.get("score") is not None and not regime_missing else "partial" if cycle.get("score") is not None else "blocked",
            "evidence": list(cycle.get("evidence") or [])[:5],
            "missing": list(dict.fromkeys(regime_missing)),
            "interpretation": "指数MA20/MA60、市场宽度和成交额趋势必须同时核验。",
        }

        sector_score = None
        sector_evidence: list[str] = []
        sector_missing: list[str] = []
        sector_breadth_available = False
        if inflows or outflows:
            positive_flow = sum(max(_number(row.get("main_net_inflow")) or 0, 0) for row in inflows[:5])
            negative_flow = sum(min(_number(row.get("main_net_inflow")) or 0, 0) for row in outflows[:5])
            denominator = max(abs(positive_flow) + abs(negative_flow), 1)
            sector_score = round(_clamp((positive_flow + negative_flow) / denominator * 100, -100, 100), 1)
            if inflows:
                sector_evidence.append("净流入前列：" + "、".join(str(row.get("name") or row.get("code")) for row in inflows[:3]))
            matched = []
            for stock in stocks:
                stock_sector = str(stock.get("sector") or "")
                if not stock_sector:
                    continue
                for row in inflows:
                    board_name = str(row.get("name") or "")
                    if board_name and (board_name in stock_sector or stock_sector in board_name):
                        matched.append(f"{stock['name']}所属{stock_sector}进入资金流入前列")
                        break
            sector_evidence.extend(matched)
            index_return_5d = _number(index_metrics.get("return_5d"))
            index_return_20d = _number(index_metrics.get("return_20d"))
            return_5d = [
                _number(row.get("return_5d")) for row in [*inflows, *outflows]
                if _number(row.get("return_5d")) is not None
            ]
            return_20d = [
                _number(row.get("return_20d")) for row in [*inflows, *outflows]
                if _number(row.get("return_20d")) is not None
            ]
            breadth_rows = [
                row for row in [*inflows, *outflows]
                if _number(row.get("breadth_ratio")) is not None
            ]
            if return_5d and index_return_5d is not None:
                relative_5d = _mean([value - index_return_5d for value in return_5d])
                sector_score = round(_clamp((sector_score or 0) * 0.65 + (relative_5d or 0) * 8, -100, 100), 1)
                sector_evidence.append(f"板块相对指数5日强度{_signed_pct(relative_5d)}")
            else:
                sector_missing.append("板块5日相对强度")
            if return_20d and index_return_20d is not None:
                relative_20d = _mean([value - index_return_20d for value in return_20d])
                sector_score = round(_clamp((sector_score or 0) * 0.75 + (relative_20d or 0) * 5, -100, 100), 1)
                sector_evidence.append(f"板块相对指数20日强度{_signed_pct(relative_20d)}")
            else:
                sector_missing.append("板块20日相对强度")
            if breadth_rows:
                sector_breadth_available = True
                breadth = _mean([_number(row.get("breadth_ratio")) for row in breadth_rows if _number(row.get("breadth_ratio")) is not None])
                sector_evidence.append(f"重点板块成分上涨比例{breadth:.1f}%")
            else:
                sector_missing.append("板块成分股上涨比例")
        else:
            sector_missing.extend(["板块5/20日相对强度", "板块成分股上涨比例"])
        sector_leadership = {
            "id": "sector_leadership_score",
            "name": "板块主线强度",
            "score": sector_score,
            "status": "available" if sector_score is not None and not sector_missing else "partial" if sector_score is not None else "blocked",
            "evidence": sector_evidence[:5],
            "missing": list(dict.fromkeys(sector_missing)),
            "interpretation": "只有板块连续强于指数且成分宽度扩散，才可称为主线。",
        }

        limit_board = market.get("limit_board") or {}
        limit_up = _number(limit_board.get("limit_up"))
        limit_down = _number(limit_board.get("limit_down"))
        crowd_evidence: list[str] = []
        crowd_parts: list[float] = []
        if limit_up is not None and limit_down is not None:
            total_limits = limit_up + limit_down
            crowd_parts.append(_clamp(total_limits / 120 * 55, 0, 55))
            if total_limits:
                crowd_parts.append(_clamp(abs(limit_up - limit_down) / total_limits * 25, 0, 25))
            crowd_evidence.append(f"涨停{int(limit_up)}只、跌停{int(limit_down)}只")
        sentiment_latest = market_summary.get("latest") or {} if evidence_current else {}
        if limit_up is None:
            limit_up = _number(sentiment_latest.get("limit_up_count"))
        if limit_down is None:
            limit_down = _number(sentiment_latest.get("limit_down_count"))
        if limit_up is not None and limit_down is not None and not crowd_evidence:
            total_limits = limit_up + limit_down
            crowd_parts.append(_clamp(total_limits / 120 * 55, 0, 55))
            if total_limits:
                crowd_parts.append(_clamp(abs(limit_up - limit_down) / total_limits * 25, 0, 25))
            crowd_evidence.append(f"涨停{int(limit_up)}只、跌停{int(limit_down)}只")
        failed_rate = _number(market_summary.get("failed_limit_rate")) if evidence_current else None
        max_streak = _number(market_summary.get("max_streak_height")) if evidence_current else None
        amount_percentile = _number(market_summary.get("market_amount_percentile")) if evidence_current else None
        turnover_percentile = _number(market_summary.get("average_turnover_percentile")) if evidence_current else None
        crowd_missing: list[str] = []
        if failed_rate is not None:
            crowd_parts.append(_clamp(failed_rate * 0.4, 0, 20))
            crowd_evidence.append(f"炸板率{failed_rate:.1f}%")
        else:
            crowd_missing.append("炸板率")
        if max_streak is not None:
            crowd_parts.append(_clamp(max_streak * 2, 0, 20))
            crowd_evidence.append(f"连板高度{int(max_streak)}板")
        else:
            crowd_missing.append("连板高度")
        if amount_percentile is not None and turnover_percentile is not None:
            crowd_parts.append(_clamp((amount_percentile + turnover_percentile) / 10, 0, 20))
            crowd_evidence.append(f"市场成交额/换手历史分位{amount_percentile:.1f}%/{turnover_percentile:.1f}%")
        else:
            crowd_missing.append("换手/成交额历史分位")
        if evidence_current and market_summary.get("breadth_complete"):
            breadth = _number(market_summary.get("breadth_ratio"))
            if breadth is not None:
                crowd_parts.append(_clamp(abs(breadth - 50) * 0.2, 0, 10))
                crowd_evidence.append(f"真实市场宽度{breadth:.1f}%")
        else:
            crowd_missing.append("真实市场宽度")
        for stock in stocks:
            turnover = _number(stock.get("turnover"))
            volume_ratio = _number(stock["technical"].get("volume_ratio"))
            if turnover is not None and turnover >= 15:
                crowd_parts.append(min(20, turnover / 2))
                crowd_evidence.append(f"{stock['name']}换手{turnover:.1f}%")
            if volume_ratio is not None and volume_ratio >= 4:
                crowd_parts.append(min(20, volume_ratio * 2.5))
                crowd_evidence.append(f"{stock['name']}量比{volume_ratio:.2f}")
        if "换手/成交额历史分位" in crowd_missing and any(
            _number(stock["technical"].get("turnover_percentile_60d")) is not None
            and _number(stock["technical"].get("amount_percentile_60d")) is not None
            for stock in stocks
        ):
            crowd_missing.remove("换手/成交额历史分位")
        crowd_score = round(_clamp(sum(crowd_parts), 0, 100), 1) if crowd_parts else None
        crowd_extreme = {
            "id": "crowd_extreme_score",
            "name": "大众情绪极值",
            "score": crowd_score,
            "status": "available" if crowd_score is not None and not crowd_missing else "partial" if crowd_score is not None else "blocked",
            "evidence": crowd_evidence[:5],
            "missing": list(dict.fromkeys(crowd_missing)),
            "interpretation": "极端可以持续；只有出现滞涨、炸板上升或宽度反转才算转衰确认。",
        }

        exhaustion_scores: list[float] = []
        exhaustion_evidence: list[str] = []
        exhaustion_has_volume = False
        for stock in stocks:
            technical = stock["technical"]
            if int(technical.get("history_count") or 0) < 10 or _number(technical.get("price")) is None:
                continue
            local = 0.0
            pullback_days = technical.get("pullback_days")
            pullback_pct = _number(technical.get("pullback_from_20d_high_pct"))
            volume_to_peak = _number(technical.get("volume_to_recent_peak"))
            price = _number(technical.get("price"))
            ma10 = _number(technical.get("ma10"))
            ma20 = _number(technical.get("ma20"))
            lower_shadow = _number(technical.get("lower_shadow_ratio"))
            if pullback_days is not None and 2 <= int(pullback_days) <= 4:
                local += 25
                exhaustion_evidence.append(f"{stock['name']}回调{int(pullback_days)}日")
            if pullback_pct is not None and -8 <= pullback_pct <= -1:
                local += 20
                exhaustion_evidence.append(f"{stock['name']}距20日高点{pullback_pct:.1f}%")
            if volume_to_peak is not None and volume_to_peak <= 0.5:
                local += 30
                exhaustion_evidence.append(f"{stock['name']}成交量缩至近期峰量{volume_to_peak * 100:.0f}%")
            if volume_to_peak is not None:
                exhaustion_has_volume = True
            nearby_supports = [
                (label, value)
                for label, value in (("MA10", ma10), ("MA20", ma20))
                if value not in (None, 0) and price is not None and abs(price / value - 1) <= 0.03
            ]
            if nearby_supports:
                local += 15
                exhaustion_evidence.append(
                    f"{stock['name']}接近{'/'.join(label for label, _ in nearby_supports)}支撑"
                )
            if lower_shadow is not None and lower_shadow >= 0.01:
                local += 10
                exhaustion_evidence.append(f"{stock['name']}出现下影承接")
            exhaustion_scores.append(_clamp(local, 0, 100))
        exhaustion_score = round(_mean(exhaustion_scores), 1) if exhaustion_scores else None
        exhaustion_missing = []
        if not stocks:
            exhaustion_missing = []
            exhaustion_status = "not_applicable"
            exhaustion_evidence.append("本轮未指定个股，抛压衰竭因子不参与大盘研判")
        elif not exhaustion_scores:
            exhaustion_missing.append("至少10日个股日线")
            exhaustion_status = "blocked"
        elif not exhaustion_has_volume:
            exhaustion_missing.append("近期成交量")
            exhaustion_status = "partial"
        else:
            exhaustion_status = "available"
        supply_exhaustion = {
            "id": "supply_exhaustion_score",
            "name": "抛压衰竭确认",
            "score": exhaustion_score,
            "status": exhaustion_status,
            "evidence": exhaustion_evidence[:6],
            "missing": exhaustion_missing,
            "interpretation": "缩量回调只是候选，仍需次日重新站上均价线或关键均线确认。",
        }

        breakout_scores: list[float] = []
        breakout_evidence: list[str] = []
        breakout_has_volume = False
        for stock in stocks:
            technical = stock["technical"]
            if (
                int(technical.get("history_count") or 0) < 21
                or _number(technical.get("price")) is None
                or _number(technical.get("prior_high_20d")) is None
            ):
                continue
            local = 0.0
            breakout_pct = _number(technical.get("breakout_pct"))
            volume_ratio = _number(technical.get("volume_ratio"))
            close_location = _number(technical.get("close_location"))
            if breakout_pct is not None and breakout_pct > 0:
                local += 40
                breakout_evidence.append(f"{stock['name']}突破20日高点{breakout_pct:.1f}%")
            if volume_ratio is not None and volume_ratio >= 1.5:
                local += 25
                breakout_evidence.append(f"{stock['name']}量比{volume_ratio:.2f}")
            if volume_ratio is not None:
                breakout_has_volume = True
            if close_location is not None and close_location >= 0.75:
                local += 20
                breakout_evidence.append(f"{stock['name']}收于日内区间高位")
            if sector_score is not None and sector_score > 10:
                local += 15
            breakout_scores.append(_clamp(local, 0, 100))
        breakout_score = round(_mean(breakout_scores), 1) if breakout_scores else None
        breakout_missing: list[str] = []
        if not stocks:
            breakout_status = "not_applicable"
            breakout_evidence.append("本轮未指定个股，突破共振因子不参与大盘研判")
        elif not breakout_scores:
            breakout_missing.append("至少21日个股日线与20日高点")
            if not sector_breadth_available:
                breakout_missing.append("板块成分股上涨比例")
            breakout_status = "blocked"
        elif not breakout_has_volume:
            breakout_missing.append("量比或5日均量")
            if not sector_breadth_available:
                breakout_missing.append("板块成分股上涨比例")
            breakout_status = "partial"
        else:
            if not sector_breadth_available:
                breakout_missing.append("板块成分股上涨比例")
            breakout_status = "partial" if breakout_missing else "available"
        breakout_confirmation = {
            "id": "breakout_confirmation_score",
            "name": "突破共振确认",
            "score": breakout_score,
            "status": breakout_status,
            "evidence": breakout_evidence[:6],
            "missing": breakout_missing,
            "interpretation": "突破必须同时有量能、收盘位置和板块联动，孤立突破不追。",
        }
        return [market_regime, sector_leadership, crowd_extreme, supply_exhaustion, breakout_confirmation]

    @staticmethod
    def _research_hypotheses(
        factors: list[dict[str, Any]],
        cycle: dict[str, Any],
    ) -> list[dict[str, Any]]:
        by_id = {item["id"]: item for item in factors}

        def factor_score(factor_id: str) -> float | None:
            return _number((by_id.get(factor_id) or {}).get("score"))

        exhaustion = factor_score("supply_exhaustion_score")
        breakout = factor_score("breakout_confirmation_score")
        crowd = factor_score("crowd_extreme_score")
        sector = factor_score("sector_leadership_score")
        breakout_factor = by_id.get("breakout_confirmation_score") or {}
        breadth_missing = "板块成分股上涨比例" in (breakout_factor.get("missing") or [])
        if breakout is None:
            breakout_status = "blocked_data"
        elif breakout >= 65 and (sector or 0) > 0:
            breakout_status = "awaiting_breadth_confirmation" if breadth_missing else "candidate"
        else:
            breakout_status = "not_triggered"
        return [
            {
                "id": "H1",
                "name": "敌疲我打",
                "hypothesis": "强势板块核心股回调2-4日、缩量至峰量50%以下并接近MA10/MA20后，重新转强样本未来3日风险调整收益更高。",
                "status": "candidate" if exhaustion is not None and exhaustion >= 55 else "not_triggered" if exhaustion is not None else "blocked_data",
                "evidence": (by_id.get("supply_exhaustion_score") or {}).get("evidence") or [],
            },
            {
                "id": "H2",
                "name": "敌退我追",
                "hypothesis": "突破20日高点、量能达5日均量1.5倍且板块宽度超过60%的共振突破，优于孤立突破。",
                "status": breakout_status,
                "evidence": breakout_factor.get("evidence") or [],
            },
            {
                "id": "H3",
                "name": "极盛转衰",
                "hypothesis": "高涨幅、高换手与高成交额同时出现，且价格滞涨、炸板率上升时，未来1-3日回撤概率上升。",
                "status": "watch_reversal_confirmation" if crowd is not None and crowd >= 65 else "not_triggered" if crowd is not None else "blocked_data",
                "evidence": (by_id.get("crowd_extreme_score") or {}).get("evidence") or [],
            },
            {
                "id": "H4",
                "name": "游击优于硬扛",
                "hypothesis": "战略相持期的尾盘小仓入场和2-5日时间止损，应比无市场过滤的持有方式回撤更低。",
                "status": (
                    "active_research"
                    if cycle.get("stage") == "stalemate" and float(cycle.get("confidence") or 0) >= 40
                    else "blocked_data"
                    if cycle.get("stage") == "stalemate"
                    else "regime_not_matched"
                ),
                "evidence": list(cycle.get("evidence") or []),
            },
            {
                "id": "H5",
                "name": "主线集中但不重仓赌博",
                "hypothesis": "持有1-3只低相关核心标的、单票不超过25%，在收益接近时应比单票满仓有更低最大回撤。",
                "status": "portfolio_guardrail",
                "evidence": ["需通过历史组合与模拟盘对比验证，不以个案代替统计"],
            },
        ]

    @staticmethod
    def _camps(context: dict[str, Any], stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        leadership_evidence: list[str] = []
        leadership_scores = []
        momentum_evidence: list[str] = []
        momentum_scores = []
        crowd_evidence: list[str] = []
        opposition_evidence: list[str] = []
        opposition_score = 0.0
        for stock in stocks:
            leadership_scores.append(stock["capital"]["score"])
            leadership_evidence.extend(f"{stock['name']}：{text}" for text in stock["capital"]["evidence"][:2])
            momentum_scores.append(stock["technical"]["score"])
            momentum_evidence.extend(f"{stock['name']}：{text}" for text in stock["technical"]["evidence"][:2])
            turnover = _number(stock.get("turnover"))
            volume_ratio = _number(stock["technical"].get("volume_ratio"))
            if turnover is not None or volume_ratio is not None:
                crowd_evidence.append(
                    f"{stock['name']}：换手{turnover:.1f}%" if turnover is not None else
                    f"{stock['name']}：量比{volume_ratio:.2f}"
                )
            if stock["risks"]:
                opposition_evidence.extend(f"{stock['name']}：{text}" for text in stock["risks"][:2])
                opposition_score += len(stock["risks"][:2]) * 12

        sector = context.get("sector_flow") or {}
        inflows = [
            row for payload in sector.values() if isinstance(payload, dict)
            for row in payload.get("top_net_inflow") or []
            if (_number(row.get("main_net_inflow")) or 0) > 0
        ]
        outflows = [
            row for payload in sector.values() if isinstance(payload, dict)
            for row in payload.get("top_net_outflow") or []
            if (_number(row.get("main_net_inflow")) or 0) < 0
        ]
        if inflows:
            momentum_evidence.append(
                "板块流入前列：" + "、".join(str(row.get("name") or row.get("code")) for row in inflows[:3])
            )
        if outflows:
            opposition_evidence.append(
                "板块流出前列：" + "、".join(str(row.get("name") or row.get("code")) for row in outflows[:3])
            )

        market = context.get("market_overview") or {}
        north = market.get("north_bound") or {}
        north_value = _number(north.get("latest_inflow"))
        if north_value is not None:
            leadership_evidence.append(f"北向最近可核验净流入 {_money_yi(north_value)}")
            leadership_scores.append(_clamp(north_value / 1e8, -30, 30))
        limit_board = market.get("limit_board") or {}
        if limit_board.get("limit_up") is not None and limit_board.get("limit_down") is not None:
            crowd_evidence.append(
                f"涨停{int(limit_board['limit_up'])}只、跌停{int(limit_board['limit_down'])}只"
            )

        leadership = _mean(leadership_scores)
        momentum = _mean(momentum_scores)
        return [
            {
                "key": "leadership",
                "label": "领导力量（主力/机构）",
                "stance": "supportive" if leadership is not None and leadership > 12 else "pressured" if leadership is not None and leadership < -12 else "neutral" if leadership is not None else "unknown",
                "summary": "观察主力、北向与机构席位是否同向。",
                "evidence": leadership_evidence[:5] or ["主力/机构资金字段不完整"],
            },
            {
                "key": "momentum",
                "label": "动量先锋（游资/活跃资金）",
                "stance": "supportive" if momentum is not None and momentum > 15 else "pressured" if momentum is not None and momentum < -15 else "neutral" if momentum is not None or inflows else "unknown",
                "summary": "价格、量能与热点板块同步时，动量才有延续基础。",
                "evidence": momentum_evidence[:5] or ["动量与板块共振证据不足"],
            },
            {
                "key": "crowd",
                "label": "跟风群体（散户/零售情绪）",
                "stance": "neutral" if crowd_evidence else "unknown",
                "summary": "换手和涨跌停宽度只是情绪代理，不可单独当作买卖依据。",
                "evidence": crowd_evidence[:5] or ["缺少可核验的情绪广度数据"],
            },
            {
                "key": "opposition",
                "label": "核心对立面（减持/套牢/抛压）",
                "stance": "pressured" if opposition_score >= 24 or outflows else "neutral" if stocks else "unknown",
                "summary": "公告风险、资金流出与阻力位是必须先排除的红线。",
                "evidence": opposition_evidence[:6] or ["未发现可核验的强抛压证据，但不等于风险为零"],
            },
        ]

    @staticmethod
    def _main_contradiction(audit: dict[str, Any], cycle: dict[str, Any], stocks: list[dict[str, Any]]) -> dict[str, Any]:
        if audit["grade"] == "不足":
            return {
                "title": "数据完整性与决策确定性的矛盾",
                "summary": "当前首要问题不是预测涨跌，而是补齐能验证主力、趋势、政策与公告的证据。",
                "supporting_evidence": [audit["warning"]],
                "counter_evidence": ["缺失：" + "、".join(audit["missing"][:6]) if audit["missing"] else "未返回足够可核验数据"],
                "falsification": ["补齐缺失数据后重新运行战略研判"],
            }
        if stocks:
            trend = _mean([item["technical"]["score"] for item in stocks]) or 0.0
            capital = _mean([item["capital"]["score"] for item in stocks]) or 0.0
            if trend * capital < 0:
                title = "价格趋势与资金行为的背离"
                summary = "技术趋势与主力资金未形成同向确认，不宜把单一信号解释为主升。"
            elif trend > 15 and capital > 10:
                title = "趋势延续与高位兑现压力"
                summary = "价量与资金同向是主要方面，但需防范高换手、阻力位和公告风险使优势反转。"
            elif trend < -15 and capital < -10:
                title = "弱势惯性与超跌修复的冲突"
                summary = "当前抛压仍占主导，必须先看到缩量止跌与资金回流，再谈反击。"
            else:
                title = "方向确认与震荡噪声"
                summary = "当前信号强度不足以形成单边判断，需要等待资金、板块与均线同步。"
            support = [text for item in stocks for text in item["evidence"][:2]][:6]
            counter = [text for item in stocks for text in item["risks"][:2]][:6]
            return {
                "title": title,
                "summary": summary,
                "supporting_evidence": support or ["支持性证据有限"],
                "counter_evidence": counter or ["反方证据尚不充分"],
                "falsification": [
                    "收盘跌破MA20或近10日支撑",
                    "近5日主力资金由正转负并持续",
                    "出现减持、立案、预亏或退市风险公告",
                ],
            }
        cycle_score = _number(cycle.get("score"))
        if cycle_score is not None and cycle_score > 15:
            title = "风险偏好回升与结构分化"
            summary = "市场环境偏积极，但只有资金持续流入的主线才具有反攻条件。"
        elif cycle_score is not None and cycle_score < -15:
            title = "风险释放与本金保全"
            summary = "当前主要矛盾是保存资金并等待抛压衰竭，不与下行趋势正面对抗。"
        else:
            title = "存量轮动与主线确认"
            summary = "市场仍在相持阶段，核心是识别能连续获得资金和政策共振的方向。"
        return {
            "title": title,
            "summary": summary,
            "supporting_evidence": cycle.get("evidence") or ["市场方向证据有限"],
            "counter_evidence": ["板块轮动过快或成交额未配合时，主线假设不成立"],
            "falsification": cycle.get("falsification") or [],
        }

    @staticmethod
    def _tactics(audit: dict[str, Any], cycle: dict[str, Any], stocks: list[dict[str, Any]]) -> dict[str, Any]:
        stage = cycle.get("stage")
        signal = _mean([item["signal_score"] for item in stocks]) if stocks else None
        if audit["decision_gate"] == "observe_only":
            action = "observe"
            posture = "证据不足，暂不部署仓位"
            total_cap = [0, 0]
            single_cap = 0
        elif stage == "defense":
            action = "defend"
            posture = "战略防御：避开主跌与高抛压，优先保全本金"
            total_cap = [0, 20]
            single_cap = 5
        elif stage == "counteroffensive" and (signal is None or signal >= 15):
            action = "follow_after_confirmation"
            posture = "战略反攻：只跟踪主线中价量资金共振的核心标的"
            total_cap = [40, 60]
            single_cap = 25
        elif signal is not None and signal <= -20:
            action = "avoid"
            posture = "弱势回避：当前信号不支持试错"
            total_cap = [0, 10]
            single_cap = 5
        else:
            action = "probe"
            posture = "战略相持：尾盘小仓验证，2-5日无效就退，待主线确认再调整"
            total_cap = [20, 40]
            single_cap = 12

        if audit["grade"] == "一般":
            total_cap = [round(total_cap[0] * 0.6), round(total_cap[1] * 0.6)]
            single_cap = round(single_cap * 0.6)

        volatilities = [
            value for item in stocks
            if (value := _number(item["technical"].get("volatility_20d_pct"))) is not None
        ]
        stop_pct = round(_clamp(max(volatilities, default=2.0) * 1.5, 3.0, 5.0), 1) if stocks and audit["decision_gate"] != "observe_only" else None
        supports = [
            f"{item['name']} {item['technical']['support_10d']:.2f}"
            for item in stocks if item["technical"].get("support_10d") is not None
        ]
        entry_conditions = [
            "市场周期不处于战略防御期",
            "收盘价站上MA20，且MA5与MA10形成顺向结构",
            "近5日主力资金为正，所属板块资金同向",
            "公告来源可用，且无减持、立案、预亏等硬风险",
        ]
        retreat_conditions = [
            "收盘跌破MA20或近10日支撑位",
            "主力资金连续3日净流出，且价格未抵抗",
            "板块从资金流入前列转为持续流出",
            "任一事前确定的假设失效，不因主观成本拖延退出",
        ]
        return {
            "action": action,
            "posture": posture,
            "total_position_range_pct": total_cap,
            "single_position_cap_pct": single_cap,
            "absolute_single_position_cap_pct": 25,
            "auction_enhanced_single_cap_pct": 30,
            "time_stop_days": [2, 5] if action == "probe" else None,
            "entry_conditions": entry_conditions,
            "validation_conditions": [
                "价格、量能、主力资金和板块至少三项同向",
                "实时行情与报告数据日一致；否则重新分析",
            ],
            "retreat_conditions": retreat_conditions,
            "stop_loss": {
                "percent": stop_pct,
                "reference": "近10日支撑：" + "、".join(supports) if supports else "支撑位数据不足",
                "rule": "百分比止损与关键支撑位二者先触发者为准",
            },
            "red_lines": [
                "不因浮亏扩大仓位，不借贷加杠杆",
                "禁止满仓或把“歼灭战”理解为全部本金压单票",
                "数据不足、行情日期不明或停牌时不生成执行信号",
                "连续3笔验证性交易亏损时，暂停新试错并复盘",
                "不把资金流、龙虎榜或单一指标解释为必涨",
                "极端情绪可以持续，未出现滞涨/宽度反转/炸板上升前不押注顶底",
            ],
        }

    @staticmethod
    def _review(main: dict[str, Any], stocks: list[dict[str, Any]]) -> dict[str, Any]:
        targets = "、".join(
            item["code"] if item["name"] == item["code"] else f"{item['name']}({item['code']})"
            for item in stocks
        ) or "A股市场"
        return {
            "hypothesis": f"{targets}的主要假设：{main['title']}。{main['summary']}",
            "verification_window": "下一交易日盘面确认，5个交易日做阶段复盘",
            "checkpoints": [
                "预判与实际走势是否相符？哪条证据被证实或证伪？",
                "亏损是因为看错主要矛盾，还是战术执行不坚决？",
                "是否在数据过期、证据不足或条件未满足时强行交易？",
                "下一次应删除哪个无效因素，补充哪个可证伪条件？",
            ],
            "status": "待交易或观察窗口结束后填写",
        }

    def analyze_context(
        self,
        message: str,
        context: dict[str, Any],
        *,
        regime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stocks = self._stock_reports(context)
        audit = self._data_audit(context, stocks)
        cycle = self._market_cycle(context, regime or {}, audit)
        strategy_factors = self._strategy_factors(context, stocks, cycle)
        research_hypotheses = self._research_hypotheses(strategy_factors, cycle)
        camps = self._camps(context, stocks)
        main = self._main_contradiction(audit, cycle, stocks)
        tactics = self._tactics(audit, cycle, stocks)
        return {
            "schema_version": self.schema_version,
            "generated_at": context.get("generated_at"),
            "query": str(message or "").strip(),
            "scope": {
                "type": "stock" if stocks else "market",
                "stock_codes": [item["code"] for item in stocks],
            },
            "data_audit": audit,
            "main_contradiction": main,
            "camps": camps,
            "cycle": cycle,
            "strategy_factors": strategy_factors,
            "research_hypotheses": research_hypotheses,
            "tactics": tactics,
            "stock_reports": stocks,
            "review": self._review(main, stocks),
            "disclaimer": (
                "本报告是基于可核验实时/缓存数据的条件性研究和模拟决策，"
                "不承诺收益，不连接券商下单。"
            ),
        }

    @staticmethod
    def render_report(report: dict[str, Any]) -> str:
        audit = report["data_audit"]
        main = report["main_contradiction"]
        cycle = report["cycle"]
        tactics = report["tactics"]
        lines = [
            "【数据审计】",
            f"证据{audit['grade']} · {audit['score']:.0f}分 · 综合证据日{audit.get('data_date') or '--'} · "
            + ("纯实时" if audit.get("is_realtime") else "实时/缓存混合或历史快照"),
            audit["warning"],
            "",
            "【主要矛盾分析】",
            f"{main['title']}：{main['summary']}",
        ]
        if main.get("supporting_evidence"):
            lines.append("支持证据：" + "；".join(main["supporting_evidence"][:4]))
        if main.get("counter_evidence"):
            lines.append("反方证据：" + "；".join(main["counter_evidence"][:4]))
        lines.extend(["", "【阵营与资金博弈】"])
        for camp in report["camps"]:
            lines.append(f"{camp['label']}：{camp['summary']} " + "；".join(camp["evidence"][:2]))
        lines.extend([
            "",
            "【周期阶段定位】",
            f"{cycle['label']} · 评分{cycle.get('score') if cycle.get('score') is not None else '--'} · 置信度{cycle['confidence']:.0f}%",
            "；".join(cycle.get("evidence") or ["周期证据不足"]),
            "",
            "【五个斗争因子】",
        ])
        for factor in report.get("strategy_factors") or []:
            value = "--" if factor.get("score") is None else f"{factor['score']:+.1f}"
            lines.append(
                f"{factor['name']} {value} · {factor['status']}："
                + ("；".join(factor.get("evidence") or []) or "待补数据")
            )
        lines.extend(["", "【可证伪研究假设】"])
        for hypothesis in report.get("research_hypotheses") or []:
            lines.append(
                f"{hypothesis['id']} {hypothesis['name']} · {hypothesis['status']}：{hypothesis['hypothesis']}"
            )
        lines.extend([
            "",
            "【战术部署与风控红线】",
            tactics["posture"],
            f"模拟总仓参考 {tactics['total_position_range_pct'][0]}%-{tactics['total_position_range_pct'][1]}%，"
            f"单票上限 {tactics['single_position_cap_pct']}%",
            "入场前提：" + "；".join(tactics["entry_conditions"]),
            "撤退条件：" + "；".join(tactics["retreat_conditions"]),
            "风控红线：" + "；".join(tactics["red_lines"]),
        ])
        if tactics["stop_loss"].get("percent") is not None:
            lines.append(
                f"初始风控 {tactics['stop_loss']['percent']:.1f}%；{tactics['stop_loss']['reference']}"
            )
        if report.get("stock_reports"):
            lines.extend(["", "【标的实证摘要】"])
            for item in report["stock_reports"]:
                stock_label = item["code"] if item["name"] == item["code"] else f"{item['name']}({item['code']})"
                lines.append(
                    f"{stock_label} 现价{item['price'] if item['price'] is not None else '--'} "
                    f"· 证据日{item['data_date'] or '--'} · 信号分{item['signal_score']:+.1f}"
                )
        review = report["review"]
        lines.extend([
            "",
            "【闭环复盘】",
            review["hypothesis"],
            review["verification_window"],
            "复盘问题：" + "；".join(review["checkpoints"]),
            "",
            report["disclaimer"],
        ])
        return "\n".join(lines)


mao_strategy_agent = MaoStrategyAgent()
