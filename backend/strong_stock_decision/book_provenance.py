"""Auditable page provenance for the three-book research signals.

Page numbers are deliberately represented as strings.  Some sources use a
printed book page while the scanned 《暴涨之星》 source is only verified by
physical PDF page, and neither should be silently converted into the other.
This module contains no detection logic and returns ``None`` for an unmapped
skill rather than inventing a citation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _source(book: str, chapter: str, page_basis: str, page_range: str) -> dict[str, str]:
    return {
        "book": book,
        "chapter": chapter,
        "page_basis": page_basis,
        "page_range": page_range,
    }


_EXACT_SOURCES: dict[str, dict[str, str]] = {
    "HQS_RISK_001": _source("猎取强势股", "量时空大压", "book_printed_page", "022-024"),
    "HQS_RISK_002": _source("猎取强势股", "量时空大压", "book_printed_page", "022-024"),
    "HQS_RISK_003": _source("猎取强势股", "量时空大压", "book_printed_page", "022-024"),
    "HQS_RISK_004": _source("猎取强势股", "量时空大压", "book_printed_page", "022-024"),
    "HQS_008": _source("猎取强势股", "最佳交易区·强势A区", "book_printed_page", "067"),
    "HQS_009": _source("猎取强势股", "最佳交易区·强势B区", "book_printed_page", "076-077"),
    "HQS_010": _source("猎取强势股", "最佳交易区·风险C区", "book_printed_page", "083-084"),
    "BXZX_001": _source("暴涨之星", "蓄势之星·诱空/逼空蓄势", "pdf_physical_page", "018,021-025"),
    "BXZX_002": _source("暴涨之星", "蓄势之星·诱空/逼空蓄势", "pdf_physical_page", "018,021-025"),
    "BXZX_003": _source("暴涨之星", "调整之星·缓冲/震荡调整", "pdf_physical_page", "032-033"),
    "BXZX_004": _source("暴涨之星", "调整之星·缓冲/震荡调整", "pdf_physical_page", "032-033"),
    "BXZX_005": _source("暴涨之星", "止跌之星·同步/背离止跌", "pdf_physical_page", "053-055"),
    "BXZX_006": _source("暴涨之星", "止跌之星·同步/背离止跌", "pdf_physical_page", "053-055"),
    # The older V1 names remain auditable where the chapter identity is exact.
    "BXZX_012": _source("暴涨之星", "经典之星·现顶经典星线", "pdf_physical_page", "121-133"),
}


_PREFIX_SOURCES: tuple[tuple[str, dict[str, str]], ...] = (
    ("BXDT_VOL_", _source("暴涨大形态", "第一章·成交量形态", "book_printed_page", "004-014")),
    ("BXDT_MA_", _source("暴涨大形态", "第二章·均线形态", "book_printed_page", "016-029")),
    ("BXDT_TRI_", _source("暴涨大形态", "第三章·三角形形态", "book_printed_page", "030-047")),
    ("BXDT_BOX_", _source("暴涨大形态", "第四章·箱体形态", "book_printed_page", "048-061")),
    ("BXDT_NECK_", _source("暴涨大形态", "第五章·颈位形态", "book_printed_page", "062-083")),
    ("BXDT_UP_", _source("暴涨大形态", "第六章·顺上形态", "book_printed_page", "084-096")),
    ("BXDT_BOTTOM_", _source("暴涨大形态", "第七章·趋势底部", "book_printed_page", "097-123")),
    ("BXDT_CAPITAL_", _source("暴涨大形态", "第七章·资金底部", "book_printed_page", "124-136")),
    ("BXDT_PEAK_", _source("暴涨大形态", "第八章·巅峰超越", "book_printed_page", "169-205")),
    ("BXZX_CLASSIC_TOP_", _source("暴涨之星", "经典之星·现顶经典星线", "pdf_physical_page", "121-133")),
)


def book_source_for(skill_id: str) -> dict[str, str] | None:
    """Return a verified source record, or ``None`` when not mapped."""

    exact = _EXACT_SOURCES.get(skill_id)
    if exact is not None:
        return deepcopy(exact)
    for prefix, source in _PREFIX_SOURCES:
        if skill_id.startswith(prefix):
            return deepcopy(source)
    return None


__all__ = ["book_source_for"]
