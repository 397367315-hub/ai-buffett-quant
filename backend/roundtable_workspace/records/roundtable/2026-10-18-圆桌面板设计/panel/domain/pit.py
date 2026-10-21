"""时点可见性（REPLAY-001）：给定知识截点，哪些事实「当时」已经可知。

pit_visibility_policy_v1（版本化工程政策）：
  - time_precision=exact：available_at ≤ cutoff 才可见；
  - 只有发布日期（date_only，或 available_at 为空但有 publication_date）：不默认当天 00:00 已知；
    保守推迟到发布日之后的下一个交易时段开盘（09:30 上海）才可见；没有交易日历时按发布日次日 09:30；
    推迟后仍晚于截点 → 排除，原因 publication_time_unproven；
  - available_at 与 publication_date 都缺失（time_precision=unknown）：排除（publication_time_unproven），
    first_observed_at 只说明本系统何时第一次看到，不能倒推发布时刻；
  - 报告期（report_period）永远不当作可知时间；
  - 更正/修订（revises_fact_id）：截点时最新可见的版本替代旧版本；旧版本保留在历史里、不改写；
  - 客户端声称的 available_at 不被信任：与来源记录冲突 → source_time_conflict。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from . import clock

POLICY_VERSION = "pit_visibility_policy_v1"


def effective_available_at(fact: dict, calendar=None) -> tuple[str | None, str]:
    """返回 (按政策的可见时刻, 依据)。None 表示无法证明可知时刻。"""
    prec = fact.get("time_precision")
    av = fact.get("available_at")
    if av and prec in (None, "exact", "timestamp"):
        return av, "exact_available_at"
    pub = fact.get("publication_date")
    if prec in ("date", "date_only") or (not av and pub):
        day = pub or (av[:10] if av else None)
        if not day:
            return None, "publication_time_unproven"
        nxt = calendar.next_session(day) if calendar is not None else None
        if nxt is None:
            nxt = (datetime.fromisoformat(day) + timedelta(days=1)).strftime("%Y-%m-%d")
        sh = datetime.combine(datetime.fromisoformat(nxt).date(), time(9, 30), tzinfo=clock.SHANGHAI)
        return clock.utc_iso(sh), "date_only_shifted_to_next_session_open"
    return None, "publication_time_unproven"


def visible(facts: list[dict], cutoff_at: str, calendar=None) -> dict:
    """facts 每项至少有 fact_id；返回 {visible: [...], excluded: {fact_id: reason}, superseded: {...}}。"""
    cut = clock.parse_utc(cutoff_at)
    ok: dict[str, dict] = {}
    excluded: dict[str, str] = {}
    for f in facts:
        at, basis = effective_available_at(f, calendar)
        if at is None:
            excluded[f["fact_id"]] = "publication_time_unproven"
            continue
        if clock.parse_utc(at) > cut:
            excluded[f["fact_id"]] = ("not_yet_available" if basis == "exact_available_at"
                                      else "publication_time_unproven")
            continue
        ok[f["fact_id"]] = {**f, "_effective_available_at": at, "_basis": basis}
    superseded: dict[str, str] = {}
    for fid, f in list(ok.items()):
        tgt = f.get("revises_fact_id")
        if tgt and tgt in ok:
            superseded[tgt] = fid
    vis = [f for fid, f in ok.items() if fid not in superseded]
    return {"policy_version": POLICY_VERSION, "cutoff_at": cutoff_at, "visible": vis, "excluded": excluded,
            "superseded": superseded, "report_period_used_as_available_at": False, "synthetic_midnight_assigned": False}


def value_of(view: dict, base_fact_id: str):
    """某个事实（含其修订）在截点时的可见值。"""
    for f in view["visible"]:
        if f["fact_id"] == base_fact_id or f.get("revises_fact_id") == base_fact_id:
            return f.get("value")
    return None


def check_client_claim(claimed_available_at: str | None, source_fact: dict, cutoff_at: str) -> dict:
    """客户端带来的事实：只认来源记录的可知时刻；声称时间与来源冲突 → source_time_conflict。"""
    src_at, _ = effective_available_at(source_fact)
    conflict = bool(claimed_available_at and src_at and clock.parse_utc(claimed_available_at) != clock.parse_utc(src_at))
    future = src_at is None or clock.parse_utc(src_at) > clock.parse_utc(cutoff_at)
    return {"source_available_at": src_at, "claimed_available_at": claimed_available_at, "conflict": conflict,
            "visible": (not future) and not conflict}
