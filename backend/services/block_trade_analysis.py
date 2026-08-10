"""Evidence-based analysis for the A-share block-trade monitor."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from database import async_session
from services.ai_service import ai_service
from services.data_collector import shanghai_now
from services.quote_cache import quote_snapshot_service


def _number(value: object) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique(values: list[object], limit: int = 4) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _seat_type(name: str) -> str:
    if "机构专用" in name:
        return "机构专用席位"
    if any(term in name for term in ("总部", "基金", "资管", "投资")):
        return "机构/资管席位"
    if "证券营业部" in name:
        return "券商营业部"
    return "席位类型未识别"


class BlockTradeAnalysisService:
    async def _quotes(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        if not codes:
            return {}
        try:
            payload = await asyncio.wait_for(
                quote_snapshot_service.fetch(codes, async_session),
                timeout=12,
            )
        except Exception:
            return {}
        return {str(item.get("code")): item for item in payload.get("stocks") or []}

    @staticmethod
    def _stock_view(code: str, name: str, rows: list[dict[str, Any]], quote: dict[str, Any] | None) -> dict[str, Any]:
        amounts = [_number(row.get("amount")) or 0 for row in rows]
        premiums = [_number(row.get("premium")) for row in rows]
        premiums = [item for item in premiums if item is not None]
        prices = [_number(row.get("price")) for row in rows]
        prices = [item for item in prices if item is not None and item > 0]
        latest_trade = max((str(row.get("date") or "") for row in rows), default=None)
        avg_premium = sum(premiums) / len(premiums) if premiums else None
        total_amount = sum(amounts)
        latest_price = _number((quote or {}).get("price"))
        latest_row = max(rows, key=lambda item: str(item.get("date") or ""), default={})
        trade_price = _number(latest_row.get("price"))
        relative_to_latest = ((trade_price / latest_price - 1) * 100) if trade_price and latest_price else None
        buyers = _unique([row.get("buyer") for row in rows], 5)
        sellers = _unique([row.get("seller") for row in rows], 5)
        buyer_types = _unique([_seat_type(item) for item in buyers], 3)
        seller_types = _unique([_seat_type(item) for item in sellers], 3)

        facts = [
            f"共{len(rows)}笔，合计成交额 {total_amount / 1e8:.2f}亿元",
            f"成交价区间 {min(prices):.2f}至{max(prices):.2f}元" if prices else "成交价缺失",
        ]
        if avg_premium is not None:
            facts.append(f"平均相对成交溢价率 {avg_premium:+.2f}%")
        if latest_price is not None:
            facts.append(f"最新可验证价 {latest_price:.2f}元（数据日 {(quote or {}).get('cache_trade_date') or '--'}）")
        if relative_to_latest is not None:
            facts.append(f"最近一笔成交价相对最新价 {relative_to_latest:+.2f}%")
        if buyers:
            facts.append(f"买方以{'、'.join(buyer_types)}为主")
        if sellers:
            facts.append(f"卖方以{'、'.join(seller_types)}为主")

        evidence: list[str] = []
        risks: list[str] = []
        if avg_premium is not None and avg_premium > 0.5:
            evidence.append(f"平均溢价 {avg_premium:+.2f}%，买方成交意愿相对积极")
        elif avg_premium is not None and avg_premium < -1:
            risks.append(f"平均折价 {avg_premium:.2f}%，存在卖方让价或退出压力")
        else:
            evidence.append("成交大致平价，单看价格折溢价信号偏中性")
        if len(rows) >= 2:
            evidence.append(f"同一交易日/窗口出现{len(rows)}笔记录，值得结合后续价格承接观察")
        if latest_price is None:
            risks.append("最新行情未返回，暂不能判断成交价之后是被承接还是回落")
        elif relative_to_latest is not None and relative_to_latest < -3:
            evidence.append(f"当前可验证价高于最近大宗成交价 {abs(relative_to_latest):.2f}%，说明交易后价格承接尚可；两者时点不同，不能直接当作收益")
        elif relative_to_latest is not None and relative_to_latest > 3:
            risks.append(f"当前可验证价低于最近大宗成交价 {relative_to_latest:.2f}%，交易后价格承接偏弱，需继续观察")
        if not buyers or not sellers:
            risks.append("买卖方席位信息不完整，无法判断机构类型和是否关联交易")

        if avg_premium is not None and avg_premium > 0.5 and not risks:
            conclusion = "偏积极但需验证"
        elif risks:
            conclusion = "中性偏谨慎"
        else:
            conclusion = "中性观察"
        return {
            "code": code,
            "name": name,
            "trade_count": len(rows),
            "latest_trade_date": latest_trade,
            "total_amount": round(total_amount, 2),
            "average_premium": round(avg_premium, 2) if avg_premium is not None else None,
            "latest_trade_price": trade_price,
            "latest_price": latest_price,
            "relative_to_latest_pct": round(relative_to_latest, 2) if relative_to_latest is not None else None,
            "buyers": buyers,
            "sellers": sellers,
            "buyer_types": buyer_types,
            "seller_types": seller_types,
            "facts": facts,
            "evidence": evidence,
            "risks": risks,
            "conclusion": conclusion,
            "quote_source": (quote or {}).get("quote_source") or ("cache" if quote else None),
            "quote_data_date": (quote or {}).get("cache_trade_date"),
        }

    async def analyze(
        self,
        trades: list[dict[str, Any]],
        *,
        selected_code: str | None = None,
        use_ai: bool = True,
    ) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        names: dict[str, str] = {}
        for row in trades:
            code = str(row.get("code") or "").strip()
            if not code:
                continue
            grouped[code].append(row)
            names[code] = str(row.get("name") or code)
        codes = list(grouped)
        quotes = await self._quotes(codes)
        stocks = [self._stock_view(code, names[code], grouped[code], quotes.get(code)) for code in codes]
        stocks.sort(key=lambda item: item["total_amount"], reverse=True)
        total_amount = sum(item["total_amount"] for item in stocks)
        premium_count = sum(((_number(row.get("premium")) or 0) > 0) for row in trades)
        discount_count = sum(((_number(row.get("premium")) or 0) < 0) for row in trades)
        selected = next((item for item in stocks if item["code"] == selected_code), None) if selected_code else None
        if selected is None and stocks:
            selected = stocks[0]
        if selected:
            headline = f"{selected['name']}有{selected['trade_count']}笔大宗交易，结论为“{selected['conclusion']}”：先核对价格承接和资金方向，再判断是否具有跟随价值。"
        else:
            headline = "当前没有可分析的大宗交易记录。"
        fallback = (
            f"本批共{len(trades)}笔、涉及{len(stocks)}只股票，成交额合计{total_amount / 1e8:.2f}亿元；"
            f"溢价{premium_count}笔、折价{discount_count}笔。大宗交易只说明协议成交，不能单独证明后续涨跌。"
        )
        payload = {
            "available": bool(stocks),
            "headline": headline,
            "summary": fallback,
            "stocks": stocks,
            "selected": selected,
            "data_date": max((str(row.get("date") or "") for row in trades), default=None),
            "quote_data_dates": sorted({item["quote_data_date"] for item in stocks if item.get("quote_data_date")}),
            "source": "eastmoney_block_trade+quote_cache",
            "updated_at": shanghai_now().isoformat(),
            "ai_narrative": None,
            "ai_generated": False,
        }
        if use_ai and stocks and ai_service.client:
            compact = {
                "headline": headline,
                "summary": fallback,
                "stocks": [{
                    "name": item["name"], "code": item["code"], "facts": item["facts"],
                    "evidence": item["evidence"], "risks": item["risks"], "conclusion": item["conclusion"],
                } for item in stocks[:10]],
            }
            prompt = "只依据下列JSON，用中文输出两段：逐笔事实和人工复核建议。不要预测必涨必跌，不要新增数据。每段不超过100字。\n" + json.dumps(compact, ensure_ascii=False)
            try:
                generated = await asyncio.wait_for(
                    ai_service.generate(prompt, "你是大宗交易审计分析师，必须区分事实、推断和风险。"),
                    timeout=15,
                )
                if generated and not generated.startswith("[AI服务"):
                    payload["ai_narrative"] = generated.strip()
                    payload["ai_generated"] = True
            except Exception:
                pass
        return payload


block_trade_analysis_service = BlockTradeAnalysisService()
