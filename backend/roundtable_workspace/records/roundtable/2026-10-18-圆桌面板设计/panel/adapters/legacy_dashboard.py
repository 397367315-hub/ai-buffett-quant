"""旧 dashboard.json → legacy QuantPageData 适配（DATA-001 / EVAL-001）。

- 保留原 camelCase 与 gate/runState 语义，但不把旧来源 source:"live" 当成新鲜度证明：
  另附 legacyNotice 与 dataStatus（报价日 / 生成时间分开）；
- 审查席未运行不冒充 SUCCESS（refresh.py 已改 SKIPPED）；未知/失败不伪装 CLOSED，缺失 count 不写 0；
- 新页面不使用本接口；训练上下文直接拒绝（由调用方检查）。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import LEGACY_DATA_DIR

DASHBOARD = LEGACY_DATA_DIR / "dashboard.json"


def annotate_quote_completion(data: dict) -> dict:
    """Keep incomplete daily snapshots distinct from confirmed closing bars."""
    status = data.get("dataStatus")
    if not isinstance(status, dict):
        return data
    quote = str(status.get("quote_date") or "").replace("-", "")
    completed = str(status.get("latest_completed_session_date") or "").replace("-", "")
    if len(quote) == 8 and len(completed) == 8 and quote > completed:
        status["quality"] = "partial"
        status["freshness_reason"] = (
            f"报价日 {status['quote_date']} 晚于已核验的完整交易日 "
            f"{status['latest_completed_session_date']}；当日数据为尚未确认收盘的日线快照。"
        )
        status["quality_reason"] = "当日数据尚未确认收盘；候选、形态和量价结论仅供观察，更新行情后需复核。"
    elif status.get("quality") == "partial":
        if status.get("freshness") == "fresh":
            status["freshness_reason"] = "截至生成时已覆盖最近完整交易日；日线级快照不是逐笔行情，逐只收盘完成状态尚未核验。"
        status["quality_reason"] = "数据源未提供逐只收盘完成标志；本页使用日线级快照，候选和形态需结合更新后的行情复核。"
    return data


def load_dashboard(path: Path | None = None) -> dict | None:
    f = path or DASHBOARD
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    src = d.get("source")
    d["legacy"] = True
    d["deprecated"] = True
    d["legacySource"] = src
    d["legacyNotice"] = ("legacy 视图：数据来自旧 refresh.py 缓存，source 字段只表示「来自旧后端」，"
                         "不是新鲜度证明；以 dataStatus 的报价日与生成时间为准，不用于执行。")
    for s in d.get("seats") or []:
        if s.get("runState") == "SUCCESS" and s.get("version") == "trading-tuixue-v1":
            s["runState"] = "SKIPPED"
    try:
        from ..services import legacy_evaluation
        d["legacyEvaluation"] = legacy_evaluation.material()
    except Exception as ex:      # 旧资料登记失败不影响旧视图，但要说出来
        d["legacyEvaluation"] = {"status": "unavailable", "error": type(ex).__name__}
    return annotate_quote_completion(d)
