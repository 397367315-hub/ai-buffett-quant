"""契约枚举（与 contracts/models.schema.json 同名同值）。

界面中文不新增 API 枚举；这里的值就是线上 JSON 的值。
"""
from __future__ import annotations

from enum import Enum


class _S(str, Enum):
    def __str__(self) -> str:   # 便于直接拼进 JSON
        return self.value


class RunState(_S):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class Applicability(_S):
    APPLICABLE = "applicable"
    INAPPLICABLE = "inapplicable"
    UNKNOWN = "unknown"


class Verdict(_S):
    CONDITIONS_MET = "conditions_met"
    CONDITIONS_FAILED = "conditions_failed"
    WATCH = "watch"
    UNKNOWN = "unknown"


class JobState(_S):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CaseState(_S):
    QUEUED = "queued"
    PREPARING = "preparing"
    INITIAL_ROUND = "initial_round"
    CHALLENGE_ROUND = "challenge_round"
    SYNTHESIZING = "synthesizing"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


TERMINAL_JOB = {"succeeded", "partial", "failed", "cancelled"}
TERMINAL_CASE = {"succeeded", "partial", "failed"}
TERMINAL_RUN = {"succeeded", "partial", "failed", "skipped"}

FRESHNESS = ("fresh", "stale", "unknown")
QUALITY = ("complete", "partial", "unavailable")
SOURCE_MODE = ("provider_api", "file_import", "user_manual", "fixture", "mixed")
FEED_PRECISION = ("daily_bar", "date_snapshot", "intraday_snapshot", "tick", "mixed", "unknown")
BAR_STATUS = ("final", "partial", "unknown")
PRICE_BASIS = ("raw", "as_of_adjusted", "mixed", "unknown")
ROLES = ("primary", "validator", "discipline", "context")
HORIZONS = ("short", "swing", "long")
PLAN_PURPOSE = ("research", "simulation", "user_trade_record")
ELIGIBILITY = ("eligible", "ineligible", "unknown")
RULE_ORIGIN = ("author_quote", "editorial_summary", "engineering_policy", "user_rule")


def check_triplet(run_state: str, applicability: str, verdict: str, candidate_count) -> str | None:
    """SeatRun 三维正交约束（02 §5）。返回违规说明；合法返回 None。

    - queued/running/failed → verdict 必须 unknown，candidate_count=null
    - inapplicable → verdict unknown、候选为空、candidate_count=null
    - 只有 applicable 才能给 conditions_met / conditions_failed
    """
    rs, ap, vd = str(run_state), str(applicability), str(verdict)
    if rs in ("queued", "running", "failed"):
        if vd != "unknown":
            return f"{rs} 时 verdict 必须 unknown"
        if candidate_count is not None:
            return f"{rs} 时 candidate_count 必须为 null"
    if ap == "inapplicable":
        if vd != "unknown":
            return "inapplicable 时 verdict 必须 unknown"
        if candidate_count is not None:
            return "inapplicable 时 candidate_count 必须为 null"
    if vd in ("conditions_met", "conditions_failed") and ap != "applicable":
        return "只有 applicable 才能判定条件满足/不满足"
    return None
