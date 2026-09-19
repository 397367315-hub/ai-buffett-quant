"""Deterministic Wildman rules.

The rule core only consumes structured facts and never calls AI or external
data sources. Unknown facts remain waiting evidence. Rule changes require a
new version so historical decisions remain replayable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable


RULE_VERSION = "WM_RULE_CORE_V1_1"


class Cycle(StrEnum):
    ICE = "冰点"
    START = "启动/试错"
    MAIN_RISE = "发酵/主升"
    CLIMAX = "高潮"
    RETREAT = "退潮"


class CycleNode(StrEnum):
    ICE_TURN = "冰点节点"
    START = "启动节点"
    FIRST_DIVERGENCE = "主升确认/首分歧节点"
    RETREAT = "退潮节点"
    NON_KEY = "非关键节点"


class MainlineState(StrEnum):
    WATCH = "观察"
    CANDIDATE = "候选主线"
    HIGH_QUALITY = "高质量候选"
    CONFIRMED = "核心主线确认"
    WEAKENING = "主线走弱"
    RETREAT = "主线退潮"


class Role(StrEnum):
    SPACE_DRAGON = "空间龙/总龙头"
    PIONEER = "先锋"
    CORE_MIDCAP = "核心中军"
    OLD_DRAGON = "题材老龙"
    CROSS_CYCLE = "穿越龙"
    SUPPLEMENT = "补涨龙"
    MID_LEVEL = "中位股"
    FOLLOWER = "跟风"
    EXCLUDE = "杂毛/排除"
    PENDING = "待确认"


class CandidateStatus(StrEnum):
    WATCH = "观察"
    WAIT_CONFIRM = "等待确认"
    MODE_READY = "模式条件成立"
    RISK_REJECTED = "风险否决"
    INVALIDATED = "逻辑失效"
    EXIT = "退出"


HARD_RISKS = {
    "RETREAT_MARKET", "FISH_TAIL", "FOLLOWER", "FAKE_BREAKOUT",
    "NO_SUPPORT", "ANCHOR_BROKEN", "MODE_OUTSIDE", "NO_STOP",
    "RISK_REWARD_FAIL", "CYCLE_UNCONFIRMED", "CLIMAX_NO_CHASE",
    "ICE_NO_NEW_STARTER",
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _yes(value: Any) -> bool:
    return value is True


def _tri_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _tri_gte(value: Any, threshold: float) -> bool | None:
    number = _num(value)
    return None if number is None else number >= threshold


def _tri_lte(value: Any, threshold: float) -> bool | None:
    number = _num(value)
    return None if number is None else number <= threshold


def _tri_all(values: Iterable[bool | None]) -> bool | None:
    values = list(values)
    if any(value is False for value in values):
        return False
    if any(value is None for value in values):
        return None
    return True


def _tri_any(values: Iterable[bool | None]) -> bool | None:
    values = list(values)
    if any(value is True for value in values):
        return True
    if any(value is None for value in values):
        return None
    return False


def _tri_not(value: bool | None) -> bool | None:
    return None if value is None else not value


def _quality(evidence_rows: list[dict[str, Any]]) -> dict[str, Any]:
    missing = [row["rule_name"] for row in evidence_rows if row["passed"] is None]
    known = len(evidence_rows) - len(missing)
    if known == 0:
        status = "UNKNOWN"
    elif missing:
        status = "PARTIAL"
    else:
        status = "COMPLETE"
    return {
        "status": status,
        "complete": not missing,
        "known_rules": known,
        "total_rules": len(evidence_rows),
        "missing": missing,
    }


def _closed(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").lower()
    return row.get("exit_date") is not None or row.get("closed") is True or status in {"closed", "close", "已平仓"}


def _date_key(row: dict[str, Any]) -> str:
    return str(row.get("exit_date") or row.get("entry_date") or row.get("trade_id") or "")


def evidence(rule_id: str, name: str, required: str, actual: Any, passed: bool | None, source: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_name": name,
        "required": required,
        "actual": actual,
        "passed": passed,
        "source": f"野人哥交易系统笔录 / {source}",
    }


class MarketCycleEngine:
    ACTIONS = {
        Cycle.ICE: (["空仓观望", "极轻仓试错新题材"], ["接力", "重仓"], "0或≤5%"),
        Cycle.START: (["先锋试错", "潜在龙试错", "老龙右侧确认"], ["冷门孤立股", "满仓"], "5%~10%"),
        Cycle.MAIN_RISE: (["真龙首分歧", "弱转强", "核心中军趋势回踩"], ["后排跟风", "模式外随手单"], "20%~40%"),
        Cycle.CLIMAX: (["持有最强", "去弱留强", "分批减仓"], ["追高", "缩量加速中位"], "逐步减至20%~30%"),
        Cycle.RETREAT: (["清仓", "空仓等待新冰点"], ["新开仓", "低吸", "打板"], "0"),
    }
    UNKNOWN_ACTIONS = (["观察", "等待完整周期事实"], ["新开仓", "追涨", "重仓"], "0")

    def detect(self, market: dict[str, Any]) -> dict[str, Any]:
        height = _num(market.get("max_limit_height"))
        down = _num(market.get("limit_down_count"))
        nuclear = _num(market.get("nuclear_button_count"))
        first_boards = _num(market.get("new_theme_first_boards"))
        retreat = _tri_all([
            _tri_bool(market.get("leader_nuked")),
            _tri_any([_tri_bool(market.get("mid_level_limit_down_spread")), _tri_gte(down, 10)]),
        ])
        over_consensus = _tri_all([
            _tri_gte(market.get("one_word_limit_count"), 3),
            _tri_bool(market.get("leader_volume_acceleration")),
            _tri_bool(market.get("rear_all_red")),
        ])
        main_rise = _tri_all([
            _tri_gte(height, 4),
            _tri_bool(market.get("ladder_complete")),
            _tri_bool(market.get("core_midcap_support")),
        ])
        start = _tri_all([
            _tri_gte(first_boards, 2),
            _tri_any([_tri_bool(market.get("limit_down_decreasing")), _tri_lte(down, 9)]),
        ])
        ice = _tri_all([
            _tri_lte(height, 3),
            _tri_any([
                _tri_gte(down, 10),
                _tri_gte(nuclear, 2),
                _tri_gte(market.get("yesterday_limit_loss_count"), 3),
            ]),
        ])

        if retreat is True:
            cycle, node = Cycle.RETREAT.value, CycleNode.RETREAT.value
        elif over_consensus is True:
            cycle, node = Cycle.CLIMAX.value, CycleNode.NON_KEY.value
        elif main_rise is True:
            cycle = Cycle.MAIN_RISE.value
            node = CycleNode.FIRST_DIVERGENCE.value if _yes(market.get("first_divergence")) else CycleNode.NON_KEY.value
        elif start is True:
            cycle, node = Cycle.START.value, CycleNode.START.value
        elif ice is True:
            cycle = Cycle.ICE.value
            node = CycleNode.ICE_TURN.value if _yes(market.get("limit_down_decreasing")) and first_boards is not None and first_boards > 0 else CycleNode.NON_KEY.value
        else:
            cycle, node = "待确认", "待确认"

        facts = [
            evidence("WM_CYCLE_HEIGHT", "连板高度", "主升需≥4板；冰点需≤3板", height, _tri_any([_tri_gte(height, 4), _tri_lte(height, 3)]), "情绪周期"),
            evidence("WM_CYCLE_DOWN", "跌停数量", "冰点约≥10~20家或明确收敛", down, _tri_any([_tri_gte(down, 10), _tri_lte(down, 9)]), "情绪周期"),
            evidence("WM_CYCLE_LADDER", "板块梯队", "主升需完整", market.get("ladder_complete"), _tri_bool(market.get("ladder_complete")), "情绪周期"),
            evidence("WM_CYCLE_LEADER", "空间龙状态", "退潮检查被核", market.get("leader_nuked"), None if market.get("leader_nuked") is None else not retreat, "情绪周期"),
            evidence("WM_CYCLE_NUCLEAR", "核按钮/昨日亏钱效应", "冰点辅助证据", {"nuclear": nuclear, "yesterday_limit_loss_count": _num(market.get("yesterday_limit_loss_count"))}, _tri_any([_tri_gte(nuclear, 2), _tri_gte(market.get("yesterday_limit_loss_count"), 3)]), "情绪周期"),
        ]
        confirmed = cycle != "待确认"
        allowed, forbidden, position = self.ACTIONS.get(cycle, self.UNKNOWN_ACTIONS)
        return {
            "cycle": cycle,
            "cycle_node": node,
            "confirmed": confirmed,
            "cycle_confirmed": confirmed,
            "allowed_actions": allowed,
            "forbidden_actions": forbidden,
            "position_range": position,
            "evidence": facts,
            "data_quality": _quality(facts),
        }


class MainlineEngine:
    def evaluate(self, theme: dict[str, Any]) -> dict[str, Any]:
        limit_count = _num(theme.get("limit_up_count"))
        height = _num(theme.get("max_limit_height"))
        step1 = _tri_gte(limit_count, 3)
        step2 = _tri_all([
            _tri_gte(height, 2),
            _tri_bool(theme.get("has_pioneer")),
            _tri_bool(theme.get("has_core_midcap")),
        ])
        step3 = _tri_bool(theme.get("old_dragon_active"))
        step4 = _tri_all([_tri_bool(theme.get(key)) for key in ("leader_premium", "support_promotion", "core_midcap_stable")])
        step5 = _tri_any([_tri_bool(theme.get(key)) for key in ("fund_return", "reversal", "supplement_started", "resists_market_drop")])
        if _yes(theme.get("retreat")):
            state = MainlineState.RETREAT
        elif _yes(theme.get("weakening")):
            state = MainlineState.WEAKENING
        elif step1 is True and step2 is True and step4 is True:
            state = MainlineState.CONFIRMED
        elif step1 is True and step2 is True:
            state = MainlineState.HIGH_QUALITY
        elif step1 is True:
            state = MainlineState.CANDIDATE
        else:
            state = MainlineState.WATCH
        checks = [
            evidence("WM_MAINLINE_STEP1", "同题材涨停潮", "≥3~5只", limit_count, step1, "主线五步法/Step1"),
            evidence("WM_MAINLINE_STEP2", "梯队完整", "空间龙≥2~3板+先锋+中军", {"height": height, "pioneer": theme.get("has_pioneer"), "midcap": theme.get("has_core_midcap")}, step2, "主线五步法/Step2"),
            evidence("WM_MAINLINE_STEP3", "老龙异动", "底部堆量/首板/放量突破", theme.get("old_dragon_active"), step3, "主线五步法/Step3"),
            evidence("WM_MAINLINE_STEP4", "次日溢价验证", "龙头溢价+助攻晋级+中军稳定", {key: theme.get(key) for key in ("leader_premium", "support_promotion", "core_midcap_stable")}, step4, "主线五步法/Step4"),
            evidence("WM_MAINLINE_STEP5", "盘中动态强化", "回流/反包/补涨/抗跌", {key: theme.get(key) for key in ("fund_return", "reversal", "supplement_started", "resists_market_drop")}, step5, "主线五步法/Step5"),
        ]
        return {
            "state": state.value,
            "confirmed": state is MainlineState.CONFIRMED,
            "steps": {"step1": step1, "step2": step2, "step3": step3, "step4": step4, "step5": step5},
            "evidence": checks,
            "missing": [item["rule_name"] for item in checks if item["passed"] is None],
            "data_quality": _quality(checks),
        }


class StockRoleClassifier:
    def classify(self, stock: dict[str, Any], theme: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
        height = _num(stock.get("consecutive_limit_days"))
        max_height = _num(market.get("max_limit_height"))
        leader_height = _num(stock.get("leader_height"))
        if leader_height is None:
            leader_height = max_height
        cap = _num(stock.get("market_cap"))
        independent_leadership = _tri_bool(stock.get("independent_theme_leadership"))
        early_theme_start = _tri_bool(stock.get("early_theme_start"))
        space = (
            height is not None
            and max_height is not None
            and height >= max(max_height, 2)
            and _yes(stock.get("theme_linkage"))
            and _yes(stock.get("emotion_benchmark"))
            and independent_leadership is True
            and early_theme_start is True
        )
        supplement_facts = [
            _tri_bool(stock.get("supplement_started_after_leader")),
            _tri_not(independent_leadership),
            None if leader_height is None else leader_height >= 4,
            None if height is None or leader_height is None else height <= leader_height * 0.7,
        ]
        supplement = _tri_all(supplement_facts)
        if _yes(stock.get("excluded")):
            role = Role.EXCLUDE
        elif _yes(stock.get("cross_cycle")):
            role = Role.CROSS_CYCLE
        elif space:
            role = Role.SPACE_DRAGON
        elif _yes(stock.get("old_dragon")):
            role = Role.OLD_DRAGON
        elif cap is not None and cap >= 10_000_000_000 and _yes(stock.get("trend_intact")):
            role = Role.CORE_MIDCAP
        elif supplement is True:
            role = Role.SUPPLEMENT
        elif _yes(stock.get("first_limit_pioneer")) and height == 1:
            role = Role.PIONEER
        elif height is not None and max_height is not None and height >= max(max_height, 2) and _yes(stock.get("theme_linkage")) and _yes(stock.get("emotion_benchmark")) and (independent_leadership is not True or early_theme_start is not True):
            role = Role.PENDING
        elif height is not None and height >= 2:
            role = Role.MID_LEVEL
        elif _yes(stock.get("theme_linkage")):
            role = Role.FOLLOWER
        else:
            role = Role.EXCLUDE
        timing = (
            early_theme_start
            if role is Role.SPACE_DRAGON
            else _tri_bool(stock.get("first_limit_pioneer"))
            if role is Role.PIONEER
            else _tri_bool(stock.get("supplement_started_after_leader"))
        )
        influence = _tri_bool(stock.get("theme_linkage"))
        height_rule = None if height is None or leader_height is None else (height >= leader_height if role is Role.SPACE_DRAGON else height <= leader_height * 0.7 if role is Role.SUPPLEMENT else height >= 0)
        divergence_support = _tri_bool(stock.get("first_divergence_support"))
        retreat_behavior = stock.get("retreat_behavior")
        checks = [
            evidence("WM_ROLE_HEIGHT", "周期连板身位", "最高或并列最高", {"actual": height, "market_max": max_height}, None if height is None or max_height is None else height >= max_height, "个股角色"),
            evidence("WM_ROLE_LINKAGE", "板块带动性", "龙头有跟随；补涨不得独立领涨", stock.get("theme_linkage"), influence, "个股角色"),
            evidence("WM_ROLE_LEADERSHIP", "独立领涨事实", "真龙需明确独立领涨；补涨需明确非独立领涨", {"value": stock.get("independent_theme_leadership"), "fact_basis": stock.get("fact_basis")}, independent_leadership if role is not Role.SUPPLEMENT else _tri_not(independent_leadership), "个股角色"),
            evidence("WM_ROLE_BENCHMARK", "情绪标杆", "资金认可", stock.get("emotion_benchmark"), _tri_bool(stock.get("emotion_benchmark")), "个股角色"),
            evidence("WM_ROLE_EARLY_START", "题材早期启动", "空间龙需明确处于题材最早一批启动", {"early_theme_start": stock.get("early_theme_start"), "fact_basis": stock.get("fact_basis")}, early_theme_start, "个股角色"),
            evidence("WM_ROLE_TIMING", "启动时点", "补涨须在真实龙头确立后启动；空间龙须是题材早期启动", {"stock_start_date": stock.get("stock_start_date"), "leader_established_date": stock.get("leader_established_date"), "early_theme_start": stock.get("early_theme_start"), "first_limit_pioneer": stock.get("first_limit_pioneer"), "supplement_started_after_leader": stock.get("supplement_started_after_leader"), "fact_basis": stock.get("fact_basis")}, timing, "个股角色"),
            evidence("WM_ROLE_HEIGHT_RATIO", "相对龙头身位", "补涨通常为真实龙头50%~70%板数的经验参考，不是收益目标", {"stock_height": height, "leader_height": leader_height, "ratio": round(height / leader_height, 3) if height is not None and leader_height not in (None, 0) else None, "fact_basis": stock.get("fact_basis")}, height_rule, "个股角色"),
            evidence("WM_ROLE_DIVERGENCE_SUPPORT", "首次分歧承接", "龙头承接较稳；补涨承接弱须如实记录", {"value": stock.get("first_divergence_support"), "fact_basis": stock.get("fact_basis")}, divergence_support, "个股角色"),
            evidence("WM_ROLE_RETREAT_BEHAVIOR", "退潮表现", "仅展示观测事实，不预判A杀或反弹", retreat_behavior, None if retreat_behavior is None else True, "个股角色"),
        ]
        comparison = [
            {"dimension": "启动时机", "leader": "题材启动最早/最早一批", "supplement": "真实龙头确立后低位启动", "actual": {"early_theme_start": stock.get("early_theme_start"), "stock_start_date": stock.get("stock_start_date"), "leader_established_date": stock.get("leader_established_date"), "supplement_started_after_leader": stock.get("supplement_started_after_leader")}},
            {"dimension": "板块影响力", "leader": "有跟随、价格领先", "supplement": "无/弱独立领涨，依附主线", "actual": {"theme_linkage": stock.get("theme_linkage"), "independent_theme_leadership": stock.get("independent_theme_leadership"), "fact_basis": stock.get("fact_basis")}},
            {"dimension": "身位高度", "leader": "真实最高/并列最高", "supplement": "通常为真龙50%~70%板数（经验参考，非收益目标）", "actual": {"stock_height": height, "leader_height": leader_height, "ratio": round(height / leader_height, 3) if height is not None and leader_height not in (None, 0) else None}},
            {"dimension": "首次分歧", "leader": "有承接、支持较强", "supplement": "承接弱/快速坍塌风险", "actual": {"first_divergence_support": stock.get("first_divergence_support"), "fact_basis": stock.get("fact_basis")}},
            {"dimension": "退潮表现", "leader": "横盘或A，取决于周期事实", "supplement": "A形态风险仅作观察，不作确定预言", "actual": {"retreat_behavior": retreat_behavior}},
        ]
        return {"role": role.value, "evidence": checks, "comparison": comparison, "fact_basis": stock.get("fact_basis"), "confidence_source": "rule_based"}


class IntradaySupportEngine:
    def evaluate(self, intraday: dict[str, Any], level2: dict[str, Any] | None = None) -> dict[str, Any]:
        level2 = level2 or {}
        summary = level2.get("summary") or level2
        quality = level2.get("data_quality") or summary.get("data_quality") or {}
        l2_available = level2.get("available") is True or summary.get("available") is True
        absorption = _num(((summary.get("absorption") or {}).get("buy") or {}).get("value"))
        bid_replenishment = _num(((summary.get("replenishment") or {}).get("bid") or {}).get("value"))
        obi = _num((summary.get("obi") or {}).get("value"))
        distribution = _num((summary.get("distribution") or {}).get("value"))
        price_positive = _tri_any([_tri_bool(intraday.get("two_pullbacks_hold")), _tri_bool(intraday.get("lows_rising"))])
        price_bad = _tri_all([_tri_bool(intraday.get("vertical_drop")), _tri_bool(intraday.get("down_volume_expands"))])
        price_support = False if price_bad is True else price_positive
        quality_status = str(quality.get("status") or "").upper() if isinstance(quality, dict) else str(quality or "").upper()
        pagination = None
        if isinstance(quality, dict):
            pagination = quality.get("pagination_complete", quality.get("pagination"))
        if pagination is None:
            pagination = level2.get("pagination_complete", summary.get("pagination_complete"))
        if pagination is None:
            pagination_complete = True
        elif isinstance(pagination, dict):
            pagination_complete = pagination.get("complete") is True or pagination.get("status") == "COMPLETE" or pagination.get("has_more") is False
        else:
            pagination_complete = pagination is True
        l2_complete = l2_available and quality_status == "COMPLETE" and pagination_complete and all(value is not None for value in (absorption, bid_replenishment, obi, distribution))
        l2_support = (
            True
            if l2_complete
            and absorption >= 65
            and bid_replenishment >= 35
            and obi >= -0.1
            and distribution < 75
            else (False if l2_complete else None)
        )
        l2_bad = l2_available and (
            (distribution is not None and distribution >= 75)
            or (obi is not None and obi <= -0.35)
            or (bid_replenishment is not None and bid_replenishment <= 15)
        )
        bad = price_bad is True or l2_bad is True
        if bad:
            state = "差/无承接"
        elif price_support is True and (l2_support is True or not l2_available):
            state = "良性"
        else:
            state = "待确认"
        return {
            "state": state, "level2_available": l2_available,
            "level2_provider": level2.get("provider") or "numcat",
            "level2_quality": quality,
            "evidence": [
                evidence("WM_SUPPORT_PRICE", "分时低点与回踩", "低点抬高/两次不破前低", {key: intraday.get(key) for key in ("two_pullbacks_hold", "lows_rising", "vertical_drop")}, price_support, "分时承接"),
                evidence("WM_SUPPORT_L2", "猫爪Level-2承接", "完整样本中买方吸收增强且盘口未显著偏空", {"absorption": absorption, "bid_replenishment": bid_replenishment, "obi": obi, "distribution": distribution}, l2_support if l2_available else None, "分时承接/Level-2"),
            ],
            "note": None if l2_complete else ("猫爪Level-2样本不完整，不能确认完整盘口承接。" if l2_available else "本次尚无猫爪Level-2样本，当前只使用分时价格和量能，等待按股同步完成。"),
        }


class SetupDetector:
    def detect(self, stock: dict[str, Any], cycle: str, mainline: str, role: str, support: str) -> dict[str, Any]:
        setups: list[dict[str, Any]] = []
        is_confirmed_mainline = mainline == MainlineState.CONFIRMED.value
        is_potential_mainline = mainline in {MainlineState.CANDIDATE.value, MainlineState.HIGH_QUALITY.value, MainlineState.CONFIRMED.value}
        height = _num(stock.get("consecutive_limit_days"))
        support_ok = False if support == "差/无承接" else (True if support == "良性" else None)
        first_divergence = is_confirmed_mainline and cycle == Cycle.MAIN_RISE.value and role == Role.SPACE_DRAGON.value and height is not None and height >= 4 and _yes(stock.get("first_volume_divergence")) and support_ok is not False
        if first_divergence:
            setups.append({"type": "FIRST_DIVERGENCE", "name": "主升首分歧", "anchor_type": "分歧日最低", "anchor_price": _num(stock.get("divergence_low")), "confirmed": support_ok})
        auction_pct = _num(stock.get("auction_pct"))
        core_role = role in {Role.SPACE_DRAGON.value, Role.CORE_MIDCAP.value, Role.OLD_DRAGON.value, Role.CROSS_CYCLE.value}
        weak_to_strong = (
            cycle == Cycle.MAIN_RISE.value
            and is_confirmed_mainline
            and core_role
            and _yes(stock.get("yesterday_divergence"))
            and auction_pct is not None
            and 2 <= auction_pct <= 5
            and _yes(stock.get("auction_volume_strength"))
            and _yes(stock.get("open_volume_attack"))
            and _num(stock.get("intraday_anchor")) is not None
        )
        if weak_to_strong:
            setups.append({"type": "WEAK_TO_STRONG", "name": "弱转强", "anchor_type": "竞价/开盘承接低点", "anchor_price": _num(stock.get("intraday_anchor")), "confirmed": True})
        ma20 = _num(stock.get("ma20")); close = _num(stock.get("close_price")); low = _num(stock.get("low_price"))
        swing = _yes(stock.get("fundamentals_clear")) and _yes(stock.get("weekly_monthly_bottom")) and _yes(stock.get("ma20_turning_up")) and (_yes(stock.get("ma5_cross_ma10")) or _yes(stock.get("ma5_near_cross")))
        pullback = swing and _yes(stock.get("pullback_ma10_ma20")) and _yes(stock.get("volume_contract")) and _yes(stock.get("stabilizing_candle"))
        if swing:
            setups.append({"type": "SWING_PULLBACK" if pullback else "SWING_BREAKOUT", "name": "波段有效买点", "anchor_type": "20日线", "anchor_price": ma20, "confirmed": pullback or _yes(stock.get("bottom_volume_breakout"))})
        drawdown = _num(stock.get("drawdown_pct"))
        bpoint = drawdown is not None and drawdown <= -15 and _yes(stock.get("extreme_low_volume")) and _yes(stock.get("support_recovered"))
        if bpoint:
            setups.append({"type": "B_POINT", "name": "多空博弈B点", "anchor_type": "B点当日最低-0.5%", "anchor_price": round((low or 0) * 0.995, 3) if low else None, "confirmed": _yes(stock.get("reversal_confirmed"))})
        limit_pullback = is_potential_mainline and cycle not in {"待确认", Cycle.CLIMAX.value, Cycle.RETREAT.value} and role in {Role.PIONEER.value, Role.SPACE_DRAGON.value, Role.OLD_DRAGON.value} and _yes(stock.get("recent_solid_limit")) and _yes(stock.get("volume_contract")) and _yes(stock.get("holds_limit_candle_half")) and _yes(stock.get("stabilizing_candle"))
        if limit_pullback:
            setups.append({"type": "LIMIT_PULLBACK", "name": "涨停缩量回踩", "anchor_type": "涨停阳线半分位", "anchor_price": _num(stock.get("limit_candle_half")), "confirmed": _yes(stock.get("renewed_volume_breakout"))})
        if not setups:
            return {"type": "NONE", "name": "未形成模式内买点", "anchor_type": None, "anchor_price": None, "confirmed": False, "all": []}
        selected = next((item for item in setups if item["confirmed"]), setups[0])
        return {**selected, "all": setups}


class ExpectationEngine:
    def evaluate(self, stock: dict[str, Any]) -> dict[str, Any]:
        auction = _num(stock.get("auction_pct"))
        if _yes(stock.get("yesterday_divergence")) and auction is not None and auction >= 3 and _yes(stock.get("auction_volume_strength")):
            return {"state": "超预期", "confirmed": True, "action": "持股或等待模式内盘口确认"}
        under = _tri_all([_tri_bool(stock.get("yesterday_strong")), _tri_any([_tri_bool(stock.get("open_low")), _tri_bool(stock.get("no_support"))])])
        if under is True:
            return {"state": "不及预期", "confirmed": True, "action": "竞价或开盘优先处理，绝不补仓"}
        expected = _tri_all([_tri_bool(stock.get("yesterday_strong")), _tri_bool(stock.get("open_low"))])
        if expected is True:
            return {"state": "符合预期", "confirmed": True, "action": "观察锚点和分时，持股或分批止盈"}
        return {"state": "待确认", "confirmed": False, "action": "等待昨日状态、竞价和开盘事实，不预设符合预期"}


class RiskGate:
    def evaluate(self, market: dict[str, Any], stock: dict[str, Any], role: str, setup: dict[str, Any], support: str, risk_reward: float | None) -> dict[str, Any]:
        flags: list[str] = []
        if market.get("cycle") == Cycle.RETREAT.value: flags.append("RETREAT_MARKET")
        if market.get("confirmed") is False or market.get("cycle") == "待确认": flags.append("CYCLE_UNCONFIRMED")
        if market.get("cycle") == Cycle.CLIMAX.value: flags.append("CLIMAX_NO_CHASE")
        over_consensus = _tri_all([
            _tri_gte(market.get("one_word_limit_count"), 3),
            _tri_bool(market.get("leader_volume_acceleration")),
            _tri_bool(market.get("rear_all_red")),
        ]) is True
        fish_tail = over_consensus and (_yes(market.get("leader_broken")) or _yes(market.get("cold_rear_supplement")))
        if fish_tail: flags.append("FISH_TAIL")
        elif over_consensus: flags.append("OVER_CONSENSUS")
        if role in {Role.FOLLOWER.value, Role.EXCLUDE.value}: flags.append("FOLLOWER")
        if _yes(stock.get("fake_breakout")): flags.append("FAKE_BREAKOUT")
        if support == "差/无承接": flags.append("NO_SUPPORT")
        if _yes(stock.get("high_volume_stall")): flags.append("HIGH_VOLUME_STALL")
        if _yes(stock.get("anchor_broken")): flags.append("ANCHOR_BROKEN")
        if setup.get("type") == "NONE": flags.append("MODE_OUTSIDE")
        if setup.get("anchor_price") is None: flags.append("NO_STOP")
        if market.get("cycle") == Cycle.ICE.value and setup.get("type") != "NONE" and not (_yes(stock.get("new_starter")) or _yes(stock.get("first_limit_pioneer"))): flags.append("ICE_NO_NEW_STARTER")
        if risk_reward is not None and risk_reward < 2: flags.append("RISK_REWARD_FAIL")
        return {"flags": flags, "hard_reject": any(flag in HARD_RISKS for flag in flags)}


class PositionEngine:
    def suggest(self, cycle: str, setup: str, consecutive_stops: int = 0, drawdown_pct: float = 0) -> dict[str, Any]:
        if cycle == Cycle.RETREAT.value or drawdown_pct <= -10:
            return {"mode": "空仓", "range": "0", "reason": "退潮或阶段回撤触发"}
        if cycle == Cycle.ICE.value:
            return {"mode": "冰点试错", "range": "0~5%", "reason": "冰点只允许新题材启动事实下的极轻仓试错"}
        if consecutive_stops >= 2:
            return {"mode": "试错/复盘", "range": "0~5%", "reason": "连续止损2~3笔，只提示降仓并继续观察行情"}
        if cycle == Cycle.MAIN_RISE.value and setup in {"FIRST_DIVERGENCE", "WEAK_TO_STRONG"}:
            return {"mode": "确认仓", "range": "20%~30%", "single_stock_cap": "≤50%", "reason": "主线主升的模式内确认"}
        if cycle == Cycle.CLIMAX.value:
            return {"mode": "逐步减仓", "range": "20%~30%", "reason": "一致性过高"}
        return {"mode": "试错仓", "range": "5%~10%", "reason": "信号仍需验证"}

    @staticmethod
    def observation(reference: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            "mode": "观察",
            "range": "0",
            "reference_range": reference.get("range"),
            "allocation_type": "observation",
            "opening_recommendation": False,
            "reason": reason,
        }


class ExitEngine:
    def create(self, setup: dict[str, Any], entry_price: float | None = None) -> dict[str, Any]:
        price_stop = round(entry_price * 0.95, 3) if entry_price else None
        return {
            "anchor_type": setup.get("anchor_type"), "anchor_price": setup.get("anchor_price"),
            "price_stop": price_stop, "max_loss_rule": "-5%~-7%",
            "logic_stop": "开仓逻辑消失无条件退出", "cycle_stop": "市场进入退潮则退出",
            "take_profit": "盈利后将防守上移至成本价上方",
        }


class ClassicSetupEngine:
    def evaluate(self, stock: dict[str, Any]) -> list[dict[str, Any]]:
        explicit = stock.get("classic_setups")
        if isinstance(explicit, list):
            return [item for item in explicit if isinstance(item, dict) and item.get("id")]
        result = []
        if _yes(stock.get("classic_520_confirmed")):
            result.append({"id": "WM_CLASSIC_520", "name": "520战法", "status": "等待次日收阳且高于金叉日确认", "stop": "有效破5日减半，破20日清仓"})
        if _yes(stock.get("classic_t_confirmed")):
            result.append({"id": "WM_CLASSIC_T", "name": "老太太扶楼梯", "status": "仅主动工具观察", "risk": "左侧交易，单边下跌或流动性枯竭时风险极高"})
        if _yes(stock.get("classic_75a_confirmed")):
            result.append({"id": "WM_CLASSIC_75A", "name": "圆弧底75A", "status": "突破后等待缩量回踩确认", "stop": "跌破圆弧底最低或75日线"})
        return result


@dataclass
class WildmanRuleCore:
    market_cycle: MarketCycleEngine = MarketCycleEngine()
    mainline: MainlineEngine = MainlineEngine()
    role: StockRoleClassifier = StockRoleClassifier()
    support: IntradaySupportEngine = IntradaySupportEngine()
    setup: SetupDetector = SetupDetector()
    expectation: ExpectationEngine = ExpectationEngine()
    risk: RiskGate = RiskGate()
    position: PositionEngine = PositionEngine()
    exit: ExitEngine = ExitEngine()
    classic: ClassicSetupEngine = ClassicSetupEngine()

    @staticmethod
    def risk_reward(entry: Any, target: Any, stop: Any) -> float | None:
        entry_value, target_value, stop_value = _num(entry), _num(target), _num(stop)
        if entry_value is None or target_value is None or stop_value is None or entry_value <= stop_value:
            return None
        return round(max(target_value - entry_value, 0) / (entry_value - stop_value), 2)

    def evaluate(self, market: dict[str, Any], theme: dict[str, Any], stock: dict[str, Any], intraday: dict[str, Any] | None = None, level2: dict[str, Any] | None = None, account: dict[str, Any] | None = None) -> dict[str, Any]:
        cycle = self.market_cycle.detect(market)
        market_with_cycle = {**market, "cycle": cycle["cycle"], "confirmed": cycle["confirmed"]}
        mainline = self.mainline.evaluate(theme)
        role = self.role.classify(stock, theme, market)
        support = self.support.evaluate(intraday or {}, level2)
        setup = self.setup.detect(stock, cycle["cycle"], mainline["state"], role["role"], support["state"])
        rr = self.risk_reward(stock.get("close_price"), stock.get("pressure_price"), setup.get("anchor_price"))
        risk = self.risk.evaluate(market_with_cycle, stock, role["role"], setup, support["state"], rr)
        stage_ready = cycle["confirmed"] and cycle["cycle"] not in {Cycle.RETREAT.value, Cycle.CLIMAX.value, "待确认"}
        if setup["type"] in {"FIRST_DIVERGENCE", "WEAK_TO_STRONG"}:
            stage_ready = stage_ready and cycle["cycle"] == Cycle.MAIN_RISE.value and mainline["state"] == MainlineState.CONFIRMED.value
        elif setup["type"] == "LIMIT_PULLBACK":
            stage_ready = stage_ready and mainline["state"] in {MainlineState.CANDIDATE.value, MainlineState.HIGH_QUALITY.value, MainlineState.CONFIRMED.value}
        setup_ready = setup.get("confirmed") is True and setup.get("anchor_price") is not None and rr is not None and rr >= 2
        if risk["hard_reject"]:
            status = CandidateStatus.RISK_REJECTED
        elif setup["type"] == "NONE":
            status = CandidateStatus.WATCH
        elif stage_ready and setup_ready:
            status = CandidateStatus.MODE_READY
        else:
            status = CandidateStatus.WAIT_CONFIRM
        account = account or {}
        consecutive_stops = int(_num(account.get("consecutive_stops")) or 0)
        # The service has no aggregated equity ledger; only a separately verified
        # drawdown fact may affect the advisory position range.
        drawdown_pct = _num(account.get("verified_drawdown_pct")) or 0
        reference_position = self.position.suggest(cycle["cycle"], setup["type"], consecutive_stops, drawdown_pct)
        if risk["hard_reject"] or status is CandidateStatus.WATCH or not cycle["confirmed"]:
            reason = "风险否决，仓位仅作参考，不给出开仓建议" if risk["hard_reject"] else "未形成可执行模式，仓位仅作参考，不给出开仓建议"
            position = self.position.observation(reference_position, reason)
        else:
            position = {
                **reference_position,
                "reference_range": reference_position.get("range"),
                "allocation_type": "reference",
                "opening_recommendation": status is CandidateStatus.MODE_READY,
            }
        exit_plan = self.exit.create(setup, _num(stock.get("close_price")))
        passed = [*cycle["evidence"], *mainline["evidence"], *role["evidence"], *support["evidence"]]
        chain = [
            {"stage": "市场", "result": cycle["cycle"], "detail": cycle["cycle_node"]},
            {"stage": "板块", "result": mainline["state"], "detail": theme.get("theme_name") or "题材待确认"},
            {"stage": "个股", "result": role["role"], "detail": stock.get("name") or stock.get("symbol")},
            {"stage": "买点", "result": setup["name"], "detail": "已确认" if setup.get("confirmed") else "等待确认"},
            {"stage": "盘口", "result": support["state"], "detail": "猫爪Level-2" if support["level2_available"] else "分时价量"},
            {"stage": "风险", "result": "风险否决" if risk["hard_reject"] else "未触发硬否决", "detail": "、".join(risk["flags"]) or "无"},
            {"stage": "盈亏比", "result": f"{rr}:1" if rr is not None else "等待压力位/锚点", "detail": "最低2:1，目标3:1"},
            {"stage": "结论", "result": status.value, "detail": position["range"], "opening_recommendation": position["opening_recommendation"]},
        ]
        return {
            "rule_version": RULE_VERSION, "cycle": cycle, "mainline": mainline,
            "role": role, "support": support, "setup": setup,
            "expectation": self.expectation.evaluate(stock), "risk": risk,
            "risk_reward": rr, "position": position, "exit_plan": exit_plan,
            "candidate_status": status.value, "explain_chain": chain,
            "passed_rules": [item for item in passed if item.get("passed") is True],
            "failed_rules": [item for item in passed if item.get("passed") is False],
            "waiting_rules": [item for item in passed if item.get("passed") is None],
            "classic_setups": self.classic.evaluate(stock),
        }


def review_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    closed = sorted((row for row in items if _closed(row)), key=_date_key)
    measurable = [row for row in closed if _num(row.get("pnl_pct")) is not None]
    inside = [row for row in measurable if row.get("mode_inside") is True]
    outside = [row for row in measurable if row.get("mode_inside") is not True]
    inside_pnl = [_num(row.get("pnl_pct")) for row in inside]
    all_pnl = [_num(row.get("pnl_pct")) for row in measurable]
    wins = [pnl for pnl in inside_pnl if pnl is not None and pnl > 0]
    gains = [pnl for pnl in inside_pnl if pnl is not None and pnl > 0]
    losses = [abs(pnl) for pnl in inside_pnl if pnl is not None and pnl < 0]
    all_losses = [abs(pnl) for pnl in all_pnl if pnl is not None and pnl < 0]
    outside_losses = [abs(_num(row.get("pnl_pct"))) for row in outside if _num(row.get("pnl_pct")) is not None and _num(row.get("pnl_pct")) < 0]

    def max_losses(sequence: list[dict[str, Any]]) -> int:
        longest = current = 0
        for row in sequence:
            pnl = _num(row.get("pnl_pct"))
            if pnl is not None and pnl < 0:
                current += 1
                longest = max(longest, current)
            else:
                current = 0
        return longest

    per_setup: dict[str, Any] = {}
    setup_names = sorted({str(row.get("setup_type") or "未分类") for row in measurable})
    for setup_name in setup_names:
        setup_rows = [row for row in measurable if str(row.get("setup_type") or "未分类") == setup_name]
        setup_pnl = [_num(row.get("pnl_pct")) for row in setup_rows]
        setup_wins = [pnl for pnl in setup_pnl if pnl is not None and pnl > 0]
        setup_losses = [abs(pnl) for pnl in setup_pnl if pnl is not None and pnl < 0]
        sample = len(setup_pnl)
        expectancy = round(sum(setup_pnl) / sample, 2) if sample else None
        guidance = "样本不足，继续记录，不阻断扫描" if sample < 10 else ("负期望，冻结该子模式复审，不阻断扫描" if expectancy is not None and expectancy < 0 else "维持模式并继续复盘")
        per_setup[setup_name] = {
            "sample_count": sample,
            "win_rate": round(len(setup_wins) / sample * 100, 1) if sample else None,
            "expectancy": expectancy,
            "profit_loss_ratio": round((sum(setup_wins) / len(setup_wins)) / (sum(setup_losses) / len(setup_losses)), 2) if setup_wins and setup_losses else None,
            "max_consecutive_losses": max_losses(setup_rows),
            "guidance": guidance,
        }
    return {
        "total": len(items), "closed_total": len(closed), "open_trades": len(items) - len(closed), "sample_count": len(measurable),
        "mode_inside": len([row for row in closed if row.get("mode_inside") is True]), "mode_outside": len([row for row in closed if row.get("mode_inside") is not True]),
        "mode_outside_ratio": round(len(outside) / len(measurable) * 100, 1) if measurable else None,
        "inside_win_rate": round(len(wins) / len(inside) * 100, 1) if inside else None,
        "profit_loss_ratio": round((sum(gains) / len(gains)) / (sum(losses) / len(losses)), 2) if gains and losses else None,
        "max_consecutive_losses": max_losses(measurable),
        "outside_loss_share": round(sum(outside_losses) / sum(all_losses) * 100, 1) if all_losses else None,
        "expectancy": round(sum(all_pnl) / len(all_pnl), 2) if all_pnl else None,
        "per_setup": per_setup,
        "violations": sorted({tag for row in closed for tag in (row.get("violation_tags") or [])}),
    }
