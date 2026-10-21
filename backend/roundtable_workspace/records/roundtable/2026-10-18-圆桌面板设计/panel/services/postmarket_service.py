"""盘后复盘（确定性规则版，不调用模型）：把一份冻结快照翻译成一份可复核的盘后报告。

设计约束：
- 每一个数字都来自快照本身（metric_id / daily_bars / instruments），报告里带 metric_id，点得回来源；
- 没有的数据写「未接入 / 未提供」，不用别的数据凑（两融、北向、ETF 份额、外围市场目前不在数据源里）；
- 只做观察与条件陈述，不做预测、不给买卖建议；风险与观察点都由规则触发，触发条件写在条目里；
- 输出同时给结构化 JSON 与 Markdown，后续接模型时模型只读这份 JSON，不直接读原始数据。

规则标签（summary.tone）：
  broad_strong / broad_weak   宽度（上涨占比 ≥0.55 / ≤0.45；中间算中性）
  sentiment_hot / cold        涨停家数 ≥60 且炸板率 ≤0.25 / 涨停 ≤30 或炸板率 ≥0.35
  volume_up / volume_down     成交额 / 20 日均值 ≥1.2 / ≤0.8
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..domain import clock

SCHEMA = "postmarket_report_v1"
MISSING = "未提供"


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _pct(v: Decimal | None, places: int = 2) -> str | None:
    if v is None:
        return None
    q = (v * 100).quantize(Decimal(1).scaleb(-places))
    return f"{q}%"


def _yi(minor: str | None) -> str | None:
    """分 → 亿（两位小数，四舍五入）。"""
    if minor is None:
        return None
    try:
        b = int(minor)
    except (TypeError, ValueError):
        return None
    base = 10 ** 10
    hundredths = (abs(b) * 100 + base // 2) // base
    whole, dec = divmod(hundredths, 100)
    return f"{'-' if b < 0 else ''}{whole:,}.{dec:02d} 亿"


def _metric(snap: dict, metric_id: str, symbol: str | None = None) -> dict | None:
    for m in snap.get("metrics") or []:
        if m.get("metric_id") == metric_id and (symbol is None or m.get("symbol") == symbol):
            return m
    return None


def _val(snap: dict, metric_id: str, symbol: str | None = None) -> Decimal | None:
    m = _metric(snap, metric_id, symbol)
    if m is None:
        return None
    return _dec(m.get("value"))


def _item(label: str, value: str | None, metric_id: str | None = None, tone: str = "", note: str = "") -> dict:
    return {"label": label, "value": value if value is not None else MISSING,
            "metric_id": metric_id, "tone": tone, "note": note, "available": value is not None}


def _tone_for_pct(v: Decimal | None) -> str:
    if v is None:
        return ""
    return "up" if v > 0 else "down" if v < 0 else "flat"


def _sections(snap: dict) -> list[dict]:
    md = snap.get("metadata") or {}
    up = _val(snap, "breadth_up_count")
    down = _val(snap, "breadth_down_count")
    quoted = _val(snap, "breadth_quoted_count")
    flat = (quoted - up - down) if (quoted is not None and up is not None and down is not None) else None
    up_ratio = _val(snap, "breadth_up_ratio")
    median = _val(snap, "breadth_median_pct_chg")

    limit_up = _val(snap, "limit_up_count")
    limit_down = _val(snap, "limit_down_count")
    broken = _val(snap, "broken_board_count")
    broken_ratio = _val(snap, "broken_board_ratio")
    max_boards = _val(snap, "max_consecutive_boards")
    consec = _val(snap, "consecutive_board_count")
    st_limit = _val(snap, "st_limit_up_count")

    amount = _val(snap, "total_amount_minor")
    amount_vs = _val(snap, "amount_vs_20d_avg")
    active = _val(snap, "active_amount_count")
    high_turn = _val(snap, "high_turnover_share")

    abs_med = _val(snap, "abs_pct_chg_median")
    big_move = _val(snap, "big_move_share")

    prev_members = _val(snap, "prev_limit_up_member_count")
    prev_quoted = _val(snap, "prev_limit_up_quoted_count")
    prev_follow = _val(snap, "prev_limit_up_followthrough")

    sections = [
        {"id": "breadth", "title": "市场宽度",
         "note": f"分母：{md.get('universe_policy_id') or '环境口径'}；报价覆盖 {int(quoted) if quoted is not None else '—'} 只",
         "items": [
             _item("上涨家数", str(int(up)) if up is not None else None, "breadth_up_count", "up"),
             _item("下跌家数", str(int(down)) if down is not None else None, "breadth_down_count", "down"),
             _item("平盘家数", str(int(flat)) if flat is not None else None, None, "flat", "= 有报价 − 上涨 − 下跌"),
             _item("上涨占比", _pct(up_ratio), "breadth_up_ratio", "up" if up_ratio is not None and up_ratio > Decimal("0.5") else "down"),
             _item("涨跌幅中位数", _pct(median, 3), "breadth_median_pct_chg", _tone_for_pct(median)),
         ]},
        {"id": "sentiment", "title": "情绪与涨停生态",
         "note": "涨停价按规则由昨收推算；封板时间、开板次数等只有事件流才有，归档快照里是未知",
         "items": [
             _item("涨停家数（非 ST）", str(int(limit_up)) if limit_up is not None else None, "limit_up_count", "up"),
             _item("跌停家数（非 ST）", str(int(limit_down)) if limit_down is not None else None, "limit_down_count", "down"),
             _item("炸板家数", str(int(broken)) if broken is not None else None, "broken_board_count"),
             _item("炸板率", _pct(broken_ratio), "broken_board_ratio",
                   "down" if broken_ratio is not None and broken_ratio >= Decimal("0.3") else ""),
             _item("最高连板", str(int(max_boards)) if max_boards is not None else None, "max_consecutive_boards"),
             _item("连板家数（≥2 板）", str(int(consec)) if consec is not None else None, "consecutive_board_count"),
             _item("ST 涨停家数", str(int(st_limit)) if st_limit is not None else None, "st_limit_up_count", "", "单列，不进上面的非 ST 口径"),
         ]},
        {"id": "followthrough", "title": "昨日涨停组今日表现",
         "note": "成员在上一交易日固定，不因今天停牌或表现差而事后重选",
         "items": [
             _item("昨日涨停组成员", str(int(prev_members)) if prev_members is not None else None, "prev_limit_up_member_count"),
             _item("其中有报价", str(int(prev_quoted)) if prev_quoted is not None else None, "prev_limit_up_quoted_count"),
             _item("平均涨跌", _pct(prev_follow, 3), "prev_limit_up_followthrough", _tone_for_pct(prev_follow),
                   "无报价成员不补 0"),
         ]},
        {"id": "liquidity", "title": "成交与流动性",
         "items": [
             _item("环境样本成交额", _yi(str(int(amount)) if amount is not None else None), "total_amount_minor"),
             _item("成交额 / 20 日均值", (f"{amount_vs.quantize(Decimal('0.001'))} 倍" if amount_vs is not None else None),
                   "amount_vs_20d_avg", "up" if amount_vs is not None and amount_vs >= Decimal("1.2")
                   else "down" if amount_vs is not None and amount_vs <= Decimal("0.8") else ""),
             _item("成交额 ≥ 1 亿元家数", str(int(active)) if active is not None else None, "active_amount_count"),
             _item("高换手占比（≥10%）", _pct(high_turn), "high_turnover_share"),
         ]},
        {"id": "volatility", "title": "波动",
         "items": [
             _item("绝对涨跌幅中位数", _pct(abs_med, 3), "abs_pct_chg_median"),
             _item("大幅波动占比（|涨跌| ≥5%）", _pct(big_move), "big_move_share"),
         ]},
    ]

    # 指数（provider 快照才有；归档快照是未提供）
    idx_ids = [("000001.SH", "上证指数"), ("399001.SZ", "深证成指"), ("399006.SZ", "创业板指"),
               ("000300.SH", "沪深300"), ("000852.SH", "中证1000")]
    idx_items = []
    for sym, name in idx_ids:
        pct = _val(snap, "index_pct_chg", sym)
        ret20 = _val(snap, "index_ret_20", sym)
        dev = _val(snap, "index_close_to_ma20", sym)
        idx_items.append({"label": name, "value": _pct(pct) if pct is not None else MISSING,
                          "metric_id": "index_pct_chg" if pct is not None else None,
                          "tone": _tone_for_pct(pct),
                          "note": (f"20 日 {_pct(ret20)}；距 MA20 {dev.quantize(Decimal('0.0001'))} 倍"
                                   if ret20 is not None and dev is not None else ""),
                          "available": pct is not None})
    sections.insert(0, {"id": "index", "title": "指数",
                        "note": "" if any(i["available"] for i in idx_items) else "本快照没有指数日线（归档只含个股日线）",
                        "items": idx_items})

    # 主线（行业）：需要行业映射；归档快照没有
    sections.append({"id": "leaders", "title": "主线（行业代理）",
                     "note": "需要证券基础信息里的行业映射；归档快照不含，provider 快照才有" if _val(snap, "limit_up_industry_count") is None else
                             "按行业分组的涨停家数，行业代理不是概念成员",
                     "items": [
                         _item("有涨停的行业数", str(int(_val(snap, "limit_up_industry_count"))) if _val(snap, "limit_up_industry_count") is not None else None, "limit_up_industry_count"),
                         _item("涨停最多行业占比", _pct(_val(snap, "top_industry_limit_up_share")), "top_industry_limit_up_share"),
                         _item("该行业成员上涨占比", _pct(_val(snap, "top_industry_member_up_ratio")), "top_industry_member_up_ratio"),
                     ]})
    # 板块与资金（M6）：把「板块口径」和「资金口径」摊开写：有什么、缺什么、缺的字段叫什么
    ind_ok = _val(snap, "limit_up_industry_count") is not None
    sections.append({"id": "sector_funds", "title": "板块与资金",
                     "note": "板块强度用行业代理（涨停家数）计算；资金面需要的字段在本快照里没有的，一律标「未接入」，不用别的数凑",
                     "items": [
                         _item("行业代理（涨停家数）",
                               str(int(_val(snap, "limit_up_industry_count"))) if ind_ok else None,
                               "limit_up_industry_count",
                               note="" if ind_ok else "需要的字段：limit_up_industry_count / top_industry_limit_up_share（证券基础信息的行业映射，provider 快照才有）"),
                         _item("板块资金净流入", None, None,
                               note="需要的字段：industry_net_inflow_minor（分行业、单位分）；当前没有数据源，未接入"),
                         _item("两融余额与变动", None, None,
                               note="需要的字段：margin_balance_minor / margin_balance_chg_minor；未接入"),
                         _item("北向资金", None, None,
                               note="需要的字段：northbound_net_minor；未接入（且口径几经调整，接入前要先定口径）"),
                         _item("ETF 份额 / 申赎", None, None,
                               note="需要的字段：etf_net_subscribe_minor；未接入"),
                         _item("外围市场（美/港/汇率）", None, None,
                               note="需要的字段：overseas_index_pct / fx_pct；未接入，可用官方搜索临时补，但结果要标未核验"),
                     ]})
    return sections


def _observations(snap: dict) -> tuple[list[str], list[str], str]:
    """规则触发的观察点与风险（不做预测）。返回 (观察点, 风险, tone)。"""
    up_ratio = _val(snap, "breadth_up_ratio")
    limit_up = _val(snap, "limit_up_count")
    broken_ratio = _val(snap, "broken_board_ratio")
    max_boards = _val(snap, "max_consecutive_boards")
    amount_vs = _val(snap, "amount_vs_20d_avg")
    prev_follow = _val(snap, "prev_limit_up_followthrough")
    median = _val(snap, "breadth_median_pct_chg")

    watch, risk, tone = [], [], "neutral"
    if up_ratio is not None:
        if up_ratio >= Decimal("0.55"):
            tone = "broad_strong"
            watch.append(f"上涨占比 {_pct(up_ratio)}：宽度偏强，看能否连续两天站住 55% 以上")
        elif up_ratio <= Decimal("0.45"):
            tone = "broad_weak"
            watch.append(f"上涨占比 {_pct(up_ratio)}：宽度偏弱，先看跌停家数有没有继续增加")
    if limit_up is not None:
        hot = limit_up >= 60 and (broken_ratio is None or broken_ratio <= Decimal("0.25"))
        cold = limit_up <= 30 or (broken_ratio is not None and broken_ratio >= Decimal("0.35"))
        if hot:
            watch.append(f"涨停 {int(limit_up)} 家、炸板率 {_pct(broken_ratio) or '未提供'}：情绪偏热，留意高位股分歧")
        elif cold:
            risk.append(f"涨停只有 {int(limit_up)} 家、炸板率 {_pct(broken_ratio) or '未提供'}：赚钱效应偏弱，仓位应保守")
    if max_boards is not None and max_boards <= 3:
        risk.append(f"最高连板只有 {int(max_boards)} 板：高度被压制，接力风险大")
    if broken_ratio is not None and broken_ratio >= Decimal("0.35"):
        risk.append(f"炸板率 {_pct(broken_ratio)}：封板质量差，盘中冲高回落概率高")
    if amount_vs is not None and amount_vs <= Decimal("0.8"):
        risk.append(f"成交额只有 20 日均值的 {amount_vs.quantize(Decimal('0.01'))} 倍：缩量，反弹持续性存疑")
    if prev_follow is not None and prev_follow < 0:
        risk.append(f"昨日涨停组今日平均 {_pct(prev_follow, 3)}：打板/接力是负收益，昨天买的人今天在亏")
    if median is not None and median < 0 and up_ratio is not None and up_ratio > Decimal("0.5"):
        watch.append(f"涨跌幅中位数 {_pct(median, 3)} 为负、上涨家数却过半：小票在涨、权重在拖")
    if not watch:
        watch.append("今天没有触发规则级的观察点；按计划执行，不因为「没什么可说」而加仓")
    return watch, risk, tone


def build(snap: dict, *, generated_at: str | None = None) -> dict:
    md = snap.get("metadata") or {}
    sections = _sections(snap)
    watch, risk, tone = _observations(snap)
    up_ratio = _val(snap, "breadth_up_ratio")
    median = _val(snap, "breadth_median_pct_chg")
    limit_up = _val(snap, "limit_up_count")
    headline_parts = []
    if _val(snap, "index_pct_chg", "000001.SH") is not None:
        headline_parts.append(f"上证 {_pct(_val(snap, 'index_pct_chg', '000001.SH'))}")
    if up_ratio is not None:
        headline_parts.append(f"上涨 {int(_val(snap, 'breadth_up_count') or 0)} / 下跌 {int(_val(snap, 'breadth_down_count') or 0)}（{_pct(up_ratio)}）")
    if median is not None:
        headline_parts.append(f"涨跌幅中位数 {_pct(median, 3)}")
    if limit_up is not None:
        headline_parts.append(f"涨停 {int(limit_up)} 家")
    headline = "；".join(headline_parts) if headline_parts else "本快照没有可汇总的行情数据"

    gaps = [
        {"item": "两融余额与变动", "reason": "未接入数据源", "impact": "「谁的钱在动」缺一块"},
        {"item": "北向资金", "reason": "未接入数据源", "impact": "同上"},
        {"item": "ETF 份额 / 申赎", "reason": "未接入数据源", "impact": "看不出被动资金流向"},
        {"item": "外围市场（美/港/汇率）", "reason": "未接入数据源", "impact": "外部环境判断缺失"},
        {"item": "板块资金净流入", "reason": "未接入数据源（无分行业资金流）", "impact": "只知道谁涨停，不知道钱往哪个板块去"},
    ]
    if _val(snap, "limit_up_industry_count") is None:
        gaps.append({"item": "行业 / 概念分组", "reason": "该快照没有证券基础信息里的行业映射",
                     "impact": "主线板块只能等 provider 快照"})
    if _val(snap, "st_limit_up_count") is None:
        gaps.append({"item": "ST 名单", "reason": "该快照没有当日 ST 名单", "impact": "ST 涨停家数单列缺失"})

    report = {
        "schema_version": SCHEMA,
        "snapshot_id": snap.get("snapshot_id"),
        "session_date": md.get("session_date"),
        "quote_date": md.get("quote_date"),
        "generated_at": generated_at or clock.utc_iso(),
        "generator": {"kind": "rules", "model_called": False,
                      "note": "确定性规则生成；接模型后模型只读这份结构，不改数字"},
        "data_quality": {"quality": md.get("quality"), "freshness": md.get("freshness"),
                         "freshness_reason": md.get("freshness_reason"),
                         "source_mode": md.get("source_mode"), "is_synthetic": md.get("is_synthetic"),
                         "missing_fields": md.get("missing_fields") or []},
        "summary": {"headline": headline, "tone": tone},
        "sections": sections,
        "watchpoints": watch,
        "risks": risk,
        "gaps": gaps,
    }
    report["markdown"] = to_markdown(report)
    return report


def to_markdown(r: dict) -> str:
    q = r.get("data_quality") or {}
    lines = [f"# A 股盘后复盘 · {r.get('session_date') or '—'}", "",
             f"数据：快照 `{r.get('snapshot_id')}`（{q.get('source_mode') or '—'}，质量 {q.get('quality') or '—'}，"
             f"新鲜度 {q.get('freshness') or '—'}）；生成于 {r.get('generated_at')}", "",
             f"**{r['summary']['headline']}**", ""]
    for sec in r.get("sections") or []:
        lines.append(f"## {sec['title']}")
        if sec.get("note"):
            lines.append(f"_{sec['note']}_")
        lines.append("")
        lines.append("| 指标 | 值 | 来源 |")
        lines.append("|---|---|---|")
        for it in sec["items"]:
            src = f"`{it['metric_id']}`" if it.get("metric_id") else "—"
            note = f"（{it['note']}）" if it.get("note") else ""
            lines.append(f"| {it['label']} | {it['value']}{note} | {src} |")
        lines.append("")
    lines.append("## 明日观察点")
    lines += [f"- {x}" for x in r.get("watchpoints") or []] or ["- 无"]
    lines += ["", "## 主要风险"]
    lines += [f"- {x}" for x in r.get("risks") or []] or ["- 无"]
    lines += ["", "## 未接入的数据"]
    for g in r.get("gaps") or []:
        lines.append(f"- **{g['item']}**：{g['reason']}（{g['impact']}）")
    lines += ["", "---", "口径：数字均来自该快照的 metric / 日线；缺的写「未提供」。"
                        "本报告只做观察与条件陈述，不构成买卖建议，也不承诺收益。"]
    return "\n".join(lines) + "\n"
