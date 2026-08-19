"""V4 data-truth primitives shared by live decisions and historical replay."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, time
from typing import Any, Iterable
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SOURCE_GRADE_WEIGHT = {"S": 1.0, "A": 0.92, "B": 0.72, "C": 0.35}
OUTPUT_TAGS = {"FACT", "INFERENCE", "SCENARIO"}

SOURCE_REGISTRY: dict[str, dict[str, str]] = {
    "gov_cn": {"name": "中国政府网", "grade": "S", "source_type": "official_policy", "official_url": "https://www.gov.cn/zhengce/"},
    "ndrc": {"name": "国家发展改革委", "grade": "S", "source_type": "official_policy", "official_url": "https://www.ndrc.gov.cn/"},
    "pboc": {"name": "中国人民银行", "grade": "S", "source_type": "official_policy", "official_url": "https://www.pbc.gov.cn/"},
    "exchange": {"name": "证券交易所/法定公告", "grade": "S", "source_type": "official_disclosure", "official_url": ""},
    "eastmoney": {"name": "东方财富行情", "grade": "A", "source_type": "market_data", "official_url": "https://quote.eastmoney.com/"},
    "tencent": {"name": "腾讯行情", "grade": "A", "source_type": "market_data", "official_url": "https://gu.qq.com/"},
    "ftshare_mcp": {"name": "FTShare MCP", "grade": "A", "source_type": "market_data", "official_url": ""},
    "sina": {"name": "新浪财经行情", "grade": "A", "source_type": "market_data", "official_url": "https://finance.sina.com.cn/"},
    "database_cache": {"name": "系统核验缓存", "grade": "A", "source_type": "internal_cache", "official_url": ""},
    "research_media": {"name": "可靠媒体/研究资料", "grade": "B", "source_type": "research", "official_url": ""},
    "social_clue": {"name": "社交媒体线索", "grade": "C", "source_type": "sentiment_clue", "official_url": ""},
}


def finite_number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_datetime(value: Any, *, default_time: time = time(0, 0)) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, default_time)
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(raw[:10]), default_time)
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def source_identity(raw_source: Any) -> tuple[str, dict[str, str]]:
    value = str(raw_source or "").strip()
    lowered = value.lower()
    if "中国政府" in value or "gov.cn" in lowered:
        key = "gov_cn"
    elif "发展改革" in value or "ndrc" in lowered:
        key = "ndrc"
    elif "人民银行" in value or "pbc" in lowered:
        key = "pboc"
    elif any(token in value for token in ("上交所", "深交所", "北交所", "证监会", "公司公告", "法定公告")):
        key = "exchange"
    elif "eastmoney" in lowered or "东方财富" in value:
        key = "eastmoney"
    elif "tencent" in lowered or "腾讯" in value:
        key = "tencent"
    elif "ftshare" in lowered:
        key = "ftshare_mcp"
    elif "sina" in lowered or "新浪" in value:
        key = "sina"
    elif any(token in lowered for token in ("cache", "database", "pit")):
        key = "database_cache"
    elif any(token in lowered for token in ("social", "weibo", "x.com")):
        key = "social_clue"
    else:
        key = "research_media"
    return key, dict(SOURCE_REGISTRY[key])


def evidence_fingerprint(record: dict[str, Any]) -> str:
    stable = {
        key: record.get(key)
        for key in (
            "event_kind", "fact_key", "source_key", "event_time", "publish_time",
            "research_trade_date", "value",
        )
    }
    raw = json.dumps(stable, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def tagged_statement(
    text: str,
    tag: str,
    *,
    evidence_ids: Iterable[str] = (),
    confidence_pct: float | None = None,
) -> dict[str, Any]:
    normalized_tag = str(tag or "").upper()
    if normalized_tag not in OUTPUT_TAGS:
        raise ValueError("AI输出标签仅支持 FACT、INFERENCE、SCENARIO")
    return {
        "tag": normalized_tag,
        "text": str(text),
        "evidence_ids": list(dict.fromkeys(str(item) for item in evidence_ids if item)),
        "confidence_pct": round(confidence_pct, 1) if confidence_pct is not None else None,
    }


class PointInTimeGuard:
    """Reject evidence unavailable at the decision timestamp."""

    @staticmethod
    def evaluate(record: dict[str, Any], decision_time: Any) -> dict[str, Any]:
        decision = parse_datetime(decision_time)
        event = parse_datetime(record.get("event_time"))
        published = parse_datetime(record.get("publish_time"))
        available = parse_datetime(record.get("available_time"))
        snapshot = parse_datetime(record.get("snapshot_time")) or decision
        violations: list[str] = []
        if decision is None:
            violations.append("DECISION_TIME_MISSING")
        if event is None:
            violations.append("EVENT_TIME_MISSING")
        if published is None:
            violations.append("PUBLISH_TIME_MISSING")
        if available is None:
            violations.append("AVAILABLE_TIME_MISSING")
        if snapshot is None:
            violations.append("SNAPSHOT_TIME_MISSING")
        if decision and available and available > decision:
            violations.append("FUTURE_AVAILABLE_DATA")
        if decision and event and event > decision:
            violations.append("FUTURE_EVENT_DATA")
        if published and available and published > available:
            violations.append("AVAILABLE_BEFORE_PUBLICATION")
        if decision and snapshot and snapshot > decision:
            violations.append("SNAPSHOT_AFTER_DECISION")
        return {
            "allowed": not violations,
            "decision_time": decision.isoformat() if decision else None,
            "event_time": event.isoformat() if event else None,
            "publish_time": published.isoformat() if published else None,
            "available_time": available.isoformat() if available else None,
            "snapshot_time": snapshot.isoformat() if snapshot else None,
            "violations": violations,
        }

    @classmethod
    def filter(cls, records: Iterable[dict[str, Any]], decision_time: Any) -> dict[str, Any]:
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for item in records:
            result = cls.evaluate(item, decision_time)
            normalized = {**item, "pit": result, "status": "ACCEPTED" if result["allowed"] else "REJECTED_PIT"}
            (accepted if result["allowed"] else rejected).append(normalized)
        return {
            "accepted": accepted,
            "rejected": rejected,
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "passed": not rejected,
        }


def detect_data_conflicts(records: Iterable[dict[str, Any]], trade_date: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in records:
        if item.get("status") != "ACCEPTED":
            continue
        grouped[str(item.get("fact_key") or "")].append(item)
    conflicts: list[dict[str, Any]] = []
    for fact_key, rows in grouped.items():
        values: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            value = json.dumps(row.get("value"), ensure_ascii=True, sort_keys=True, default=str)
            values[value].append(row)
        if len(values) <= 1:
            continue
        ranked = sorted(
            rows,
            key=lambda row: (
                SOURCE_GRADE_WEIGHT.get(str(row.get("source_grade") or "C"), 0.0),
                str(row.get("available_time") or ""),
            ),
            reverse=True,
        )
        preferred = ranked[0]
        source_keys = list(dict.fromkeys(str(row.get("source_key") or "") for row in rows))
        conflicting_values = [json.loads(value) for value in values]
        raw = json.dumps({"fact_key": fact_key, "trade_date": trade_date, "values": conflicting_values}, sort_keys=True, default=str)
        conflicts.append({
            "fingerprint": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "type": "DATA_CONFLICT",
            "fact_key": fact_key,
            "source_keys": source_keys,
            "values": conflicting_values,
            "preferred_source": preferred.get("source_key"),
            "resolution": "按来源等级、更新时间和统计口径选择优先证据；冲突解除前降低置信度。",
            "confidence_penalty": min(30.0, 8.0 + (len(values) - 2) * 4.0),
            "status": "OPEN",
        })
    return conflicts
