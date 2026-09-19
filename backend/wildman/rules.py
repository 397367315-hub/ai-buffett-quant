"""Deterministic Wildman V1.0 rules.

The rule core only consumes structured facts and never calls AI or external
data sources. Unknown facts remain waiting evidence. Rule changes require a
new version so historical decisions remain replayable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable


RULE_VERSION = "WM_RULE_CORE_V1_0"


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
    "RISK_REWARD_FAIL",
}


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _yes(value: Any) -> bool:
    return value is True


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
        Cycle.ICE: (["空仓观望", "极轻仓试错新题材"], ["接力", "重仓"] , "0或≤5%"),
        Cycle.START: (["先锋试错", "潜在龙试错", "老龙右侧确认"], ["冷门孤立股", "满仓"] , "5%~10%"),
        Cycle.MAIN_RISE: (["真龙首分歧", "弱转强", "核心中军趋势回踩"], ["后排跟风", "模式外随手单"] , "20%~40%"),
        Cycle.CLIMAX: (["持有最强", "去弱留强", "分批减仓"], ["追高", "缩量加速中位"] , "逐步降至20%~30%"),
        Cycle.RETREAT: (["清仓", "空仓等待新冰点"], ["新开仓", "低吸", "打板"] , "0"),
    }

    def detect(self, market: dict[str, Any]) -> dict[str, Any]:
        height = int(_num(market.get("max_limit_height")) or 0)
        up = int(_num(market.get("limit_up_count")) or 0)
        down = int(_num(market.get("limit_down_count")) or 0)
        nuclear = int(_num(market.get("nuclear_button_count")) or 0)
        first_boards = int(_num(market.get("new_theme_first_boards")) or 0)
        retreat = _yes(market.get("leader_nuked")) and (_yes(market.get("mid_level_limit_down_spread")) or down >= 10)
        over_consensus = int(_num(market.get("one_word_limit_count")) or 0) >= 3 and _yes(market.get("leader_volume_acceleration")) and _yes(market.get("rear_all_red"))
        main_rise = height >= 4 and _yes(market.get("ladder_complete")) and _yes(market.get("core_midcap_support"))
        start = first_boards >= 2 and (_yes(market.get("limit_down_decreasing")) or down < 10)
        ice = height <= 3 and (down >= 10 or nuclear >= 2 or int(_num(market.get("yesterday_limit_loss_count")) or 0) >= 3)

        if retreat:
            cycle, node = Cycle.RETREAT, CycleNode.RETREAT
        elif over_consensus:
            cycle, node = Cycle.CLIMAX, CycleNode.NON_KEY
        elif main_rise:
            cycle = Cycle.MAIN_RISE
            node = CycleNode.FIRST_DIVERGENCE if _yes(market.get("first_divergence")) else CycleNode.NON_KEY
        elif start:
            cycle, node = Cycle.START, CycleNode.START
        elif ice:
            cycle = Cycle.ICE
            node = CycleNode.ICE_TURN if _yes(market.get("limit_down_decreasing")) and first_boards > 0 else CycleNode.NON_KEY
        else:
            # The five-state contract has no unknown enum. The least permissive
            # actionable state is retained and data quality blocks escalation.
            cycle, node = Cycle.ICE, CycleNode.NON_KEY

        allowed, forbidden, position = self.ACTIONS[cycle]
        facts = [
            evidence("WM_CYCLE_HEIGHT", "连板高度", "按阶段判定", height or None, height > 0 if height else None, "情绪周期"),
            evidence("WM_CYCLE_DOWN", "跌停数量", "冰点约≥10~20家", down, down >= 10, "情绪周期"),
            evidence("WM_CYCLE_LADDER", "板块梯队", "主升需完整", market.get("ladder_complete"), market.get("ladder_complete") if market.get("ladder_complete") is not None else None, "情绪周期"),
            evidence("WM_CYCLE_LEADER", "空间龙状态", "退潮检查被核", market.get("leader_nuked"), not retreat, "情绪周期"),
        ]
        known = sum(item["passed"] is not None for item in facts)
        return {
            "cycle": cycle.value, "cycle_node": node.value,
            "allowed_actions": allowed, "forbidden_actions": forbidden,
            "position_range": position, "evidence": facts,
            "data_quality": {"status": "COMPLETE" if known == len(facts) else "PARTIAL", "known_rules": known, "total_rules": len(facts)},
        }


class MainlineEngine:
    def evaluate(self, theme: dict[str, Any]) -> dict[str, Any]:
        limit_count = int(_num(theme.get("limit_up_count")) or 0)
        height = int(_num(theme.get("max_limit_height")) or 0)
        step1 = limit_count >= 3
        step2 = height >= 2 and _yes(theme.get("has_pioneer")) and _yes(theme.get("has_core_midcap"))
        step3 = theme.get("old_dragon_active") if theme.get("old_dragon_active") is not None else None
        step4 = all(_yes(theme.get(key)) for key in ("leader_premium", "support_promotion", "core_midcap_stable"))
        step4 = step4 if any(theme.get(key) is not None for key in ("leader_premium", "support_promotion", "core_midcap_stable")) else None
        step5 = any(_yes(theme.get(key)) for key in ("fund_return", "reversal", "supplement_started", "resists_market_drop"))
        step5 = step5 if any(theme.get(key) is not None for key in ("fund_return", "reversal", "supplement_started", "resists_market_drop")) else None
        if _yes(theme.get("retreat")):
            state = MainlineState.RETREAT
        elif _yes(theme.get("weakening")):
            state = MainlineState.WEAKENING
        elif step1 and step2 and step4:
            state = MainlineState.CONFIRMED
        elif step1 and step2:
            state = MainlineState.HIGH_QUALITY
        elif step1:
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
        return {"state": state.value, "steps": {"step1": step1, "step2": step2, "step3": step3, "step4": step4, "step5": step5}, "evidence": checks, "missing": [item["rule_name"] for item in checks if item["passed"] is None]}


class StockRoleClassifier:
    def classify(self, stock: dict[str, Any], theme: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
        height = int(_num(stock.get("consecutive_limit_days")) or 0)
        max_height = int(_num(market.get("max_limit_height")) or 0)
        cap = _num(stock.get("market_cap"))
        space = height >= max(max_height, 2) and _yes(stock.get("theme_linkage")) and _yes(stock.get("emotion_benchmark"))
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
        elif _yes(stock.get("first_limit_pioneer")):
            role = Role.PIONEER
        elif _yes(stock.get("supplement")):
            role = Role.SUPPLEMENT
        elif height >= 2:
            role = Role.MID_LEVEL
        elif _yes(stock.get("theme_linkage")):
            role = Role.FOLLOWER
        else:
            role = Role.EXCLUDE
        checks = [
            evidence("WM_ROLE_HEIGHT", "周期连板身位", "最高或并列最高", {"actual": height, "market_max": max_height}, height >= max_height if max_height else None, "个股角色"),
            evidence("WM_ROLE_LINKAGE", "板块带动性", "有板块助攻", stock.get("theme_linkage"), stock.get("theme_linkage"), "个股角色"),
            evidence("WM_ROLE_BENCHMARK", "情绪标杆", "资金认可", stock.get("emotion_benchmark"), stock.get("emotion_benchmark"), "个股角色"),
        ]
        return {"role": role.value, "evidence": checks, "confidence_source": "rule_based"}


class IntradaySupportEngine:
    def evaluate(self, intraday: dict[str, Any], level2: dict[str, Any] | None = None) -> dict[str, Any]:
        level2 = level2 or {}
        summary = level2.get("summary") or level2
        quality = level2.get("data_quality") or {}
        l2_available = bool(level2.get("available") or summary.get("available"))
        absorption = _num(((summary.get("absorption") or {}).get("buy") or {}).get("value"))
        bid_replenishment = _num(((summary.get("replenishment") or {}).get("bid") or {}).get("value"))
        obi = _num((summary.get("obi") or {}).get("value"))
        distribution = _num((summary.get("distribution") or {}).get("value"))
        price_support = (_yes(intraday.get("two_pullbacks_hold")) or _yes(intraday.get("lows_rising"))) and not _yes(intraday.get("vertical_drop"))
        l2_support = (
            l2_available
            and absorption is not None
            and absorption >= 65
            and (bid_replenishment is None or bid_replenishment >= 35)
            and (obi is None or obi >= -0.1)
        )
        l2_bad = l2_available and (
            (distribution is not None and distribution >= 75)
            or (obi is not None and obi <= -0.35)
            or (bid_replenishment is not None and bid_replenishment <= 15)
        )
        bad = _yes(intraday.get("vertical_drop")) and _yes(intraday.get("down_volume_expands")) or l2_bad
        if bad:
            state = "差/无承接"
        elif price_support and (l2_support or not l2_available):
            state = "良性"
        else:
            state = "待确认"
        return {
            "state": state, "level2_available": l2_available,
            "level2_provider": level2.get("provider") or "numcat",
            "level2_quality": quality,
            "evidence": [
                evidence("WM_SUPPORT_PRICE", "分时低点与回踩", "低点抬高/两次不破前低", {key: intraday.get(key) for key in ("two_pullbacks_hold", "lows_rising", "vertical_drop")}, price_support, "分时承接"),
                evidence("WM_SUPPORT_L2", "猫爪Level-2承接", "买方吸收增强且盘口未显著偏空", {"absorption": absorption, "bid_replenishment": bid_replenishment, "obi": obi, "distribution": distribution}, l2_support if l2_available else None, "分时承接/Level-2"),
            ],
            "note": None if l2_available else "本次尚无猫爪Level-2样本，当前只使用分时价格和量能，等待按股同步完成。",
        }


class SetupDetector:
    def detect(self, stock: dict[str, Any], cycle: str, mainline: str, role: str, support: str) -> dict[str, Any]:
        setups: list[dict[str, Any]] = []
        is_mainline = mainline == MainlineState.CONFIRMED.value
        first_divergence = is_mainline and role == Role.SPACE_DRAGON.value and int(_num(stock.get("consecutive_limit_days")) or 0) >= 4 and _yes(stock.get("first_volume_divergence")) and support != "差/无承接"
        if first_divergence:
            setups.append({"type": "FIRST_DIVERGENCE", "name": "主升首分歧", "anchor_type": "分歧日最低", "anchor_price": _num(stock.get("divergence_low")), "confirmed": support == "良性"})
        auction_pct = _num(stock.get("auction_pct"))
        weak_to_strong = _yes(stock.get("yesterday_divergence")) and auction_pct is not None and 2 <= auction_pct <= 5 and _yes(stock.get("auction_volume_strength")) and _yes(stock.get("open_volume_attack"))
        if weak_to_strong:
            setups.append({"type": "WEAK_TO_STRONG", "name": "弱转强", "anchor_type": "竞价/开盘承接低点", "anchor_price": _num(stock.get("intraday_anchor")), "confirmed": True})
        ma20 = _num(stock.get("ma20")); close = _num(stock.get("close_price")); low = _num(stock.get("low_price"))
        swing = _yes(stock.get("weekly_monthly_bottom")) and _yes(stock.get("ma20_turning_up")) and (_yes(stock.get("ma5_cross_ma10")) or _yes(stock.get("ma5_near_cross")))
        pullback = swing and _yes(stock.get("pullback_ma10_ma20")) and _yes(stock.get("volume_contract")) and _yes(stock.get("stabilizing_candle"))
        if swing:
            setups.append({"type": "SWING_PULLBACK" if pullback else "SWING_BREAKOUT", "name": "波段有效买点", "anchor_type": "20日线", "anchor_price": ma20, "confirmed": pullback or _yes(stock.get("bottom_volume_breakout"))})
        drawdown = _num(stock.get("drawdown_pct"))
        bpoint = drawdown is not None and drawdown <= -15 and _yes(stock.get("extreme_low_volume")) and _yes(stock.get("support_recovered"))
        if bpoint:
            setups.append({"type": "B_POINT", "name": "多空博弈B点", "anchor_type": "B点当日最低-0.5%", "anchor_price": round((low or 0) * 0.995, 3) if low else None, "confirmed": _yes(stock.get("reversal_confirmed"))})
        limit_pullback = is_mainline and role in {Role.PIONEER.value, Role.SPACE_DRAGON.value, Role.OLD_DRAGON.value} and _yes(stock.get("recent_solid_limit")) and _yes(stock.get("volume_contract")) and _yes(stock.get("holds_limit_candle_half")) and _yes(stock.get("stabilizing_candle"))
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
            return {"state": "超预期", "action": "持股或等待模式内盘口确认"}
        if _yes(stock.get("yesterday_strong")) and (_yes(stock.get("open_low")) or _yes(stock.get("no_support"))):
            return {"state": "不及预期", "action": "竞价或开盘优先处理，绝不补仓"}
        return {"state": "符合预期", "action": "观察锚点和分时，持股或分批止盈"}


class RiskGate:
    def evaluate(self, market: dict[str, Any], stock: dict[str, Any], role: str, setup: dict[str, Any], support: str, risk_reward: float | None) -> dict[str, Any]:
        flags: list[str] = []
        if market.get("cycle") == Cycle.RETREAT.value: flags.append("RETREAT_MARKET")
        over_consensus = int(_num(market.get("one_word_limit_count")) or 0) >= 3 and _yes(market.get("leader_volume_acceleration")) and _yes(market.get("rear_all_red"))
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
        if risk_reward is not None and risk_reward < 2: flags.append("RISK_REWARD_FAIL")
        return {"flags": flags, "hard_reject": any(flag in HARD_RISKS for flag in flags)}


class PositionEngine:
    def suggest(self, cycle: str, setup: str, consecutive_stops: int = 0, drawdown_pct: float = 0) -> dict[str, Any]:
        if cycle == Cycle.RETREAT.value or drawdown_pct <= -10:
            return {"mode": "空仓", "range": "0", "reason": "退潮或阶段回撤触发"}
        if consecutive_stops >= 2:
            return {"mode": "试错/复盘", "range": "0~5%", "reason": "连续止损2~3笔，只提示降仓并继续观察行情"}
        if cycle == Cycle.MAIN_RISE.value and setup in {"FIRST_DIVERGENCE", "WEAK_TO_STRONG"}:
            return {"mode": "确认仓", "range": "20%~30%", "single_stock_cap": "≤50%", "reason": "主线主升的模式内确认"}
        if cycle == Cycle.CLIMAX.value:
            return {"mode": "逐步减仓", "range": "20%~30%", "reason": "一致性过高"}
        return {"mode": "试错仓", "range": "5%~10%", "reason": "信号仍需验证"}


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
        result = []
        if _yes(stock.get("ma5_cross_ma20")) and (_yes(stock.get("ma20_turning_up")) or _yes(stock.get("ma20_flat"))) and _yes(stock.get("cross_volume_expand")):
            result.append({"id": "WM_CLASSIC_520", "name": "520战法", "status": "等待次日收阳且高于金叉日确认", "stop": "有效破5日减半，破20日清仓"})
        if (_num(stock.get("decline_days")) or 0) >= 5 or (_num(stock.get("drawdown_pct")) or 0) <= -10:
            result.append({"id": "WM_CLASSIC_T", "name": "老太太扶楼梯", "status": "仅主动工具观察", "risk": "左侧交易，单边下跌或流动性枯竭时风险极高"})
        if (_num(stock.get("arc_days")) or 0) >= 60 and _yes(stock.get("ma75_turning_up")) and _yes(stock.get("break_neckline")):
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
        market_with_cycle = {**market, "cycle": cycle["cycle"]}
        mainline = self.mainline.evaluate(theme)
        role = self.role.classify(stock, theme, market)
        support = self.support.evaluate(intraday or {}, level2)
        setup = self.setup.detect(stock, cycle["cycle"], mainline["state"], role["role"], support["state"])
        rr = self.risk_reward(stock.get("close_price"), stock.get("pressure_price"), setup.get("anchor_price"))
        risk = self.risk.evaluate(market_with_cycle, stock, role["role"], setup, support["state"], rr)
        if risk["hard_reject"]:
            status = CandidateStatus.RISK_REJECTED
        elif setup["type"] == "NONE":
            status = CandidateStatus.WATCH
        elif setup.get("confirmed") and rr is not None and rr >= 2:
            status = CandidateStatus.MODE_READY
        else:
            status = CandidateStatus.WAIT_CONFIRM
        account = account or {}
        position = self.position.suggest(cycle["cycle"], setup["type"], int(account.get("consecutive_stops") or 0), float(account.get("drawdown_pct") or 0))
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
            {"stage": "结论", "result": status.value, "detail": position["range"]},
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
    inside = [row for row in items if row.get("mode_inside")]
    outside = [row for row in items if not row.get("mode_inside")]
    wins = [row for row in inside if (_num(row.get("pnl_pct")) or 0) > 0]
    gains = [(_num(row.get("pnl_pct")) or 0) for row in inside if (_num(row.get("pnl_pct")) or 0) > 0]
    losses = [abs(_num(row.get("pnl_pct")) or 0) for row in inside if (_num(row.get("pnl_pct")) or 0) < 0]
    return {
        "total": len(items), "mode_inside": len(inside), "mode_outside": len(outside),
        "mode_outside_ratio": round(len(outside) / len(items) * 100, 1) if items else None,
        "inside_win_rate": round(len(wins) / len(inside) * 100, 1) if inside else None,
        "profit_loss_ratio": round((sum(gains) / len(gains)) / (sum(losses) / len(losses)), 2) if gains and losses else None,
        "violations": sorted({tag for row in items for tag in (row.get("violation_tags") or [])}),
    }
