"""Evidence-bound V4 "market way" decision layer.

The service decorates the stable 2026 workbench. It never turns a policy title,
fund-flow number, or inferred player identity into a standalone trade signal.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import desc, func, select

from database import async_session
from models import (
    DataSourceRegistry,
    DataQualityEvent,
    FinancialPITSnapshot,
    IndustryFundFlowDaily,
    IndustryValidationSnapshot,
    MarketBoard,
    MarketDataCache,
    MarketWayJudgment,
    MarketWayState,
    PolicyTransmissionRecord,
    TruthDataConflict,
    TruthDataEvent,
    StockUniverseSnapshot,
)
from services.data_collector import shanghai_now
from services.macro_policy_news import macro_policy_news_collector
from services.market_decision_contract import WORKBENCH_CONTRACT_VERSION
from services.truth_layer import (
    PointInTimeGuard,
    SOURCE_GRADE_WEIGHT,
    SOURCE_REGISTRY,
    detect_data_conflicts,
    evidence_fingerprint,
    finite_number,
    parse_datetime,
    source_identity,
    tagged_statement,
)


MARKET_WAY_VERSION = "a-share-market-way-v4.0.0"
POLICY_CACHE_KEY = "market_way_v4_policy_context"
JUDGMENT_ACTIONS = {"BULLISH", "NEUTRAL", "BEARISH", "WAIT", "NO_TRADE"}

NATIONAL_DIRECTIONS: tuple[dict[str, Any], ...] = (
    {"id": "tech_self_reliance", "name": "科技自主", "keywords": ("科技自立", "科技自主", "国产替代", "核心技术", "集成电路", "半导体", "芯片"), "sectors": ("半导体", "芯片", "电子", "软件", "计算机")},
    {"id": "new_quality_productivity", "name": "新质生产力", "keywords": ("新质生产力", "未来产业", "战略性新兴产业"), "sectors": ("机器人", "自动化", "高端装备", "人工智能", "工业母机")},
    {"id": "energy_security", "name": "能源安全", "keywords": ("能源安全", "能源保障", "油气", "煤炭", "电力安全", "新型电力系统"), "sectors": ("煤炭", "石油", "油气", "电力", "电网", "储能")},
    {"id": "food_security", "name": "粮食安全", "keywords": ("粮食安全", "耕地保护", "种业", "农业现代化", "粮食生产"), "sectors": ("农业", "种业", "农产品", "化肥", "养殖")},
    {"id": "defense_security", "name": "国防安全", "keywords": ("国防", "军工", "强军", "航空航天", "国家安全"), "sectors": ("军工", "航空", "航天", "船舶")},
    {"id": "artificial_intelligence", "name": "人工智能", "keywords": ("人工智能", "大模型", "智能算力", "算力基础设施", "数据要素"), "sectors": ("人工智能", "算力", "数据", "软件", "通信", "机器人")},
    {"id": "advanced_manufacturing", "name": "高端制造", "keywords": ("高端制造", "先进制造", "设备更新", "工业母机", "智能制造"), "sectors": ("机械", "高端装备", "工业母机", "机器人", "自动化")},
    {"id": "biomedicine", "name": "生物医药", "keywords": ("生物医药", "创新药", "医疗器械", "公共卫生", "医药创新"), "sectors": ("医药", "医疗", "生物", "创新药")},
    {"id": "consumption_livelihood", "name": "消费与民生", "keywords": ("扩大内需", "提振消费", "民生", "消费品", "服务消费", "以旧换新"), "sectors": ("消费", "家电", "食品", "旅游", "零售", "汽车")},
    {"id": "financial_reform", "name": "金融改革", "keywords": ("金融改革", "资本市场", "金融强国", "证券", "保险", "银行"), "sectors": ("证券", "银行", "保险", "多元金融")},
    {"id": "digital_economy", "name": "数字经济", "keywords": ("数字经济", "数字中国", "数据要素", "云计算", "工业互联网"), "sectors": ("数字经济", "数据", "云计算", "软件", "互联网")},
    {"id": "industrial_upgrade", "name": "产业升级", "keywords": ("产业升级", "技术改造", "转型升级", "专精特新", "制造业升级"), "sectors": ("制造", "专精特新", "机械", "自动化", "材料")},
    {"id": "major_infrastructure", "name": "重大基础设施", "keywords": ("重大基础设施", "基础设施", "重大项目", "专项债", "城市更新", "交通强国"), "sectors": ("基建", "建筑", "建材", "轨交", "工程机械", "通信")},
)

OFFENSIVE_TERMS = ("科技", "人工智能", "算力", "机器人", "半导体", "软件", "电子", "新能源", "军工", "高端")
DEFENSIVE_TERMS = ("银行", "公用", "电力", "煤炭", "食品", "医药", "农业", "红利", "运营商")


def _num(value: Any) -> float | None:
    return finite_number(value)


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _avg(values: list[float | None]) -> float | None:
    observed = [float(value) for value in values if value is not None and math.isfinite(value)]
    return sum(observed) / len(observed) if observed else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _unique(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value not in (None, "")))


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _naive(value: Any) -> datetime:
    parsed = parse_datetime(value) or shanghai_now()
    return parsed.replace(tzinfo=None)


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _trimmed_mean(values: list[float | None]) -> float | None:
    observed = sorted(float(value) for value in values if value is not None and math.isfinite(value))
    if not observed:
        return None
    trim = int(len(observed) * 0.05) if len(observed) >= 20 else 0
    selected = observed[trim:len(observed) - trim] if trim else observed
    return sum(selected) / len(selected)


def _phase(now: datetime) -> str:
    minute = now.hour * 60 + now.minute
    if minute < 9 * 60 + 30:
        return "auction_0925"
    if minute <= 10 * 60 + 40:
        return "morning_1040"
    if minute <= 13 * 60:
        return "midday_1142"
    if minute < 14 * 60 + 40:
        return "hypothesis"
    if minute <= 14 * 60 + 55:
        return "tail_1455"
    return "close_review"


def _market_cutoff(payload: dict[str, Any], generated: datetime) -> datetime:
    meta = payload.get("meta") or {}
    trade_day = _date(meta.get("decision_date"))
    updated = parse_datetime(meta.get("updated_at"))
    if trade_day == generated.date() and meta.get("is_realtime") and updated:
        return min(updated, generated)
    if trade_day:
        return parse_datetime(datetime.combine(trade_day, time(15, 0))) or generated
    return updated or generated


def build_truth_layer(
    payload: dict[str, Any],
    policy_context: dict[str, Any] | None,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or shanghai_now()
    meta = payload.get("meta") or {}
    audit = payload.get("audit") or {}
    research_day = _date(meta.get("decision_date"))
    cutoff = _market_cutoff(payload, generated)
    records: list[dict[str, Any]] = []
    component_dates = audit.get("component_dates") if isinstance(audit.get("component_dates"), dict) else {}
    source_candidates = [str(item) for item in audit.get("data_sources") or [] if item]
    default_source = str(meta.get("source") or (source_candidates[0] if source_candidates else "database_cache"))

    for index, (component, raw_day) in enumerate(component_dates.items()):
        component_day = _date(raw_day)
        if component_day is None or component == "auction":
            continue
        raw_source = default_source
        if component == "index_history":
            raw_source = "tencent_index_history"
        elif component == "stock_selection" and len(source_candidates) > 1:
            raw_source = source_candidates[-1]
        source_key, source_meta = source_identity(raw_source)
        event_at = cutoff if component_day == generated.date() and meta.get("is_realtime") else parse_datetime(datetime.combine(component_day, time(15, 0)))
        available_at = parse_datetime(meta.get("updated_at")) or generated
        record = {
            "event_kind": "market_component",
            "fact_key": "research_trade_date",
            "label": component,
            "source_key": source_key,
            "source_name": source_meta["name"],
            "source_grade": source_meta["grade"],
            "tag": "FACT",
            "event_time": event_at.isoformat() if event_at else None,
            "publish_time": event_at.isoformat() if event_at else None,
            "available_time": available_at.isoformat(),
            "snapshot_time": generated.isoformat(),
            "research_trade_date": research_day.isoformat() if research_day else None,
            "data_cutoff_time": cutoff.isoformat(),
            "value": component_day.isoformat(),
            "quality_flags": ["CROSS_DAY_COMPONENT"] if research_day and component_day != research_day else [],
        }
        record["fingerprint"] = evidence_fingerprint(record)
        record["id"] = record["fingerprint"][:12]
        records.append(record)

    if research_day and not records:
        source_key, source_meta = source_identity(default_source)
        record = {
            "event_kind": "market_snapshot",
            "fact_key": "research_trade_date",
            "label": "market_workbench",
            "source_key": source_key,
            "source_name": source_meta["name"],
            "source_grade": source_meta["grade"],
            "tag": "FACT",
            "event_time": cutoff.isoformat(),
            "publish_time": cutoff.isoformat(),
            "available_time": (parse_datetime(meta.get("updated_at")) or generated).isoformat(),
            "snapshot_time": generated.isoformat(),
            "research_trade_date": research_day.isoformat(),
            "data_cutoff_time": cutoff.isoformat(),
            "value": research_day.isoformat(),
            "quality_flags": [],
        }
        record["fingerprint"] = evidence_fingerprint(record)
        record["id"] = record["fingerprint"][:12]
        records.append(record)

    context = policy_context or {}
    context_available = parse_datetime(context.get("updated_at")) or generated
    for item in context.get("policy_items") or []:
        published = parse_datetime(item.get("published_at"))
        if not published or published > generated:
            continue
        source_key, source_meta = source_identity(item.get("source"))
        record = {
            "event_kind": "policy_event",
            "fact_key": f"policy:{_stable_hash([item.get('title'), item.get('url')])[:20]}",
            "label": str(item.get("title") or "政策事件"),
            "source_key": source_key,
            "source_name": source_meta["name"],
            "source_grade": source_meta["grade"],
            "tag": "FACT",
            "event_time": published.isoformat(),
            "publish_time": published.isoformat(),
            "available_time": context_available.isoformat(),
            "snapshot_time": generated.isoformat(),
            "research_trade_date": research_day.isoformat() if research_day else generated.date().isoformat(),
            "data_cutoff_time": cutoff.isoformat(),
            "value": {"title": item.get("title"), "url": item.get("url")},
            "quality_flags": [],
        }
        record["fingerprint"] = evidence_fingerprint(record)
        record["id"] = record["fingerprint"][:12]
        records.append(record)

    filtered = PointInTimeGuard.filter(records, generated)
    accepted = filtered["accepted"]
    conflicts = detect_data_conflicts(accepted, research_day.isoformat() if research_day else generated.date().isoformat())
    market_coverage = _num((payload.get("market_state") or {}).get("coverage_pct")) or 0.0
    temporal_score = 100.0 * filtered["accepted_count"] / max(len(records), 1)
    source_score = _avg([
        SOURCE_GRADE_WEIGHT.get(str(item.get("source_grade") or "C"), 0.35) * 100
        for item in accepted
    ]) or 0.0
    conflict_penalty = sum(_num(item.get("confidence_penalty")) or 0.0 for item in conflicts)
    completeness = _clamp(market_coverage * 0.65 + temporal_score * 0.20 + source_score * 0.15 - conflict_penalty)
    has_future_violation = any(
        any(str(flag).startswith("FUTURE_") or flag == "AVAILABLE_BEFORE_PUBLICATION" for flag in item["pit"]["violations"])
        for item in filtered["rejected"]
    )
    if not research_day or market_coverage < 40 or has_future_violation:
        status = "FAIL"
    elif conflicts or filtered["rejected_count"] or market_coverage < 70:
        status = "LIMITED"
    else:
        status = "PASS"
    confidence = _clamp(completeness * (0.78 if conflicts else 1.0))
    grade_summary = {
        grade: sum(item.get("source_grade") == grade for item in accepted)
        for grade in ("S", "A", "B", "C")
    }
    warnings = _unique([
        *(f"{item['label']}未通过PIT：{','.join(item['pit']['violations'])}" for item in filtered["rejected"]),
        *(f"{item['fact_key']}存在DATA_CONFLICT" for item in conflicts),
        *(f"{item['label']}跨交易日降级" for item in accepted if "CROSS_DAY_COMPONENT" in (item.get("quality_flags") or [])),
        *(f"{item}跨交易日降级" for item in audit.get("stale_components") or []),
    ])
    fact_ids = [item["id"] for item in accepted]
    return {
        "version": "truth-layer-v1.0.0",
        "research_trade_date": research_day.isoformat() if research_day else None,
        "data_cutoff_time": cutoff.isoformat(),
        "generated_at": generated.isoformat(),
        "status": status,
        "status_label": {"PASS": "真值通过", "LIMITED": "证据降级", "FAIL": "真值阻断"}[status],
        "completeness_pct": round(completeness, 1),
        "confidence_pct": round(confidence, 1),
        "high_confidence_allowed": status == "PASS" and confidence >= 70,
        "source_grade_summary": grade_summary,
        "source_rule": "S官方一手、A专业数据、B可靠研究、C仅作情绪线索；低等级不能覆盖高等级事实。",
        "pit_guard": {
            "passed": filtered["passed"],
            "accepted_count": filtered["accepted_count"],
            "rejected_count": filtered["rejected_count"],
            "rule": "available_time <= decision_time；未来数据不得进入当时判断。",
        },
        "conflicts": conflicts,
        "warnings": warnings,
        "records": [
            {
                key: item.get(key)
                for key in (
                    "id", "fingerprint", "event_kind", "fact_key", "label", "source_key",
                    "source_name", "source_grade", "tag", "event_time", "publish_time",
                    "available_time", "snapshot_time", "research_trade_date", "data_cutoff_time",
                    "value", "quality_flags", "status",
                )
            }
            for item in [*accepted, *filtered["rejected"]]
        ],
        "tagged_output": [
            tagged_statement(
                f"本次研究交易日为{research_day.isoformat() if research_day else '未确定'}，数据截止{cutoff.isoformat()}。",
                "FACT", evidence_ids=fact_ids[:8], confidence_pct=confidence,
            ),
            tagged_statement(
                "真值层通过不代表市场方向正确，只代表当前判断使用了时间可用且来源可追踪的数据。",
                "INFERENCE", evidence_ids=fact_ids[:8], confidence_pct=confidence,
            ),
        ],
    }


def _policy_level(title: str) -> str:
    if any(term in title for term in ("资金", "项目", "补贴", "采购", "专项债", "再贷款", "投资计划")):
        return "L4"
    if any(term in title for term in ("实施细则", "管理办法", "工作要点", "试点办法", "申报指南")):
        return "L3"
    if any(term in title for term in ("战略", "纲要", "五年规划", "强国建设")):
        return "L1"
    return "L2"


def _direction_matches(text: str, direction: dict[str, Any]) -> bool:
    return any(term.lower() in text.lower() for term in direction["keywords"])


def _sector_matches(name: str, direction: dict[str, Any]) -> bool:
    return any(term in name for term in direction["sectors"])


def _industry_sample(
    direction: dict[str, Any],
    payload: dict[str, Any],
    industry_evidence: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    aggregate = (industry_evidence or {}).get(str(direction["id"]))
    if aggregate:
        status = str(aggregate.get("validation_status") or "PARTIAL")
        label = {
            "VERIFIED_IMPROVING": "行业财务PIT验证改善",
            "VERIFIED_WEAKENING": "行业财务PIT验证走弱",
            "VERIFIED_MIXED": "行业财务PIT验证分化",
            "PARTIAL": "行业财务覆盖仍在补采",
        }.get(status, "行业财务证据待核验")
        return {
            "status": status,
            "label": label,
            "sample_count": int(aggregate.get("financial_sample_count") or 0),
            "universe_count": int(aggregate.get("universe_count") or 0),
            "coverage_pct": _round(_num(aggregate.get("coverage_pct")), 1),
            "source_data_date": aggregate.get("source_data_date"),
            "latest_disclosure_date": aggregate.get("latest_disclosure_date"),
            "metrics": aggregate.get("metrics") or {},
            "source": aggregate.get("source") or "financial_pit_snapshots + stock_universe_snapshots",
            "source_grade": aggregate.get("source_grade") or "A",
            "sample_stocks": [],
            "causal_link_verified": False,
            "boundary": "行业指标来自公告日可用的公司财务PIT聚合；可验证盈利阶段，但政策与盈利之间的因果仍需订单、项目和资本开支证据。",
        }
    decision = payload.get("decision_2026") or {}
    candidates = [
        item for item in decision.get("candidate_decisions") or []
        if _sector_matches(str(item.get("sector") or ""), direction)
    ]
    scores = [_num((item.get("fundamental") or {}).get("score")) for item in candidates]
    observed = [score for score in scores if score is not None]
    average = _avg(observed)
    if len(observed) >= 2 and average is not None and average >= 65:
        status, label = "SAMPLE_IMPROVING", "企业样本盈利改善"
    elif observed and average is not None and average < 45:
        status, label = "SAMPLE_WEAK", "企业样本盈利偏弱"
    elif observed:
        status, label = "SAMPLE_MIXED", "企业样本盈利分化"
    else:
        status, label = "UNVERIFIED", "产业/盈利尚未验证"
    return {
        "status": status,
        "label": label,
        "sample_count": len(observed),
        "universe_count": len(candidates),
        "coverage_pct": round(len(observed) / max(len(candidates), 1) * 100, 1),
        "source_data_date": (payload.get("meta") or {}).get("decision_date"),
        "latest_disclosure_date": None,
        "metrics": {"average_fundamental_score": _round(average, 1)},
        "source": "workbench_candidate_financial_pit",
        "source_grade": "A",
        "average_fundamental_score": _round(average, 1),
        "sample_stocks": [
            {"code": item.get("code"), "name": item.get("name"), "sector": item.get("sector"), "score": (item.get("fundamental") or {}).get("score")}
            for item in candidates[:5]
        ],
        "causal_link_verified": False,
        "boundary": "候选股财务PIT样本只用于产业线索，不能证明政策已传导至全行业或由政策导致盈利改善。",
    }


def _derived_market_strength(change_pct: float | None, breadth_pct: float | None) -> float | None:
    """Derive a transparent sector score from same-day observable fields."""
    components: list[float] = []
    if change_pct is not None:
        components.append(_clamp(50.0 + change_pct * 5.0))
    if breadth_pct is not None:
        components.append(_clamp(breadth_pct))
    return _round(_avg(components), 1)


def build_national_direction_radar(
    payload: dict[str, Any],
    policy_context: dict[str, Any] | None,
    truth: dict[str, Any],
    industry_evidence: dict[str, dict[str, Any]] | None = None,
    market_flow_evidence: list[dict[str, Any]] | None = None,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or shanghai_now()
    context = policy_context or {}
    accepted_policy_labels = {
        item.get("label") for item in truth.get("records") or []
        if item.get("event_kind") == "policy_event" and item.get("status") == "ACCEPTED"
    }
    policy_items = [
        item for item in context.get("policy_items") or []
        if item.get("title") in accepted_policy_labels
    ]
    # The readable workbench keeps only leading topics. Direction validation
    # also consumes the complete industry-flow cache so lower-ranked sectors
    # are not silently treated as missing.
    line_by_name: dict[str, dict[str, Any]] = {}
    for raw in [*(payload.get("main_lines") or []), *(market_flow_evidence or [])]:
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        key = "".join(name.split())
        existing = line_by_name.get(key)
        if existing is None or (
            _num(existing.get("strength_score")) is None
            and _num(raw.get("strength_score")) is not None
        ):
            line_by_name[key] = raw
    lines = list(line_by_name.values())
    rows: list[dict[str, Any]] = []
    for direction in NATIONAL_DIRECTIONS:
        matched = [item for item in policy_items if _direction_matches(str(item.get("title") or ""), direction)]
        matched.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
        recent = 0
        prior = 0
        for item in matched:
            published = _date(item.get("published_at"))
            if not published:
                continue
            age = (generated.date() - published).days
            if 0 <= age <= 30:
                recent += 1
            elif 31 <= age <= 90:
                prior += 1
        restrictive = any(any(term in str(item.get("title") or "") for term in ("整治", "收紧", "限制", "处罚", "风险提示")) for item in matched[:3])
        if restrictive:
            marginal = "REVERSING"
        elif recent >= 2 and recent > prior:
            marginal = "ACCELERATING"
        elif recent:
            marginal = "STABLE"
        elif prior:
            marginal = "DECELERATING"
        else:
            marginal = "UNOBSERVED"
        levels = [_policy_level(str(item.get("title") or "")) for item in matched]
        max_direct_level = max(levels, key=lambda value: int(value[1:])) if levels else None
        market_lines = [line for line in lines if _sector_matches(str(line.get("name") or ""), direction)]
        strength_values = [
            value for line in market_lines
            if (value := _num(line.get("strength_score"))) is not None
        ]
        market_strength = max(strength_values) if strength_values else None
        market_change = _avg([_num(line.get("change_pct")) for line in market_lines])
        market_breadth = _avg([_num(line.get("breadth")) for line in market_lines])
        if market_strength is not None and market_strength >= 65 and (market_change or 0) > 0:
            market_status = "PRICED_UP"
        elif market_strength is not None and market_strength < 50:
            market_status = "NOT_STARTED"
        elif market_strength is not None:
            market_status = "MIXED"
        else:
            market_status = "UNOBSERVED"
        industry = _industry_sample(direction, payload, industry_evidence)
        industry_verified = industry["status"] in {"VERIFIED_IMPROVING", "VERIFIED_WEAKENING", "VERIFIED_MIXED"}
        enterprise_verified = (
            industry_verified
            and int(industry.get("sample_count") or 0) >= 5
            and _num((industry.get("metrics") or {}).get("profitable_company_ratio_pct")) is not None
        )
        stages = [
            {"level": "L1", "label": "国家战略", "verified": "L1" in levels, "evidence": [item.get("title") for item, level in zip(matched, levels) if level == "L1"][:2]},
            {"level": "L2", "label": "重大政策", "verified": "L2" in levels, "evidence": [item.get("title") for item, level in zip(matched, levels) if level == "L2"][:3]},
            {"level": "L3", "label": "执行细则", "verified": "L3" in levels, "evidence": [item.get("title") for item, level in zip(matched, levels) if level == "L3"][:2]},
            {"level": "L4", "label": "资金/项目落地", "verified": "L4" in levels, "evidence": [item.get("title") for item, level in zip(matched, levels) if level == "L4"][:2]},
            {"level": "L5", "label": "产业数据验证", "verified": industry_verified, "proxy_observed": bool(market_lines), "evidence": [f"公司财务PIT行业聚合覆盖{industry.get('coverage_pct') or 0}%，状态{industry['label']}", "订单、价格、产量和开工率仍作为下一层专项数据源"]},
            {"level": "L6", "label": "企业盈利兑现", "verified": enterprise_verified, "proxy_observed": industry["sample_count"] > 0, "evidence": [industry["boundary"]]},
        ]
        contiguous = 0
        for stage in stages:
            if stage["verified"]:
                contiguous += 1
            else:
                break
        max_verified = f"L{contiguous}" if contiguous else "L0"
        transmission_state = "UNOBSERVED" if not matched else "CONTIGUOUS" if contiguous >= 4 else "PARTIAL"
        policy_up = marginal in {"ACCELERATING", "STABLE"}
        industry_up = industry["status"] in {"SAMPLE_IMPROVING", "VERIFIED_IMPROVING"}
        market_up = market_status == "PRICED_UP" and (market_strength or 0) >= 70
        if policy_up and industry_up and not market_up:
            gap_state = "A"
            gap_label = "政策与盈利样本改善，市场尚未启动"
        elif policy_up and industry_up and market_up:
            gap_state = "B"
            gap_label = "政策、盈利样本与市场同向"
        elif policy_up and not industry_up and market_up:
            gap_state = "C"
            gap_label = "市场跑在产业验证前面"
        elif not policy_up and not industry_up and market_up:
            gap_state = "D"
            gap_label = "市场上涨缺少政策与盈利验证"
        else:
            gap_state = "UNRESOLVED"
            gap_label = "证据不足或尚未形成典型错位"
        observed_weight = 0
        weighted_score = 0.0
        if matched:
            policy_score = 75.0 if marginal == "ACCELERATING" else 60.0 if marginal == "STABLE" else 35.0
            weighted_score += policy_score * 30
            observed_weight += 30
        industry_score = _num((industry.get("metrics") or {}).get("validation_score"))
        if industry_score is None:
            industry_score = _num(industry.get("average_fundamental_score"))
        if industry_score is not None:
            weighted_score += industry_score * 30
            observed_weight += 30
        if market_strength is not None:
            weighted_score += market_strength * 30
            observed_weight += 30
        if matched:
            weighted_score += 100 * 10
            observed_weight += 10
        score = weighted_score / observed_weight if observed_weight >= 30 else None
        confidence = min(82.0, 22.0 + len(matched) * 7.0 + len(market_lines) * 5.0 + industry["sample_count"] * 4.0)
        evidence = _unique([
            *(str(item.get("title")) for item in matched[:3]),
            *(
                f"{line.get('name')}强度{line.get('strength_score')}、宽度{line.get('breadth')}%"
                f"（{line.get('source') or '市场缓存'}）"
                for line in market_lines[:2]
            ),
            industry["label"] if industry["sample_count"] else "",
        ])
        rows.append({
            "id": direction["id"],
            "name": direction["name"],
            "marginal_state": marginal,
            "policy_count_30d": recent,
            "policy_count_31_90d": prior,
            "max_direct_policy_level": max_direct_level,
            "max_verified_level": max_verified,
            "transmission_state": transmission_state,
            "stages": stages,
            "industry_validation": industry,
            "market_validation": {
                "status": market_status,
                "strength_score": _round(market_strength, 1),
                "change_pct": _round(market_change, 2),
                "breadth_pct": _round(market_breadth, 1),
                "sectors": [line.get("name") for line in market_lines],
                "source": _unique([str(line.get("source") or "") for line in market_lines]) or ["unavailable"],
                "data_date": max((str(line.get("data_date") or "") for line in market_lines), default=None) or None,
            },
            "gap": {"state": gap_state, "label": gap_label, "causal_verified": False},
            "score": _round(score, 1),
            "confidence_pct": round(confidence, 1) if evidence else 0.0,
            "evidence": evidence or ["当前没有可用于该方向的S级政策或同日市场证据"],
            "policies": [
                {
                    "title": item.get("title"), "source": item.get("source"), "source_grade": "S",
                    "published_at": item.get("published_at"), "url": item.get("url"),
                    "level": _policy_level(str(item.get("title") or "")), "tag": "FACT",
                }
                for item in matched[:6]
            ],
        })
    rows.sort(key=lambda item: (item["score"] is not None, item["score"] or -1, item["policy_count_30d"]), reverse=True)
    observed = [item for item in rows if item["policies"] or item["market_validation"]["status"] != "UNOBSERVED"]
    return {
        "version": "national-direction-v1.0.0",
        "updated_at": generated.isoformat(),
        "directions": rows,
        "observed_count": len(observed),
        "policy_source_available": bool(policy_items),
        "summary": (
            f"{len(observed)}个国家方向具有可追踪证据；只有连续L1-L6传导才可视为政策走向产业与盈利。"
            if observed else "官方政策证据或方向匹配当前不足，不生成政策投资结论。"
        ),
        "boundary": "政策新闻不是买入信号；L5/L6必须由产业与企业数据验证，市场上涨不能替代。",
    }


def _previous_payloads(previous_states: list[Any]) -> list[dict[str, Any]]:
    output = []
    for item in previous_states:
        if isinstance(item, dict):
            payload = item
        else:
            payload = getattr(item, "payload", {})
        if isinstance(payload, dict):
            output.append(payload.get("market_way_v4") or payload)
    return output


def build_momentum_state(payload: dict[str, Any], previous_states: list[Any] | None = None) -> dict[str, Any]:
    market = payload.get("market_state") or {}
    structure = payload.get("structure_health") or {}
    crowding = payload.get("crowding_risk") or {}
    dimensions = {str(item.get("id")): item for item in market.get("dimensions") or []}
    lines = payload.get("main_lines") or []
    market_score = _num(market.get("score"))
    structure_score = _num(structure.get("score"))
    capital_score = _num((dimensions.get("capital") or {}).get("score"))
    breadth_score = _num((dimensions.get("breadth") or {}).get("score"))
    mainline_score = _avg([_num(item.get("strength_score")) for item in lines[:3]])
    line_breadth = _avg([_num(item.get("breadth")) for item in lines[:3]])
    crowding_score = _num(crowding.get("score"))
    strength = _avg([market_score, structure_score, capital_score, mainline_score])
    breadth = _avg([breadth_score, line_breadth])
    if market_score is None:
        direction = "UNKNOWN"
    elif str(market.get("state_code")) in {"S1", "S2"} and market_score >= 60:
        direction = "UP"
    elif str(market.get("state_code")) in {"S4", "S5"} or market_score < 40:
        direction = "DOWN"
    else:
        direction = "MIXED"
    order_values = [structure_score, mainline_score, breadth, capital_score]
    order_weights = [35, 25, 20, 20]
    observed = [(value, weight) for value, weight in zip(order_values, order_weights) if value is not None]
    order_score = sum(value * weight for value, weight in observed) / sum(weight for _, weight in observed) if sum(weight for _, weight in observed) >= 50 else None
    previous = _previous_payloads(previous_states or [])
    previous_momentum = [item.get("momentum") or {} for item in previous]
    previous_strength = _num((previous_momentum[0] if previous_momentum else {}).get("strength"))
    previous_order = _num((previous_momentum[0] if previous_momentum else {}).get("order_score"))
    marginal = strength - previous_strength if strength is not None and previous_strength is not None else None
    order_delta = order_score - previous_order if order_score is not None and previous_order is not None else None
    qualitative = str((payload.get("contradiction_evolution") or {}).get("qualitative_shift") or "not_confirmed")
    if qualitative == "confirmed" or (previous_order is not None and order_delta is not None and previous_order >= 60 and order_delta <= -15):
        order_state = "结构瓦解"
    elif order_score is None:
        order_state = "无序"
    elif crowding_score is not None and crowding_score >= 78 and order_score >= 65:
        order_state = "拥挤"
    elif order_score >= 72:
        order_state = "高度有序"
    elif order_score >= 56:
        order_state = "结构形成"
    elif order_score >= 40:
        order_state = "开始聚合"
    else:
        order_state = "无序"
    previous_state = str((previous_momentum[0] if previous_momentum else {}).get("state") or "")
    if direction == "DOWN" or order_state == "结构瓦解":
        state = "退势"
    elif previous_state == "退势" and (structure_score or 0) >= 55 and (marginal or 0) > 0:
        state = "返势"
    elif (crowding_score or 0) >= 82 and (market_score or 0) >= 70:
        state = "极势"
    elif direction == "UP" and ((structure_score is not None and structure_score < 52) or (breadth is not None and breadth < 45)):
        state = "分势"
    elif (market_score or 0) >= 80 and (breadth or 0) >= 65:
        state = "盛势"
    elif direction == "UP" and (structure_score or 0) >= 60:
        state = "顺势"
    elif direction in {"UP", "MIXED"} and (marginal or 0) >= 5 and (line_breadth or 0) >= 50:
        state = "启势"
    elif (structure_score or 0) >= 42 or (order_delta or 0) > 5:
        state = "蓄势"
    else:
        state = "无序"
    persistence = 1
    for old in previous_momentum:
        if old.get("direction") == direction:
            persistence += 1
        else:
            break
    trajectory = [
        {"trade_date": old.get("trade_date"), "score": old.get("order_score"), "state": old.get("order_state")}
        for old in reversed(previous_momentum[:4])
    ]
    trajectory.append({"trade_date": (payload.get("meta") or {}).get("decision_date"), "score": _round(order_score, 1), "state": order_state})
    return {
        "state": state,
        "direction": direction,
        "strength": _round(strength, 1),
        "persistence_sessions": persistence,
        "breadth": _round(breadth, 1),
        "marginal_change": _round(marginal, 1),
        "order_score": _round(order_score, 1),
        "order_state": order_state,
        "order_change": _round(order_delta, 1),
        "order_trajectory": trajectory,
        "evidence": _unique([
            f"市场状态{market.get('state_label')}，评分{market_score if market_score is not None else '--'}",
            f"结构健康{structure_score if structure_score is not None else '--'}，主线强度{_round(mainline_score, 1) if mainline_score is not None else '--'}",
            f"扩散度{_round(breadth, 1) if breadth is not None else '--'}，拥挤风险{crowding_score if crowding_score is not None else '--'}",
        ]),
        "method": "方向、强度、持续、扩散、边际变化与有序度联合判定；涨一天不定义为新势。",
    }


def build_capital_migration(payload: dict[str, Any], momentum: dict[str, Any], radar: dict[str, Any], previous_states: list[Any] | None = None) -> dict[str, Any]:
    lines = payload.get("main_lines") or []
    rows = []
    for line in lines:
        name = str(line.get("name") or "")
        flow = _num(line.get("main_net_inflow"))
        style = "OFFENSIVE" if any(term in name for term in OFFENSIVE_TERMS) else "DEFENSIVE" if any(term in name for term in DEFENSIVE_TERMS) else "BALANCED"
        rows.append({"sector": name, "flow": flow, "style": style, "strength": _num(line.get("strength_score")), "breadth": _num(line.get("breadth")), "lifecycle": line.get("lifecycle")})
    inflow = sorted([item for item in rows if item["flow"] is not None and item["flow"] > 0], key=lambda item: item["flow"], reverse=True)
    outflow = sorted([item for item in rows if item["flow"] is not None and item["flow"] < 0], key=lambda item: item["flow"])
    offensive_flow = sum(item["flow"] or 0 for item in rows if item["style"] == "OFFENSIVE")
    defensive_flow = sum(item["flow"] or 0 for item in rows if item["style"] == "DEFENSIVE")
    crowding = _num((payload.get("crowding_risk") or {}).get("score"))
    if momentum["direction"] == "UP" and offensive_flow > max(defensive_flow, 0) and (momentum.get("breadth") or 0) >= 52:
        appetite = "RISK_ON"
    elif momentum["direction"] == "DOWN" or defensive_flow > max(offensive_flow, 0) or (crowding or 0) >= 82:
        appetite = "RISK_OFF"
    else:
        appetite = "NEUTRAL"
    top = inflow[0] if inflow else (rows[0] if rows else {})
    lifecycle = str(top.get("lifecycle") or "")
    stage = {
        "启动": "异动", "观察": "异动", "扩散": "扩散", "强化": "持续",
        "分化预警": "拥挤", "退潮": "衰减",
    }.get(lifecycle, "承接" if top else "未观察")
    previous = _previous_payloads(previous_states or [])
    old_to = (((previous[0].get("capital_migration") or {}).get("to")) or []) if previous else []
    old_top = str((old_to[0] if old_to else {}).get("sector") or "")
    new_top = str(top.get("sector") or "")
    industry_support = any(
        new_top in (item.get("market_validation") or {}).get("sectors", [])
        and (item.get("industry_validation") or {}).get("status") == "SAMPLE_IMPROVING"
        for item in radar.get("directions") or []
    )
    if old_top and new_top and old_top != new_top and stage in {"持续", "扩散"} and industry_support:
        rotation = "MAINLINE_SWITCH"
        rotation_label = "疑似主线换道，仍需多日持续验证"
    elif appetite == "RISK_OFF" and defensive_flow > offensive_flow:
        rotation = "DEFENSIVE_SWITCH"
        rotation_label = "防御切换"
    else:
        rotation = "ROTATION"
        rotation_label = "普通轮动/证据未达换道标准"
    return {
        "from": outflow[:4],
        "to": inflow[:4],
        "matrix": {
            "offensive_inflow": [item for item in inflow if item["style"] == "OFFENSIVE"][:4],
            "offensive_outflow": [item for item in outflow if item["style"] == "OFFENSIVE"][:4],
            "defensive_inflow": [item for item in inflow if item["style"] == "DEFENSIVE"][:4],
            "defensive_outflow": [item for item in outflow if item["style"] == "DEFENSIVE"][:4],
        },
        "risk_appetite": appetite,
        "stage": stage,
        "rotation_type": rotation,
        "rotation_label": rotation_label,
        "evidence": _unique([
            f"进攻方向可见净流量{offensive_flow / 1e8:+.1f}亿元" if rows else "",
            f"防御方向可见净流量{defensive_flow / 1e8:+.1f}亿元" if rows else "",
            "流出端数据不足，不能完整回答资金从哪里来" if not outflow else "",
            f"当前迁徙阶段：{stage}",
        ]),
        "boundary": "资金净流入只描述可见交易结果，不等同于主力看多；换道需持续、扩散和产业支持。",
    }


def build_market_force(payload: dict[str, Any], momentum: dict[str, Any], capital: dict[str, Any], radar: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("headline_metrics") or {}
    lines = payload.get("main_lines") or []
    daily = (payload.get("daily_short_term_recommendations") or {}).get("candidates") or []
    market_caps = [_num(item.get("market_cap")) for item in daily]
    large_cap_share = sum(value is not None and value >= 30_000_000_000 for value in market_caps) / max(sum(value is not None for value in market_caps), 1)
    leader_boards = max((_num((item.get("leader") or {}).get("boards")) or 0 for item in lines), default=0)
    limit_up = _num(metrics.get("limit_up"))
    failed_rate = _num(metrics.get("failed_limit_rate"))
    policy_l4 = sum(any(stage["level"] == "L4" and stage["verified"] for stage in item.get("stages") or []) for item in radar.get("directions") or [])
    scores = {
        "机构配置型": 35 + large_cap_share * 35 + (10 if momentum["order_state"] in {"结构形成", "高度有序"} else 0),
        "游资情绪型": 25 + min(35, leader_boards * 8) + (10 if (limit_up or 0) >= 50 else 0) - (8 if (failed_rate or 0) >= 35 else 0),
        "量化交易型": 25 + (18 if momentum.get("breadth") is not None and 45 <= momentum["breadth"] <= 65 else 0) + (12 if momentum["order_state"] == "无序" else 0),
        "产业资本型": 25 + min(30, policy_l4 * 10),
        "融资杠杆型": 0,
        "散户情绪型": 22 + (18 if (limit_up or 0) >= 70 else 0) + (12 if (failed_rate or 0) >= 35 else 0),
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    observed_dimensions = sum(value > 0 for value in scores.values()) - 1
    top_name, top_score = ranked[0]
    second_score = ranked[1][1]
    if observed_dimensions < 3 or top_score - second_score < 8:
        force_type = "混合型"
    else:
        force_type = top_name
    confidence = min(72.0, 30.0 + observed_dimensions * 7.0 + max(0.0, top_score - second_score))
    return {
        "type": force_type,
        "label": f"疑似{force_type}定价",
        "confidence_pct": round(confidence, 1),
        "scores": [{"type": key, "score": round(value, 1)} for key, value in ranked],
        "evidence": _unique([
            f"候选中大市值样本占比{large_cap_share * 100:.0f}%" if market_caps else "",
            f"可见最高连板{int(leader_boards)}，涨停{int(limit_up) if limit_up is not None else '--'}，炸板率{failed_rate if failed_rate is not None else '--'}%",
            f"市场有序度：{momentum['order_state']}，资金风险偏好：{capital['risk_appetite']}",
            "融资余额字段不可核验，融资杠杆型不参与主导判定" if scores["融资杠杆型"] == 0 else "",
        ]),
        "boundary": "这里只能推断疑似主要定价力量；席位、净流入或交易形态都不能直接证明真实资金身份。",
    }


def _apply_truth_gate(payload: dict[str, Any], truth: dict[str, Any]) -> None:
    decision = payload.get("decision_2026") or {}
    permission = decision.get("trading_permission") or {}
    if truth["status"] == "FAIL":
        permission.update({
            "code": "BLOCK", "label": "真值阻断", "allows_new_position": False,
            "max_total_position_pct": 0,
            "reasons": _unique(["真值层未通过，禁止输出高置信度策略", *(permission.get("reasons") or [])]),
        })
        if payload.get("market_cognition"):
            payload["market_cognition"]["final_action"] = "no_trade"
            payload["market_cognition"]["action_label"] = "不交易"
        if payload.get("adaptive_strategy_weights"):
            payload["adaptive_strategy_weights"]["final_action"] = "no_trade"
            payload["adaptive_strategy_weights"]["weights"] = [
                {**item, "weight_pct": 0} for item in payload["adaptive_strategy_weights"].get("weights") or []
            ]
    elif truth["status"] == "LIMITED" and permission.get("code") == "ALLOW":
        permission.update({
            "code": "CAUTION", "label": "证据降级，仅谨慎研究", "allows_new_position": True,
            "max_total_position_pct": min(int(permission.get("max_total_position_pct") or 0), 25),
            "reasons": _unique(["真值层存在跨日或冲突证据，执行级别自动下调", *(permission.get("reasons") or [])]),
        })


def build_market_way_v4(
    payload: dict[str, Any],
    policy_context: dict[str, Any] | None = None,
    previous_states: list[Any] | None = None,
    industry_evidence: dict[str, dict[str, Any]] | None = None,
    market_flow_evidence: list[dict[str, Any]] | None = None,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or shanghai_now()
    truth = build_truth_layer(payload, policy_context, generated_at=generated)
    radar = build_national_direction_radar(
        payload, policy_context, truth, industry_evidence, market_flow_evidence,
        generated_at=generated,
    )
    momentum = build_momentum_state(payload, previous_states)
    capital = build_capital_migration(payload, momentum, radar, previous_states)
    force = build_market_force(payload, momentum, capital, radar)
    market = payload.get("market_state") or {}
    cognition = payload.get("market_cognition") or {}
    decision = payload.get("decision_2026") or {}
    crowding = _num((payload.get("crowding_risk") or {}).get("score"))
    regime = (decision.get("market_regime") or {}).get("label") or market.get("state_label") or "不可判定"
    position_state = "极端拥挤" if (crowding or 0) >= 85 else "高位拥挤" if (crowding or 0) >= 70 else "中性位置" if crowding is not None else "位置待核验"
    active_window = next((item for item in decision.get("decision_windows") or [] if item.get("status") in {"进行中", "窗口已到"}), None)
    timing = active_window or {"id": _phase(generated), "label": "当前研究窗口", "time": generated.strftime("%H:%M"), "status": "研究中"}
    top_directions = [item for item in radar["directions"] if item["policies"]][:3]
    policy_state = (
        "、".join(f"{item['name']}({item['marginal_state']})" for item in top_directions)
        if top_directions else "官方方向证据不足"
    )
    industry_supported = [item for item in radar["directions"] if item["industry_validation"]["status"] in {"SAMPLE_IMPROVING", "VERIFIED_IMPROVING"}]
    final_permission = decision.get("trading_permission") or {}
    if truth["status"] == "FAIL" or final_permission.get("code") == "BLOCK":
        conclusion = "NO_TRADE"
    elif final_permission.get("code") in {"ALLOW", "CAUTION"} and momentum["state"] in {"顺势", "盛势", "启势", "返势"}:
        conclusion = "EXECUTION_READY" if (decision.get("conditional_orders") or {}).get("execute") else "TRIGGER_WAIT"
    elif momentum["state"] in {"蓄势", "分势"}:
        conclusion = "WATCH"
    else:
        conclusion = "RESEARCH"
    confidence_inputs = [truth.get("confidence_pct"), market.get("confidence_pct"), momentum.get("strength")]
    confidence = _avg([_num(item) for item in confidence_inputs]) or 0.0
    if truth["status"] != "PASS":
        confidence = min(confidence, 59.0)
    chain = [
        {"key": "truth", "glyph": "真", "label": truth["status_label"], "status": truth["status"], "tag": "FACT", "summary": f"完整度{truth['completeness_pct']}%，截止{truth['data_cutoff_time']}", "evidence": truth["warnings"][:3] or ["四时间与来源等级检查完成"]},
        {"key": "way", "glyph": "道", "label": "国家方向与底层环境", "status": "OBSERVED" if top_directions else "UNVERIFIED", "tag": "INFERENCE", "summary": policy_state, "evidence": [item["evidence"][0] for item in top_directions] or ["无足够S级方向证据，不把题材上涨倒推为国家方向"]},
        {"key": "policy", "glyph": "策", "label": "政策边际与资源配置", "status": top_directions[0]["marginal_state"] if top_directions else "UNOBSERVED", "tag": "INFERENCE", "summary": f"已匹配{sum(bool(item['policies']) for item in radar['directions'])}个方向；L5/L6不由新闻推断", "evidence": [item["gap"]["label"] for item in top_directions] or [radar["boundary"]]},
        {"key": "industry", "glyph": "业", "label": "产业与盈利验证", "status": "SAMPLE_SUPPORT" if industry_supported else "UNVERIFIED", "tag": "INFERENCE", "summary": "、".join(item["name"] for item in industry_supported[:3]) or "尚无足够产业/企业盈利样本形成验证", "evidence": [item["industry_validation"]["boundary"] for item in (industry_supported[:1] or radar["directions"][:1])]},
        {"key": "momentum", "glyph": "势", "label": momentum["state"], "status": momentum["direction"], "tag": "INFERENCE", "summary": f"强度{momentum['strength'] if momentum['strength'] is not None else '--'}，有序度{momentum['order_state']}，持续{momentum['persistence_sessions']}个样本", "evidence": momentum["evidence"][:3]},
        {"key": "force", "glyph": "力", "label": force["label"], "status": force["type"], "tag": "INFERENCE", "summary": f"置信度{force['confidence_pct']}%，风险偏好{capital['risk_appetite']}", "evidence": force["evidence"][:3]},
        {"key": "shape", "glyph": "形", "label": str(market.get("state_label") or "不可判定"), "status": str(market.get("state_code") or "S0"), "tag": "FACT", "summary": str(cognition.get("principal_contradiction", {}).get("statement") or "市场结构待核验"), "evidence": (cognition.get("facts") or [])[:3]},
        {"key": "position", "glyph": "位", "label": position_state, "status": "HIGH" if (crowding or 0) >= 70 else "NORMAL" if crowding is not None else "UNKNOWN", "tag": "INFERENCE", "summary": f"市场阶段{regime}，拥挤风险{crowding if crowding is not None else '--'}", "evidence": (payload.get("crowding_risk") or {}).get("evidence", [])[:3]},
        {"key": "timing", "glyph": "时", "label": str(timing.get("label") or "研究窗口"), "status": str(timing.get("status") or "研究中"), "tag": "FACT", "summary": f"{timing.get('time') or generated.strftime('%H:%M')}；时间窗口只改变信息集，不创造胜率", "evidence": ["09:30-10:40、午间、14:40-14:55和次日竞价分别保存快照"]},
        {"key": "stop", "glyph": "止", "label": "动态失效边界", "status": "ACTIVE", "tag": "SCENARIO", "summary": "道变、业变、势变、力变、形变、位极或时失时降级/停止", "evidence": _unique([*((decision.get("exit_engine") or {}).get("logic_failure") or []), *((decision.get("exit_engine") or {}).get("market_deterioration") or [])])[:4]},
    ]
    counter_evidence = _unique([
        *(truth.get("warnings") or []),
        *(item["gap"]["label"] for item in radar["directions"] if item["gap"]["state"] in {"C", "D"}),
        *((decision.get("why_not_buy") or {}).get("reasons") or []),
    ])[:8]
    next_validation = _unique([
        "政策方向需继续验证L3执行细则、L4资源落地、L5产业数据和L6盈利兑现",
        "观察资金迁徙是否从异动走向持续与扩散",
        "下一决策窗口重新核验市场宽度、成交、板块结构和Alpha",
        *((cognition.get("practice_hypothesis") or {}).get("falsification") or []),
    ])[:7]
    return {
        "version": MARKET_WAY_VERSION,
        "contract_version": WORKBENCH_CONTRACT_VERSION,
        "generated_at": generated.isoformat(),
        "phase": _phase(generated),
        "truth": truth,
        "national_direction_radar": radar,
        "momentum": momentum,
        "capital_migration": capital,
        "market_force": force,
        "chain": chain,
        "principal_contradiction": cognition.get("principal_contradiction") or {"statement": "证据不足", "evidence": []},
        "final_decision": {
            "code": conclusion,
            "label": {"RESEARCH": "研究", "WATCH": "观察", "TRIGGER_WAIT": "等待触发", "EXECUTION_READY": "满足执行研究条件", "NO_TRADE": "不交易"}[conclusion],
            "confidence_pct": round(_clamp(confidence), 1),
            "permission": final_permission.get("label"),
            "why_not_buy": (decision.get("why_not_buy") or {}).get("reasons", [])[:6],
            "evidence": _unique([momentum["evidence"][0] if momentum["evidence"] else "", force["evidence"][0] if force["evidence"] else "", policy_state]),
            "counter_evidence": counter_evidence or ["当前未形成额外反证，但仍需人工最终判断"],
            "next_validation": next_validation,
            "real_broker_order": False,
        },
        "standard_output": {
            "facts": truth["tagged_output"],
            "inferences": [tagged_statement(item["summary"], "INFERENCE", confidence_pct=confidence) for item in chain if item["tag"] == "INFERENCE"],
            "scenarios": [tagged_statement(item, "SCENARIO", confidence_pct=None) for item in next_validation],
        },
        "boundaries": [
            "政策利好不直接等于买入",
            "资金净流入不直接等于主力看多",
            "市场上涨不能替代产业和盈利验证",
            "评分高不直接进入执行，必须通过真值、位置、时间和知止条件",
        ],
    }


class MarketWayV4Service:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None
        self._last_trade_day: date | None = None
        self._last_pipeline: dict[str, Any] = {}
        self._last_flow_source: dict[str, Any] = {}
        self._refresh_status: dict[str, Any] = {
            "status": "idle", "stage": "idle", "progress": 0,
            "message": "等待定时更新", "updated_at": None,
        }

    async def _policy_context(self, *, refresh: bool = False) -> dict[str, Any]:
        cached: dict[str, Any] = {}
        try:
            async with async_session() as session:
                direct = await session.get(MarketDataCache, POLICY_CACHE_KEY)
                macro = await session.get(MarketDataCache, "macro_dashboard_v1")
            if direct and isinstance(direct.payload, dict):
                cached = dict(direct.payload)
            elif macro and isinstance(macro.payload, dict):
                policy = (macro.payload or {}).get("policy") or {}
                cached = {**policy, "updated_at": (macro.payload or {}).get("snapshot_updated_at") or (macro.payload or {}).get("updated_at")}
        except Exception:
            cached = {}
        if cached.get("available") and not refresh:
            return cached
        if not refresh and cached:
            return cached
        try:
            fresh = await asyncio.wait_for(macro_policy_news_collector.get_context(), timeout=6.0)
        except Exception:
            return cached or macro_policy_news_collector.empty_context()
        if isinstance(fresh, dict) and fresh.get("available"):
            try:
                async with async_session() as session:
                    row = await session.get(MarketDataCache, POLICY_CACHE_KEY)
                    if row is None:
                        session.add(MarketDataCache(key=POLICY_CACHE_KEY, payload=fresh))
                    else:
                        row.payload = fresh
                    await session.commit()
            except Exception:
                pass
        return fresh if isinstance(fresh, dict) else cached

    async def _market_history_status(self) -> dict[str, Any]:
        """Describe the persisted market-evidence chain before calculating V4."""
        try:
            from services.strategic_market_data import strategic_market_data_service
            from services.history_cache import history_cache

            snapshot = await strategic_market_data_service.history(limit=30)
            backfill_job = await history_cache.latest_backfill_status()
        except Exception as exc:
            return {
                "status": "unavailable",
                "data_date": None,
                "history_count": 0,
                "amount_history_count": 0,
                "turnover_history_count": 0,
                "source": "stock_daily_bars -> market_sentiment_daily",
                "message": f"市场情绪历史读取失败：{type(exc).__name__}",
                "action": "重试读取本地缓存；若仍为空则采集全市场日行情",
                "source_chain": ["腾讯日线", "FTShare日线", "StockDailyBar", "MarketSentimentDaily"],
                "backfill_job": None,
            }
        summary = snapshot.get("summary") or {}
        history_count = int(snapshot.get("count") or 0)
        amount_count = int(summary.get("amount_history_count") or 0)
        turnover_count = int(summary.get("turnover_history_count") or 0)
        latest = summary.get("latest") or {}
        latest_amount = _num(latest.get("market_amount"))
        amount_ma5 = _num(summary.get("market_amount_ma5"))
        comparable = bool(
            latest_amount is not None
            and amount_ma5 not in (None, 0)
            and 0.25 <= latest_amount / amount_ma5 <= 4.0
        )
        ready = bool(
            snapshot.get("available")
            and summary.get("is_current")
            and summary.get("breadth_complete")
            and summary.get("amount_complete")
            and summary.get("turnover_complete")
            and history_count >= 30
            and amount_count >= 30
            and turnover_count >= 30
            and comparable
        )
        if ready:
            status = "available"
            message = "全市场日行情已聚合为同口径市场情绪历史"
            action = "直接使用已核验缓存；盘后追加最新交易日"
        elif history_count:
            status = "cache_incomplete"
            message = "已有市场情绪缓存，但历史样本或单位审计尚未完成"
            action = "由StockDailyBar补齐最近30个交易日并重建聚合"
        else:
            status = "unavailable"
            message = "尚无市场情绪历史聚合"
            action = "先采集全市场日行情，再生成MarketSentimentDaily"
        source_keys = [str(item) for item in snapshot.get("sources") or [] if item]
        return {
            "status": status,
            "data_date": snapshot.get("data_date"),
            "history_count": history_count,
            "amount_history_count": amount_count,
            "turnover_history_count": turnover_count,
            "history_coverage_pct": _round(min(history_count / 30 * 100, 100), 1),
            "amount_coverage_pct": _round(min(amount_count / max(history_count, 1) * 100, 100), 1),
            "turnover_coverage_pct": _round(min(turnover_count / max(history_count, 1) * 100, 100), 1),
            "trading_day_age": summary.get("trading_day_age"),
            "amount_comparable": comparable,
            "source": " + ".join(source_keys) if source_keys else "stock_daily_bars -> market_sentiment_daily",
            "source_chain": ["腾讯日线", "FTShare日线", "StockDailyBar", "MarketSentimentDaily"],
            "sources": source_keys,
            "message": message,
            "action": action,
            # A completed market baseline is independent from a separate
            # long-running board/stock archive. Do not surface an old partial
            # archive as if the 30-day decision baseline were still broken.
            "backfill_job": (
                backfill_job
                if not ready or str((backfill_job or {}).get("status")) in {"queued", "running"}
                else None
            ),
        }

    async def _market_flow_evidence(self, trade_day: date | None) -> tuple[list[dict[str, Any]], str | None]:
        """Load the complete industry-flow cache used by direction validation."""
        if trade_day is None:
            return [], None
        try:
            async with async_session() as session:
                flow_day = (await session.execute(
                    select(func.max(IndustryFundFlowDaily.trade_date)).where(
                        IndustryFundFlowDaily.trade_date <= trade_day,
                    )
                )).scalar_one_or_none()
                if flow_day is None:
                    return [], None
                rows = (await session.execute(
                    select(IndustryFundFlowDaily, MarketBoard.name, MarketBoard.source)
                    .outerjoin(
                        MarketBoard,
                        (MarketBoard.board_type == "industry")
                        & (MarketBoard.code == IndustryFundFlowDaily.board_code),
                    )
                    .where(IndustryFundFlowDaily.trade_date == flow_day)
                )).all()
        except Exception as exc:
            await self._record_quality_issue(
                "industry_flow", trade_day, f"行业资金流缓存读取失败：{type(exc).__name__}",
                "保留可用题材快照并重试行业资金流采集。",
            )
            return [], None

        output: list[dict[str, Any]] = []
        for row, board_name, board_source in rows:
            name = str(board_name or row.board_code or "").strip()
            if not name:
                continue
            up = _num(row.up_count)
            down = _num(row.down_count)
            breadth = (up / (up + down) * 100.0) if up is not None and down is not None and up + down else None
            change = _num(row.change_pct)
            source_name = str(board_source or "").lower()
            if "ftshare" in source_name:
                evidence_source = "ftshare_history"
            elif (
                self._last_flow_source.get("source") == "eastmoney"
                and self._last_flow_source.get("trade_date") == flow_day.isoformat()
            ):
                evidence_source = "eastmoney_live"
            elif (
                self._last_flow_source.get("source") == "tencent"
                and self._last_flow_source.get("trade_date") == flow_day.isoformat()
            ):
                evidence_source = "tencent_live"
            else:
                evidence_source = "database_cache"
            output.append({
                "name": name,
                "change_pct": _round(change, 2),
                "main_net_inflow": row.main_net_inflow,
                "breadth": _round(breadth, 1),
                "strength_score": _derived_market_strength(change, breadth),
                "strength_score_source": "change_pct + breadth derived",
                "source": evidence_source,
                "source_detail": str(board_source or "industry_fund_flow_daily"),
                "data_date": flow_day.isoformat(),
            })
        return output, flow_day.isoformat()

    async def _record_quality_issue(
        self,
        component: str,
        trade_day: date | None,
        message: str,
        acquisition_action: str,
        *,
        severity: str = "WARNING",
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            async with async_session() as session:
                existing = (await session.execute(select(DataQualityEvent).where(
                    DataQualityEvent.component == component,
                    DataQualityEvent.research_trade_date == trade_day,
                    DataQualityEvent.status == "OPEN",
                ).limit(1))).scalar_one_or_none()
                if existing is None:
                    session.add(DataQualityEvent(
                        component=component, event_type="DATA_ACQUISITION_GAP", severity=severity,
                        research_trade_date=trade_day, message=message,
                        acquisition_action=acquisition_action, details=details or {}, status="OPEN",
                    ))
                else:
                    existing.message = message
                    existing.acquisition_action = acquisition_action
                    existing.details = details or {}
                    existing.detected_at = datetime.utcnow()
                await session.commit()
        except Exception:
            pass

    async def _resolve_quality_issue(self, component: str, trade_day: date | None) -> None:
        try:
            async with async_session() as session:
                rows = (await session.execute(select(DataQualityEvent).where(
                    DataQualityEvent.component == component,
                    DataQualityEvent.research_trade_date == trade_day,
                    DataQualityEvent.status == "OPEN",
                ))).scalars().all()
                for row in rows:
                    row.status = "RESOLVED"
                    row.resolved_at = datetime.utcnow()
                await session.commit()
        except Exception:
            pass

    @staticmethod
    def _industry_metrics(financial_rows: list[dict[str, Any]]) -> dict[str, Any]:
        revenue_growth = [_num(item.get("revenue_growth")) for item in financial_rows]
        profit_growth = [_num(item.get("deducted_profit_growth")) for item in financial_rows]
        roe = [_num(item.get("roe")) for item in financial_rows]
        gross_margin = [_num(item.get("gross_margin")) for item in financial_rows]
        ocf_quality = [_num(item.get("ocf_to_profit")) for item in financial_rows]
        debt_ratio = [_num(item.get("debt_ratio")) for item in financial_rows]
        net_profit = [_num(item.get("net_profit")) for item in financial_rows]
        valid_profit = [value for value in net_profit if value is not None]
        valid_revenue_growth = [value for value in revenue_growth if value is not None]
        valid_profit_growth = [value for value in profit_growth if value is not None]
        values = {
            "revenue_growth_pct": _round(_trimmed_mean(revenue_growth), 2),
            "deducted_profit_growth_pct": _round(_trimmed_mean(profit_growth), 2),
            "roe_pct": _round(_trimmed_mean(roe), 2),
            "gross_margin_pct": _round(_trimmed_mean(gross_margin), 2),
            "ocf_to_profit": _round(_trimmed_mean(ocf_quality), 3),
            "debt_ratio_pct": _round(_trimmed_mean(debt_ratio), 2),
            "profitable_company_ratio_pct": _round(sum(value > 0 for value in valid_profit) / len(valid_profit) * 100, 1) if valid_profit else None,
            "revenue_growth_positive_ratio_pct": _round(sum(value > 0 for value in valid_revenue_growth) / len(valid_revenue_growth) * 100, 1) if valid_revenue_growth else None,
            "deducted_profit_growth_positive_ratio_pct": _round(sum(value > 0 for value in valid_profit_growth) / len(valid_profit_growth) * 100, 1) if valid_profit_growth else None,
        }
        factors = [
            (_num(values["revenue_growth_pct"]), -20, 30, 25),
            (_num(values["deducted_profit_growth_pct"]), -30, 50, 30),
            (_num(values["roe_pct"]), 0, 20, 20),
            (_num(values["ocf_to_profit"]), 0, 1.5, 15),
            (_num(values["profitable_company_ratio_pct"]), 20, 90, 10),
        ]
        scored = [((_clamp((value - low) / (high - low) * 100)), weight) for value, low, high, weight in factors if value is not None]
        observed_weight = sum(weight for _, weight in scored)
        values["validation_score"] = _round(sum(score * weight for score, weight in scored) / observed_weight, 1) if observed_weight >= 50 else None
        values["observed_metric_weight_pct"] = observed_weight
        return values

    async def _cached_industry_evidence(self, trade_day: date) -> dict[str, dict[str, Any]]:
        try:
            async with async_session() as session:
                rows = (await session.execute(select(IndustryValidationSnapshot).where(
                    IndustryValidationSnapshot.trade_date == trade_day,
                ).order_by(IndustryValidationSnapshot.direction_key))).scalars().all()
        except Exception:
            return {}
        return {
            row.direction_key: {
                "direction_key": row.direction_key, "direction_name": row.direction_name,
                "industries": row.industries, "trade_date": row.trade_date.isoformat(),
                "source_data_date": row.source_data_date.isoformat(),
                "latest_disclosure_date": row.latest_disclosure_date.isoformat() if row.latest_disclosure_date else None,
                "universe_count": row.universe_count, "financial_sample_count": row.financial_sample_count,
                "coverage_pct": row.coverage_pct, "validation_status": row.validation_status,
                "metrics": row.metrics, "source": row.source, "source_grade": row.source_grade,
                "available_time": row.available_time.isoformat() if row.available_time else None,
            }
            for row in rows
        }

    async def _industry_evidence(self, trade_day: date | None, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        if trade_day is None:
            return {}
        cached = await self._cached_industry_evidence(trade_day)
        if len(cached) == len(NATIONAL_DIRECTIONS) and not refresh:
            return cached
        try:
            async with async_session() as session:
                universe_day = (await session.execute(select(func.max(StockUniverseSnapshot.trade_date)).where(
                    StockUniverseSnapshot.trade_date <= trade_day,
                ))).scalar_one_or_none()
                if universe_day is None:
                    await self._record_quality_issue(
                        "industry_universe", trade_day, "缺少可用于产业聚合的股票行业PIT快照",
                        "定时任务将调用全市场PIT行业快照采集；采集成功后自动重建产业验证。",
                    )
                    return cached
                universe_rows = (await session.execute(select(
                    StockUniverseSnapshot.stock_code,
                    StockUniverseSnapshot.industry,
                ).where(StockUniverseSnapshot.trade_date == universe_day))).all()
                ranked_financial = select(
                    FinancialPITSnapshot.stock_code.label("stock_code"),
                    FinancialPITSnapshot.disclosed_at.label("disclosed_at"),
                    FinancialPITSnapshot.roe.label("roe"),
                    FinancialPITSnapshot.gross_margin.label("gross_margin"),
                    FinancialPITSnapshot.revenue_growth.label("revenue_growth"),
                    FinancialPITSnapshot.deducted_profit_growth.label("deducted_profit_growth"),
                    FinancialPITSnapshot.ocf_to_profit.label("ocf_to_profit"),
                    FinancialPITSnapshot.debt_ratio.label("debt_ratio"),
                    FinancialPITSnapshot.net_profit.label("net_profit"),
                    func.row_number().over(
                        partition_by=FinancialPITSnapshot.stock_code,
                        order_by=(desc(FinancialPITSnapshot.disclosed_at), desc(FinancialPITSnapshot.report_date)),
                    ).label("row_number"),
                ).where(FinancialPITSnapshot.disclosed_at <= trade_day).subquery()
                financial_rows = (await session.execute(select(ranked_financial).where(
                    ranked_financial.c.row_number == 1,
                ))).mappings().all()
        except Exception as exc:
            await self._record_quality_issue(
                "industry_financial_pit", trade_day, f"产业财务PIT聚合失败：{type(exc).__name__}",
                "保留上一成功快照并在盘后定时任务自动重试。", severity="ERROR",
            )
            return cached
        universe_by_code = {str(row.stock_code): str(row.industry or "") for row in universe_rows}
        financial_by_code = {str(row["stock_code"]): dict(row) for row in financial_rows}
        generated = shanghai_now()
        output: dict[str, dict[str, Any]] = {}
        for direction in NATIONAL_DIRECTIONS:
            matched_codes = [code for code, industry in universe_by_code.items() if industry and _sector_matches(industry, direction)]
            matched_financial = [financial_by_code[code] for code in matched_codes if code in financial_by_code]
            metrics = self._industry_metrics(matched_financial)
            coverage = len(matched_financial) / max(len(matched_codes), 1) * 100
            revenue_growth = _num(metrics.get("revenue_growth_pct"))
            profit_growth = _num(metrics.get("deducted_profit_growth_pct"))
            cash_quality = _num(metrics.get("ocf_to_profit"))
            if len(matched_financial) < 5 or coverage < 20 or metrics.get("validation_score") is None:
                status = "PARTIAL"
            elif (revenue_growth or 0) > 0 and (profit_growth or 0) > 5 and (cash_quality is None or cash_quality >= 0.7):
                status = "VERIFIED_IMPROVING"
            elif revenue_growth is not None and profit_growth is not None and revenue_growth < 0 and profit_growth < -5:
                status = "VERIFIED_WEAKENING"
            else:
                status = "VERIFIED_MIXED"
            latest_disclosure = max((_date(row.get("disclosed_at")) for row in matched_financial), default=None)
            industries = sorted({universe_by_code[code] for code in matched_codes if universe_by_code[code]})
            item = {
                "direction_key": direction["id"], "direction_name": direction["name"],
                "industries": industries, "trade_date": trade_day.isoformat(),
                "source_data_date": universe_day.isoformat(),
                "latest_disclosure_date": latest_disclosure.isoformat() if latest_disclosure else None,
                "universe_count": len(matched_codes), "financial_sample_count": len(matched_financial),
                "coverage_pct": round(coverage, 1), "validation_status": status,
                "metrics": metrics, "source": "financial_pit_snapshots + stock_universe_snapshots",
                "source_grade": "A", "available_time": generated.isoformat(),
            }
            output[direction["id"]] = item
        try:
            async with async_session() as session:
                for item in output.values():
                    row = (await session.execute(select(IndustryValidationSnapshot).where(
                        IndustryValidationSnapshot.direction_key == item["direction_key"],
                        IndustryValidationSnapshot.trade_date == trade_day,
                        IndustryValidationSnapshot.source_data_date == universe_day,
                    ))).scalar_one_or_none()
                    values = {
                        "direction_name": item["direction_name"], "industries": item["industries"],
                        "latest_disclosure_date": _date(item["latest_disclosure_date"]),
                        "universe_count": item["universe_count"], "financial_sample_count": item["financial_sample_count"],
                        "coverage_pct": item["coverage_pct"], "validation_status": item["validation_status"],
                        "metrics": item["metrics"], "source": item["source"], "source_grade": item["source_grade"],
                        "available_time": _naive(item["available_time"]),
                    }
                    if row is None:
                        session.add(IndustryValidationSnapshot(
                            direction_key=item["direction_key"], trade_date=trade_day,
                            source_data_date=universe_day, **values,
                        ))
                    else:
                        for key, value in values.items():
                            setattr(row, key, value)
                await session.commit()
            await self._resolve_quality_issue("industry_universe", trade_day)
            await self._resolve_quality_issue("industry_financial_pit", trade_day)
        except Exception as exc:
            print(f"Industry validation persistence failed: {type(exc).__name__}")
        return output or cached

    async def _previous_states(self, trade_day: date | None, limit: int = 5) -> list[MarketWayState]:
        if trade_day is None:
            return []
        try:
            async with async_session() as session:
                rows = (await session.execute(
                    select(MarketWayState)
                    .where(MarketWayState.trade_date < trade_day, MarketWayState.contract_version == WORKBENCH_CONTRACT_VERSION)
                    .order_by(desc(MarketWayState.trade_date), desc(MarketWayState.generated_at))
                    .limit(limit)
                )).scalars().all()
            return list(rows)
        except Exception:
            return []

    async def _persist(self, payload: dict[str, Any], v4: dict[str, Any]) -> None:
        trade_day = _date((payload.get("meta") or {}).get("decision_date"))
        if trade_day is None:
            return
        truth = v4["truth"]
        records = truth.get("records") or []
        fingerprints = [str(item.get("fingerprint")) for item in records if item.get("fingerprint")]
        conflict_fingerprints = [str(item.get("fingerprint")) for item in truth.get("conflicts") or [] if item.get("fingerprint")]
        try:
            async with async_session() as session:
                source_keys = {str(item.get("source_key")) for item in records if item.get("source_key")}
                for source_key in source_keys:
                    source_meta = SOURCE_REGISTRY.get(source_key) or SOURCE_REGISTRY["research_media"]
                    source = await session.get(DataSourceRegistry, source_key)
                    if source is None:
                        session.add(DataSourceRegistry(
                            source_key=source_key, name=source_meta["name"], grade=source_meta["grade"],
                            source_type=source_meta["source_type"], official_url=source_meta.get("official_url") or None,
                        ))
                    else:
                        source.grade = source_meta["grade"]
                        source.active = True
                existing_events = set()
                if fingerprints:
                    existing_events = set((await session.execute(
                        select(TruthDataEvent.fingerprint).where(TruthDataEvent.fingerprint.in_(fingerprints))
                    )).scalars().all())
                for item in records:
                    fingerprint = str(item.get("fingerprint") or "")
                    if not fingerprint or fingerprint in existing_events:
                        continue
                    event_time = item.get("event_time") or v4["generated_at"]
                    session.add(TruthDataEvent(
                        fingerprint=fingerprint,
                        event_kind=str(item.get("event_kind") or "unknown"),
                        fact_key=str(item.get("fact_key") or "unknown"),
                        label=str(item.get("label") or "unknown")[:300],
                        source_key=str(item.get("source_key") or "research_media"),
                        source_grade=str(item.get("source_grade") or "B")[:1],
                        evidence_tag=str(item.get("tag") or "FACT"),
                        event_time=_naive(event_time), publish_time=_naive(item.get("publish_time") or event_time),
                        available_time=_naive(item.get("available_time") or v4["generated_at"]),
                        snapshot_time=_naive(item.get("snapshot_time") or v4["generated_at"]),
                        research_trade_date=trade_day,
                        data_cutoff_time=_naive(item.get("data_cutoff_time") or v4["generated_at"]),
                        status=str(item.get("status") or "ACCEPTED"),
                        value_payload={"value": item.get("value")},
                        quality_flags=item.get("quality_flags") or [],
                    ))
                existing_conflicts = set()
                if conflict_fingerprints:
                    existing_conflicts = set((await session.execute(
                        select(TruthDataConflict.fingerprint).where(TruthDataConflict.fingerprint.in_(conflict_fingerprints))
                    )).scalars().all())
                for item in truth.get("conflicts") or []:
                    if item["fingerprint"] in existing_conflicts:
                        continue
                    session.add(TruthDataConflict(
                        fingerprint=item["fingerprint"], conflict_type=item["type"], fact_key=item["fact_key"],
                        research_trade_date=trade_day, source_keys=item["source_keys"], conflicting_values=item["values"],
                        resolution=item["resolution"], confidence_penalty=item["confidence_penalty"], status=item["status"],
                        detected_at=_naive(v4["generated_at"]),
                    ))
                policy_records = []
                for direction in v4["national_direction_radar"]["directions"]:
                    for policy in direction.get("policies") or []:
                        fingerprint = _stable_hash([direction["id"], policy.get("title"), policy.get("url")])
                        policy_records.append((fingerprint, direction, policy))
                existing_policies = set()
                if policy_records:
                    existing_policies = set((await session.execute(
                        select(PolicyTransmissionRecord.fingerprint).where(PolicyTransmissionRecord.fingerprint.in_([item[0] for item in policy_records]))
                    )).scalars().all())
                for fingerprint, direction, policy in policy_records:
                    if fingerprint in existing_policies:
                        continue
                    session.add(PolicyTransmissionRecord(
                        fingerprint=fingerprint, direction_key=direction["id"], direction_name=direction["name"],
                        policy_title=str(policy.get("title") or "")[:500], policy_url=str(policy.get("url") or "")[:800] or None,
                        source_key=source_identity(policy.get("source"))[0], source_grade="S",
                        published_at=_naive(policy.get("published_at")), available_time=_naive(v4["generated_at"]),
                        research_trade_date=trade_day, policy_level=policy.get("level") or "L2",
                        marginal_state=direction["marginal_state"], max_verified_level=direction["max_verified_level"],
                        transmission_state=direction["transmission_state"], stages=direction["stages"], evidence=direction["evidence"],
                    ))
                existing_state = (await session.execute(
                    select(MarketWayState).where(
                        MarketWayState.trade_date == trade_day,
                        MarketWayState.phase == v4["phase"],
                        MarketWayState.contract_version == WORKBENCH_CONTRACT_VERSION,
                    )
                )).scalar_one_or_none()
                state_payload = {"market_way_v4": v4, "meta": payload.get("meta") or {}}
                snapshot_hash = _stable_hash({
                    "trade_date": trade_day.isoformat(), "phase": v4["phase"],
                    "truth": truth.get("status"), "momentum": v4["momentum"],
                    "directions": [(item["id"], item["score"], item["marginal_state"]) for item in v4["national_direction_radar"]["directions"]],
                })
                values = {
                    "snapshot_hash": snapshot_hash, "truth_status": truth["status"],
                    "way_state": v4["chain"][1]["status"], "order_state": v4["momentum"]["order_state"],
                    "momentum_state": v4["momentum"]["state"], "risk_appetite": v4["capital_migration"]["risk_appetite"],
                    "pricing_force": v4["market_force"]["type"], "ai_conclusion": v4["final_decision"]["code"],
                    "confidence_pct": v4["final_decision"]["confidence_pct"], "payload": state_payload,
                    "generated_at": _naive(v4["generated_at"]),
                }
                if existing_state is None:
                    session.add(MarketWayState(
                        trade_date=trade_day, phase=v4["phase"], contract_version=WORKBENCH_CONTRACT_VERSION, **values,
                    ))
                else:
                    for key, value in values.items():
                        setattr(existing_state, key, value)
                await session.commit()
        except Exception as exc:
            print(f"Market way V4 persistence failed: {type(exc).__name__}")

    async def decorate(self, payload: dict[str, Any], *, refresh_policy: bool = False, persist: bool = True) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return payload
        result = deepcopy(payload)
        trade_day = _date((result.get("meta") or {}).get("decision_date"))
        self._last_trade_day = trade_day or self._last_trade_day
        async with self._lock:
            policy_context, previous, industry_evidence, market_history, market_flow_result = await asyncio.gather(
                self._policy_context(refresh=refresh_policy),
                self._previous_states(trade_day),
                self._industry_evidence(trade_day, refresh=refresh_policy),
                self._market_history_status(),
                self._market_flow_evidence(trade_day),
            )
            market_flow_evidence, market_flow_date = market_flow_result
            v4 = build_market_way_v4(
                result,
                policy_context,
                previous,
                industry_evidence,
                market_flow_evidence,
            )
            v4["data_pipeline"] = {
                "policy": {
                    "status": "available" if policy_context.get("available") else "refresh_pending",
                    "updated_at": policy_context.get("updated_at"),
                    "source": "中国政府网/国家发展改革委/中国人民银行",
                },
                "industry_financial": {
                    "status": "available" if industry_evidence else "refresh_pending",
                    "direction_count": len(industry_evidence),
                    "verified_count": sum(str(item.get("validation_status") or "").startswith("VERIFIED") for item in industry_evidence.values()),
                    "source_data_date": max((str(item.get("source_data_date") or "") for item in industry_evidence.values()), default=None),
                    "source": "股票行业PIT + 公司公告日财务PIT",
                },
                "market_history": market_history,
                "industry_flow": {
                    "status": "available" if market_flow_evidence else "refresh_pending",
                    "board_count": len(market_flow_evidence),
                    "data_date": market_flow_date,
                    "coverage_pct": _round(min(len(market_flow_evidence) / 500 * 100, 100), 1),
                    "cache_used": bool(market_flow_evidence and any(item.get("source") in {"database_cache", "ftshare_history"} for item in market_flow_evidence)),
                    "sources": sorted({str(item.get("source")) for item in market_flow_evidence if item.get("source")}),
                    "source_chain": ["东方财富盘中/盘后板块流", "腾讯行情代理", "FTShare历史板块流", "IndustryFundFlowDaily同口径缓存"],
                    "source": "、".join(sorted({str(item.get("source")) for item in market_flow_evidence if item.get("source")})) or "IndustryFundFlowDaily同口径缓存",
                },
                "market": {
                    "status": "available" if result.get("available") else "refresh_pending",
                    "data_date": (result.get("meta") or {}).get("decision_date"),
                    "is_realtime": bool((result.get("meta") or {}).get("is_realtime")),
                    "source": (result.get("meta") or {}).get("source"),
                },
                "refresh_job": dict(self._refresh_status),
                "rule": "实时源失败先读同口径最近成功缓存；采集任务持续重试，页面不以示例值补空。",
            }
            needs_refresh = bool(
                not policy_context.get("available")
                or len(industry_evidence) < len(NATIONAL_DIRECTIONS)
                or market_history.get("status") != "available"
                or not market_flow_evidence
            )
            if needs_refresh and not refresh_policy:
                last_update = parse_datetime(self._refresh_status.get("updated_at"))
                recently_attempted = bool(last_update and shanghai_now() - last_update < timedelta(minutes=10))
                if not self._refresh_task or self._refresh_task.done():
                    if not recently_attempted:
                        self._refresh_status = {
                            "status": "queued",
                            "stage": "queued",
                            "progress": 0,
                            "message": "数据闭环采集任务已排队",
                            "updated_at": shanghai_now().isoformat(),
                        }
                        self._refresh_task = asyncio.create_task(self._run_source_refresh())
            # Always copy the authoritative in-memory status after the task
            # decision. A second request arriving while refresh is running must
            # never observe the old idle state.
            v4["data_pipeline"]["refresh_job"] = dict(self._refresh_status)
            self._last_pipeline = deepcopy(v4["data_pipeline"])
            result["market_way_v4"] = v4
            _apply_truth_gate(result, v4["truth"])
            if v4["truth"]["status"] == "FAIL":
                v4["final_decision"].update({"code": "NO_TRADE", "label": "真值阻断", "confidence_pct": min(v4["final_decision"].get("confidence_pct", 0), 35.0)})
            elif v4["truth"]["status"] == "LIMITED" and v4["final_decision"].get("code") == "EXECUTION_READY":
                v4["final_decision"].update({"code": "TRIGGER_WAIT", "label": "证据降级，等待触发", "confidence_pct": min(v4["final_decision"].get("confidence_pct", 0), 59.0)})
            if persist:
                await self._persist(result, v4)
        return result

    async def _run_source_refresh(self) -> None:
        self._refresh_status = {"status": "running", "stage": "policy", "progress": 10, "message": "刷新官方政策源", "updated_at": shanghai_now().isoformat()}
        try:
            await self._policy_context(refresh=True)
            warnings: list[str] = []
            self._refresh_status.update({"stage": "market", "progress": 25, "message": "采集全市场日行情并重建情绪历史"})
            from services.history_cache import history_cache
            from services.strategic_market_data import strategic_market_data_service
            market_result: dict[str, Any] = {}
            try:
                market_result = await asyncio.wait_for(
                    history_cache.cache_current_stock_bars(), timeout=180,
                )
            except Exception as exc:
                market_result = {"status": "unavailable", "error": type(exc).__name__}
            self._refresh_status["market_result"] = market_result
            if market_result.get("status") not in {"success", "partial"}:
                warnings.append("全市场日行情采集未完成，继续使用已有同口径缓存")
                await self._record_quality_issue(
                    "market_daily_bars", self._last_trade_day,
                    "全市场日行情采集失败或没有可验证行情时间戳",
                    "重试东方财富全市场快照；若仍失败则从已有StockDailyBar聚合，不填造成交额。",
                    severity="ERROR",
                    details=market_result,
                )
            trade_day = _date(market_result.get("data_date")) or self._last_trade_day or shanghai_now().date()

            # Rebuild the decision baseline from already cached bars first. A
            # source refresh should not fan out into thousands of requests just
            # because MarketSentimentDaily was stale; the canonical bars may
            # already contain every required day.
            history_backfill: dict[str, Any] | None = None
            try:
                try:
                    strategic_result = await asyncio.wait_for(
                        strategic_market_data_service.sync_recent(days=30), timeout=120,
                    )
                except Exception as exc:
                    strategic_result = {"status": "unavailable", "error": type(exc).__name__}
                self._refresh_status["strategic_result"] = strategic_result

                history_after = await strategic_market_data_service.history(limit=30)
                summary_after = history_after.get("summary") or {}
                history_ready = bool(
                    history_after.get("available")
                    and summary_after.get("is_current")
                    and summary_after.get("breadth_complete")
                    and summary_after.get("amount_complete")
                    and summary_after.get("turnover_complete")
                    and int(history_after.get("count") or 0) >= 30
                    and int(summary_after.get("amount_history_count") or 0) >= 30
                    and int(summary_after.get("turnover_history_count") or 0) >= 30
                )
                if strategic_result.get("status") not in {"success", "partial"}:
                    warnings.append("市场情绪历史聚合未完成，后台将继续重试")
                if not history_ready:
                    self._refresh_status.update({
                        "stage": "history_backfill",
                        "progress": 30,
                        "message": "补采约45个自然日（至少30个交易日），重建市场情绪基线",
                    })
                    history_backfill = await history_cache.ensure_recent_backfill(days=45)
                    self._refresh_status["history_backfill"] = history_backfill
                    if history_backfill.get("status") in {"queued", "running"}:
                        warnings.append("市场情绪历史正在补采，完成后自动重建")
            except Exception as exc:
                warnings.append(f"市场情绪历史补采未提交: {type(exc).__name__}")

            self._refresh_status.update({"stage": "industry_flow", "progress": 40, "message": "刷新全行业资金流缓存"})
            try:
                industry_flow_result = await asyncio.wait_for(
                    history_cache.cache_current_industry_flow(
                        trade_date=trade_day,
                        verified_trade_date=market_result.get("status") in {"success", "partial"},
                    ), timeout=60,
                )
            except Exception as exc:
                industry_flow_result = {"status": "unavailable", "error": type(exc).__name__}
            self._refresh_status["industry_flow_result"] = industry_flow_result
            self._last_flow_source = dict(industry_flow_result)
            if industry_flow_result.get("status") == "cache":
                warnings.append(
                    f"行业资金流实时源未返回，继续使用{industry_flow_result.get('trade_date') or '最近'}已核验缓存"
                )
            elif industry_flow_result.get("status") not in {"success", "partial"}:
                warnings.append("行业资金流未完成，保留题材快照并重试")

            self._refresh_status.update({"stage": "universe", "progress": 50, "message": "刷新全市场行业PIT快照"})
            from services.pit_market_data import pit_market_data_service
            try:
                if not (market_result.get("pit_universe") or {}).get("records"):
                    await asyncio.wait_for(pit_market_data_service.capture_universe(), timeout=120)
            except Exception as exc:
                self._refresh_status["universe_warning"] = type(exc).__name__
                warnings.append("股票行业PIT未完成")
            self._refresh_status.update({"stage": "financial", "progress": 68, "message": "刷新公司公告日财务PIT"})
            from services.stock_features import stock_feature_service
            try:
                await asyncio.wait_for(stock_feature_service.capture_financial_pit(), timeout=240)
            except Exception as exc:
                self._refresh_status["financial_warning"] = type(exc).__name__
                warnings.append("公司财务PIT未完成")
            self._refresh_status.update({"stage": "industry", "progress": 88, "message": "重建产业与盈利验证"})
            await self._industry_evidence(trade_day, refresh=True)
            self._refresh_status = {
                "status": "completed" if not warnings else "completed_with_gaps",
                "stage": "completed",
                "progress": 100,
                "message": "V4数据源与缓存已更新" if not warnings else "V4已更新，仍有数据源在后台重试",
                "warnings": _unique(warnings),
                "history_backfill": history_backfill,
                "industry_flow_result": industry_flow_result,
                "updated_at": shanghai_now().isoformat(),
            }
        except Exception as exc:
            self._refresh_status = {"status": "failed", "stage": self._refresh_status.get("stage"), "progress": self._refresh_status.get("progress", 0), "message": f"数据刷新失败：{type(exc).__name__}", "updated_at": shanghai_now().isoformat()}

    async def refresh_sources(self, *, background: bool = True) -> dict[str, Any]:
        if self._refresh_task and not self._refresh_task.done():
            return dict(self._refresh_status)
        if background:
            self._refresh_task = asyncio.create_task(self._run_source_refresh())
            await asyncio.sleep(0)
            return dict(self._refresh_status)
        await self._run_source_refresh()
        return dict(self._refresh_status)

    async def refresh_policy_source(self) -> dict[str, Any]:
        context = await self._policy_context(refresh=True)
        return {
            "status": "available" if context.get("available") else "unavailable",
            "updated_at": context.get("updated_at"),
            "policy_count": len(context.get("policy_items") or []),
            "source_status": context.get("source_status") or {},
        }

    def refresh_status(self) -> dict[str, Any]:
        return dict(self._refresh_status)

    async def data_status(self) -> dict[str, Any]:
        """Return lightweight acquisition progress without rebuilding V4 per poll."""
        if not self._last_pipeline:
            payload = await self.current(force=False)
            pipeline = ((payload.get("market_way_v4") or {}).get("data_pipeline") or {})
            self._last_pipeline = deepcopy(pipeline)
        pipeline = deepcopy(self._last_pipeline)
        persisted_backfill = None
        try:
            from services.history_cache import history_cache

            persisted_backfill = await history_cache.latest_backfill_status()
            if isinstance(pipeline.get("market_history"), dict):
                # Re-read the compact summary so a completed cache repair is
                # reflected immediately. The summary decides whether an old
                # archive run is relevant to the current decision baseline.
                try:
                    pipeline["market_history"] = await self._market_history_status()
                except Exception:
                    pass
        except Exception:
            pass
        refresh_job = dict(self.refresh_status())
        market_history = pipeline.get("market_history")
        if persisted_backfill is not None and isinstance(market_history, dict) and market_history.get("status") != "available":
            # Older in-memory refresh objects can retain `running` after the
            # durable backfill row has reached partial/completed.
            refresh_job["history_backfill"] = persisted_backfill
        elif isinstance(market_history, dict) and market_history.get("status") == "available":
            # A background backfill can finish after the source refresh has
            # already written its in-memory status. Reconcile that durable
            # result and remove the now-resolved warning before the next poll.
            if persisted_backfill is not None and refresh_job.get("history_backfill") is not None:
                refresh_job["history_backfill"] = persisted_backfill
            warnings = refresh_job.get("warnings")
            if isinstance(warnings, list):
                remaining = [
                    item for item in warnings
                    if "市场情绪历史正在补采" not in str(item)
                ]
                refresh_job["warnings"] = remaining
                if refresh_job.get("status") == "completed_with_gaps" and not remaining:
                    refresh_job.update({
                        "status": "completed",
                        "message": "V4数据源与缓存已更新",
                    })
        pipeline["refresh_job"] = refresh_job
        return {"pipeline": pipeline, "refresh_job": refresh_job}

    async def current(self, *, force: bool = False) -> dict[str, Any]:
        from services.market_decision_workbench import market_decision_workbench_service

        return await market_decision_workbench_service.get(force=force)

    async def truth_status(self, *, force: bool = False) -> dict[str, Any]:
        payload = await self.current(force=force)
        return (payload.get("market_way_v4") or {}).get("truth") or {}

    async def conflicts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        async with async_session() as session:
            rows = (await session.execute(
                select(TruthDataConflict).order_by(desc(TruthDataConflict.detected_at)).limit(limit)
            )).scalars().all()
        return [{
            "id": row.id, "fingerprint": row.fingerprint, "type": row.conflict_type,
            "fact_key": row.fact_key, "trade_date": row.research_trade_date.isoformat(),
            "source_keys": row.source_keys, "values": row.conflicting_values,
            "resolution": row.resolution, "confidence_penalty": row.confidence_penalty,
            "status": row.status, "detected_at": row.detected_at.isoformat() if row.detected_at else None,
        } for row in rows]

    async def quality_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        async with async_session() as session:
            rows = (await session.execute(
                select(DataQualityEvent).order_by(desc(DataQualityEvent.detected_at)).limit(limit)
            )).scalars().all()
        return [{
            "id": row.id, "component": row.component, "event_type": row.event_type,
            "severity": row.severity,
            "research_trade_date": row.research_trade_date.isoformat() if row.research_trade_date else None,
            "source_key": row.source_key, "message": row.message,
            "acquisition_action": row.acquisition_action, "details": row.details,
            "status": row.status, "detected_at": row.detected_at.isoformat() if row.detected_at else None,
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        } for row in rows]

    async def save_judgment(self, request: dict[str, Any]) -> dict[str, Any]:
        action = str(request.get("user_action") or "").strip().upper()
        if action not in JUDGMENT_ACTIONS:
            raise ValueError("user_action 仅支持 BULLISH、NEUTRAL、BEARISH、WAIT、NO_TRADE")
        current = await self.current(force=False)
        v4 = current.get("market_way_v4") or {}
        trade_day = _date(request.get("trade_date") or (current.get("meta") or {}).get("decision_date"))
        if trade_day is None:
            raise ValueError("当前没有可记录的交易日")
        phase = str(request.get("phase") or v4.get("phase") or "current")[:30]
        user_key = str(request.get("user_key") or "default")[:80]
        evidence = request.get("user_evidence") or []
        if isinstance(evidence, str):
            evidence = [item.strip() for item in evidence.split("\n") if item.strip()]
        if not isinstance(evidence, list):
            raise ValueError("user_evidence 必须是字符串数组")
        ai_judgment = {
            "conclusion": (v4.get("final_decision") or {}).get("code"),
            "momentum": (v4.get("momentum") or {}).get("state"),
            "risk_appetite": (v4.get("capital_migration") or {}).get("risk_appetite"),
            "confidence_pct": (v4.get("final_decision") or {}).get("confidence_pct"),
            "snapshot_time": v4.get("generated_at"),
        }
        async with async_session() as session:
            row = (await session.execute(select(MarketWayJudgment).where(
                MarketWayJudgment.trade_date == trade_day,
                MarketWayJudgment.phase == phase,
                MarketWayJudgment.user_key == user_key,
            ))).scalar_one_or_none()
            if row is None:
                row = MarketWayJudgment(
                    trade_date=trade_day, phase=phase, user_key=user_key, ai_judgment=ai_judgment,
                    user_action=action, user_judgment=str(request.get("user_judgment") or "")[:4000] or None,
                    user_evidence=[str(item)[:500] for item in evidence[:12]],
                )
                session.add(row)
            else:
                row.ai_judgment = ai_judgment
                row.user_action = action
                row.user_judgment = str(request.get("user_judgment") or "")[:4000] or None
                row.user_evidence = [str(item)[:500] for item in evidence[:12]]
                row.validation_status = "PENDING"
                row.actual_result = None
                row.correct_party = None
                row.error_type = None
                row.validated_at = None
            await session.commit()
            await session.refresh(row)
        return self._judgment_dict(row)

    @staticmethod
    def _judgment_dict(row: MarketWayJudgment) -> dict[str, Any]:
        return {
            "id": row.id, "trade_date": row.trade_date.isoformat(), "phase": row.phase,
            "user_key": row.user_key, "ai_judgment": row.ai_judgment,
            "user_action": row.user_action, "user_judgment": row.user_judgment,
            "user_evidence": row.user_evidence, "actual_result": row.actual_result,
            "validation_status": row.validation_status, "correct_party": row.correct_party,
            "error_type": row.error_type, "validated_at": row.validated_at.isoformat() if row.validated_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    async def judgments(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with async_session() as session:
            rows = (await session.execute(
                select(MarketWayJudgment).order_by(desc(MarketWayJudgment.trade_date), desc(MarketWayJudgment.updated_at)).limit(limit)
            )).scalars().all()
        return [self._judgment_dict(row) for row in rows]

    async def validate_judgments(self) -> dict[str, Any]:
        async with async_session() as session:
            pending = (await session.execute(
                select(MarketWayJudgment).where(MarketWayJudgment.validation_status == "PENDING").order_by(MarketWayJudgment.trade_date)
            )).scalars().all()
            validated = 0
            for row in pending:
                outcome = (await session.execute(
                    select(MarketWayState).where(
                        MarketWayState.trade_date > row.trade_date,
                        MarketWayState.contract_version == WORKBENCH_CONTRACT_VERSION,
                    ).order_by(MarketWayState.trade_date, MarketWayState.generated_at).limit(1)
                )).scalar_one_or_none()
                if outcome is None:
                    continue
                direction = ((outcome.payload or {}).get("market_way_v4") or {}).get("momentum", {}).get("direction")
                user_expected = "UP" if row.user_action == "BULLISH" else "DOWN" if row.user_action == "BEARISH" else "MIXED"
                ai_code = str((row.ai_judgment or {}).get("conclusion") or "")
                ai_expected = "UP" if ai_code in {"EXECUTION_READY", "TRIGGER_WAIT"} else "MIXED" if ai_code in {"WATCH", "RESEARCH", "NO_TRADE"} else "UNKNOWN"
                user_correct = user_expected == direction or (row.user_action in {"WAIT", "NO_TRADE"} and direction != "UP")
                ai_correct = ai_expected == direction or (ai_code == "NO_TRADE" and direction != "UP")
                row.actual_result = {"next_trade_date": outcome.trade_date.isoformat(), "momentum_direction": direction, "state": outcome.momentum_state}
                row.validation_status = "VALIDATED"
                row.correct_party = "BOTH" if user_correct and ai_correct else "USER" if user_correct else "AI" if ai_correct else "NEITHER"
                row.error_type = None if user_correct and ai_correct else "STATE_DIRECTION_MISMATCH"
                row.validated_at = datetime.utcnow()
                validated += 1
            await session.commit()
        return {"validated": validated, "pending": len(pending) - validated, "method": "以下一交易日V4势方向作状态验证，不等同于个股收益归因。"}


market_way_v4_service = MarketWayV4Service()
