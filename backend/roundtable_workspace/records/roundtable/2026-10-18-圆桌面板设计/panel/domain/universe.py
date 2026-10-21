"""股票池政策（DATA-005 / AC-079 / AC-080）。

候选池与市场环境分母是两个政策，必须分别显示名称、成员数与覆盖率：
  - universe_cn_mainboard_v1：沪深主板 A 股；排除创业、科创、北交、B 股、当日 ST/退市风险；
    市场分类未知的不纳入；身份按「当日有效」的 stockbasic/ST 记录判断，代码段只作受控 fallback。
  - environment_cn_a_v1：沪深 A 股（含创业、科创），排除北交所与 B 股；ST 计入但单独计数。
历史回放用当时有效成员，不能拿当前上市状态或当前 ST 状态回填历史（由调用方传入当日记录）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .canonical import content_hash

CANDIDATE_POLICY = "universe_cn_mainboard_v1"
ENVIRONMENT_POLICY = "environment_cn_a_v1"

POLICIES = {
    CANDIDATE_POLICY: {
        "policy_id": CANDIDATE_POLICY,
        "version": 1,
        "include_boards": ["SH_MAIN", "SZ_MAIN"],
        "exclude": ["CHINEXT", "STAR", "BSE", "B_SHARE", "ST", "delisting_risk", "unknown_market",
                    "not_listed_on_session"],
        "identity_source_priority": ["stockbasic_market_as_observed", "code_prefix_fallback(disclosed)"],
        "description": "沪深主板 A 股候选范围：排除创业板、科创板、北交所、B 股与当日 ST/退市风险；市场未知不纳入",
    },
    ENVIRONMENT_POLICY: {
        "policy_id": ENVIRONMENT_POLICY,
        "version": 1,
        "include_boards": ["SH_MAIN", "SZ_MAIN", "CHINEXT", "STAR"],
        "exclude": ["BSE", "B_SHARE", "unknown_market", "not_listed_on_session"],
        "st_counted_separately": True,
        "description": "市场环境分母：沪深 A 股（主板、创业板、科创板），排除北交所与 B 股；ST 计入并单列",
    },
}


@dataclass
class Member:
    symbol: str
    board: str                 # SH_MAIN/SZ_MAIN/CHINEXT/STAR/BSE/unknown
    category: str              # a_share / b_share / unknown
    raw_market: str | None
    is_st: bool | None         # None=当日 ST 状态未知
    listed: bool | None        # None=上市状态未知
    name: str = ""
    board_source: str = "stockbasic"


@dataclass
class UniverseResult:
    policy_id: str
    session_date: str
    eligible: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)        # symbol → reason
    excluded_markets: set[str] = field(default_factory=set)       # 因市场规则排除的原始 market 值
    fallback_used: int = 0

    @property
    def fingerprint(self) -> str:
        return content_hash({"policy_id": self.policy_id, "session_date": self.session_date,
                             "members": sorted(self.eligible)})

    def summary(self) -> dict:
        return {"policy_id": self.policy_id, "session_date": self.session_date,
                "member_count": len(self.eligible), "excluded_count": len(self.excluded),
                "fingerprint": self.fingerprint, "code_prefix_fallback_count": self.fallback_used,
                "description": POLICIES[self.policy_id]["description"]}


def build(policy_id: str, session_date: str, members: list[Member]) -> UniverseResult:
    p = POLICIES[policy_id]
    res = UniverseResult(policy_id, session_date)
    for m in members:
        reason = None
        if m.category == "b_share":
            reason = "market_excluded:b_share"
            if m.raw_market:
                res.excluded_markets.add(m.raw_market)
        elif m.board == "unknown":
            reason = "unknown_market"
        elif m.board not in p["include_boards"]:
            reason = f"market_excluded:{m.board}"
            if m.raw_market:
                res.excluded_markets.add(m.raw_market)
        elif m.listed is False:
            reason = "not_listed_on_session"
        elif m.listed is None:
            reason = "listing_status_unknown"
        elif policy_id == CANDIDATE_POLICY and m.is_st is True:
            reason = "special_treatment"
        elif policy_id == CANDIDATE_POLICY and m.is_st is None:
            reason = "st_status_unknown"
        elif policy_id == CANDIDATE_POLICY and "退" in (m.name or ""):
            reason = "delisting_risk"
        if reason:
            res.excluded[m.symbol] = reason
        else:
            res.eligible.append(m.symbol)
            if m.board_source == "code_prefix_fallback":
                res.fallback_used += 1
    res.eligible.sort()
    return res


def historical_members(members: list[dict], cutoff_session: str) -> dict:
    """时点成员（AC-056）：按截点当日有效的上市/退市日期与当时状态，不用「今天还活着」的幸存者名单。

    members 每项：symbol, listed_at, delisted_at(可空), status_at_cutoff(可空：suspended/normal/…)。
    截点时已上市且尚未退市 → 在册（停牌也在册，只是状态不同）；尚未上市 → 不在册；已退市 → 不在册。
    """
    symbols, status, excluded = [], {}, {}
    for m in members:
        la, da = m.get("listed_at"), m.get("delisted_at")
        if la is None:
            excluded[m["symbol"]] = "listing_date_unknown"
            continue
        if la > cutoff_session:
            excluded[m["symbol"]] = "not_yet_listed_at_cutoff"
            continue
        if da is not None and da <= cutoff_session:
            excluded[m["symbol"]] = "delisted_before_cutoff"
            continue
        symbols.append(m["symbol"])
        status[m["symbol"]] = m.get("status_at_cutoff") or "listed"
    return {"cutoff_session": cutoff_session, "symbols": sorted(symbols), "status_by_symbol": status,
            "excluded": excluded, "current_survivor_filter_used": False,
            "policy": "point_in_time_membership_v1：上市日 ≤ 截点 < 退市日；停牌照样在册"}
