"""Build an auditable sector-to-sector flow view from board net-flow data.

The upstream board endpoints expose aggregate net inflow/outflow for each
sector, but they do not expose the destination of individual orders.  This
module therefore creates a balanced *inference* for the visual network.  It
never labels the result as a traced order path: each link is proportional to
the two observed net-flow rankings and is explicitly marked as inferred.
"""

from __future__ import annotations

import math
from typing import Any


MIN_DISPLAY_AMOUNT = 1


def _signed_amount(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _amount(value: Any) -> float:
    return abs(_signed_amount(value))


def _node_payload(row: dict, node_type: str) -> dict:
    return {
        "type": node_type,
        "code": str(row.get("code") or ""),
        "name": str(row.get("name") or row.get("code") or "未命名板块"),
    }


def _transfer(source: dict, target: dict, amount: float) -> dict:
    return {
        "source": source,
        "target": target,
        "amount": int(round(amount)),
        "inferred": True,
        "basis": "展示范围内净流量平衡分配",
    }


def build_inferred_transfers(inflows: list[dict], outflows: list[dict]) -> dict:
    """Return balanced links and residual nodes for a single snapshot.

    The proportional matrix preserves the observed total on each side while
    keeping every visible source connected to every visible destination.  A
    residual is represented as ``new_money`` or ``market_exit`` when the two
    visible totals do not balance.
    """
    positive = [
        row for row in inflows
        if _signed_amount(row.get("main_net_inflow")) > 0
    ]
    negative = [
        row for row in outflows
        if _signed_amount(row.get("main_net_inflow")) < 0
    ]
    inflow_total = sum(_amount(row.get("main_net_inflow")) for row in positive)
    outflow_total = sum(_amount(row.get("main_net_inflow")) for row in negative)
    paired_total = min(inflow_total, outflow_total)
    transfers: list[dict] = []

    if paired_total and inflow_total and outflow_total:
        for source_row in negative:
            source_share = _amount(source_row.get("main_net_inflow")) / outflow_total
            source = _node_payload(source_row, "outflow")
            for target_row in positive:
                target_share = _amount(target_row.get("main_net_inflow")) / inflow_total
                amount = paired_total * source_share * target_share
                if amount >= MIN_DISPLAY_AMOUNT:
                    transfers.append(_transfer(source, _node_payload(target_row, "inflow"), amount))

    if inflow_total > paired_total:
        residual = inflow_total - paired_total
        source = {
            "type": "new_money",
            "code": "__NEW_MONEY__",
            "name": "新资金进场",
        }
        for target_row in positive:
            amount = residual * _amount(target_row.get("main_net_inflow")) / inflow_total
            if amount >= MIN_DISPLAY_AMOUNT:
                transfers.append(_transfer(source, _node_payload(target_row, "inflow"), amount))

    if outflow_total > paired_total:
        residual = outflow_total - paired_total
        target = {
            "type": "market_exit",
            "code": "__MARKET_EXIT__",
            "name": "市场离场",
        }
        for source_row in negative:
            amount = residual * _amount(source_row.get("main_net_inflow")) / outflow_total
            if amount >= MIN_DISPLAY_AMOUNT:
                transfers.append(_transfer(_node_payload(source_row, "outflow"), target, amount))

    return {
        "transfers": transfers,
        "inference": {
            "method": "net_flow_balance",
            "label": "板块迁移为净流量推断",
            "description": "行情源提供板块净流入/净流出，不提供逐笔资金目的地；连线按当前展示范围内的净流量比例平衡分配。",
            "confidence": "low",
            "paired_amount": int(round(paired_total)),
            "inflow_total": int(round(inflow_total)),
            "outflow_total": int(round(outflow_total)),
        },
    }
