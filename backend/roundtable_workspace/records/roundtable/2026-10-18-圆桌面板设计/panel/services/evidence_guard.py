"""客户端引用证据的服务端核验（REPLAY-002 / AC-059）。

客户端可以在日记、计划规则里引用 evidence_ids，但服务端只认自己的证据记录：
  - 证据不存在 / 不在当前命名空间 → 422 EVIDENCE_NOT_VISIBLE；
  - 训练上下文里引用截点之后才可知的证据（不论客户端声称何时可知）→ 422 SOURCE_TIME_CONFLICT；
  - 只有日期精度、可知时刻未证明的证据在训练上下文里同样按截点外处理（pit_visibility_policy_v1）。
"""
from __future__ import annotations

import json

from ..domain import clock, pit
from ..web.errors import ApiError


def check_evidence_refs(db, evidence_ids: list[str], replay_id: str | None, cutoff_at: str | None,
                        field: str = "evidence_ids") -> None:
    if not evidence_ids:
        return
    bad, future = [], []
    for eid in evidence_ids:
        r = db.one("SELECT canonical_payload, replay_id, snapshot_id FROM evidence WHERE evidence_id=?", (eid,))
        if r is None:
            bad.append(eid)
            continue
        e = json.loads(r["canonical_payload"])
        if r["replay_id"] is not None and r["replay_id"] != replay_id:
            bad.append(eid)
            continue
        if replay_id is not None and cutoff_at:
            v = e["value"] if isinstance(e["value"], dict) else {}
            fact = {"fact_id": eid, "available_at": e["available_at"], "time_precision": e["time_precision"],
                    "publication_date": v.get("publication_date")}
            at, _ = pit.effective_available_at(fact)
            if at is None or clock.parse_utc(at) > clock.parse_utc(cutoff_at):
                future.append(eid)
    if future:
        raise ApiError(422, "SOURCE_TIME_CONFLICT", "引用的证据在训练截点之后才可知：服务端以来源记录的可知时刻为准，"
                       "不接受客户端声称的时间", [{"field": field, "reason": f"{e} available after cutoff"} for e in future[:10]])
    if bad:
        raise ApiError(422, "EVIDENCE_NOT_VISIBLE", "引用的证据不存在或不在当前上下文中",
                       [{"field": field, "reason": e} for e in bad[:10]])
