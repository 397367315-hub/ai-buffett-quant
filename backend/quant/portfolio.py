"""Independent JSON-backed paper portfolio for strategy signals."""

from __future__ import annotations

from quant.schemas import PaperBuyRequest, PaperResetRequest, PaperSellRequest
from quant.storage import quant_store
from services.data_collector import normalize_stock_code, shanghai_now
from services.research_protocol import ResearchProtocol


COMMISSION_RATE = ResearchProtocol.COMMISSION_RATE
STAMP_TAX_RATE = ResearchProtocol.STAMP_TAX_RATE


def _fee(amount: float, *, sell: bool) -> tuple[float, float]:
    return amount * COMMISSION_RATE, amount * STAMP_TAX_RATE if sell else 0.0


class PaperPortfolio:
    @staticmethod
    def _revalue(document: dict) -> None:
        account = document["account"]
        market_value = sum(float(item.get("market_value") or 0) for item in document.get("holdings", []))
        account["total_value"] = round(float(account.get("available_cash") or 0) + market_value, 2)
        initial = float(account.get("initial_capital") or 0)
        account["total_return_pct"] = round((account["total_value"] / initial - 1) * 100, 2) if initial else 0.0
        document["updated_at"] = shanghai_now().isoformat()

    @staticmethod
    def get_portfolio() -> dict:
        document = quant_store.read("paper_portfolio")
        PaperPortfolio._revalue(document)
        return quant_store.write("paper_portfolio", document)

    @staticmethod
    def buy(payload: PaperBuyRequest | dict) -> dict:
        request = payload if isinstance(payload, PaperBuyRequest) else PaperBuyRequest.model_validate(payload)
        code = normalize_stock_code(request.stock_code)
        if request.shares % 100 != 0:
            raise ValueError("A股买入数量必须是100股的整数倍")
        amount = request.price * request.shares
        commission, tax = _fee(amount, sell=False)
        total_cost = amount + commission
        document = quant_store.read("paper_portfolio")
        account = document["account"]
        if total_cost > float(account.get("available_cash") or 0):
            raise ValueError("模拟盘可用资金不足")
        holding = next((item for item in document["holdings"] if item.get("stock_code") == code), None)
        if holding is None:
            holding = {
                "stock_code": code, "stock_name": request.stock_name.strip(), "shares": 0,
                "cost": 0.0, "cost_per_share": 0.0, "buy_date": shanghai_now().date().isoformat(),
                "current_price": request.price, "market_value": 0.0, "profit_pct": 0.0,
                "strategy_ids": [request.strategy_id] if request.strategy_id else [],
            }
            document["holdings"].append(holding)
        previous_cost = float(holding.get("cost") or 0)
        holding["shares"] = int(holding.get("shares") or 0) + request.shares
        holding["cost"] = round(previous_cost + total_cost, 2)
        holding["cost_per_share"] = round(holding["cost"] / holding["shares"], 6)
        if request.stock_name.strip():
            holding["stock_name"] = request.stock_name.strip()
        holding["current_price"] = request.price
        holding["market_value"] = round(holding["shares"] * request.price, 2)
        holding["profit_pct"] = round((holding["market_value"] / holding["cost"] - 1) * 100, 2) if holding["cost"] else 0.0
        if request.strategy_id and request.strategy_id not in holding["strategy_ids"]:
            holding["strategy_ids"].append(request.strategy_id)
        account["available_cash"] = round(float(account["available_cash"]) - total_cost, 2)
        document["history"].append({
            "id": f"paper_{shanghai_now().strftime('%Y%m%d%H%M%S%f')}", "date": shanghai_now().isoformat(),
            "action": "buy", "stock_code": code, "stock_name": holding["stock_name"],
            "price": request.price, "shares": request.shares, "amount": round(amount, 2),
            "commission": round(commission, 2), "tax": 0.0, "strategy_id": request.strategy_id,
            "signal_id": request.signal_id, "reason": "用户确认模拟买入",
        })
        PaperPortfolio._revalue(document)
        return quant_store.write("paper_portfolio", document)

    @staticmethod
    def sell(payload: PaperSellRequest | dict) -> dict:
        request = payload if isinstance(payload, PaperSellRequest) else PaperSellRequest.model_validate(payload)
        code = normalize_stock_code(request.stock_code)
        if request.shares % 100 != 0:
            raise ValueError("A股卖出数量必须是100股的整数倍")
        document = quant_store.read("paper_portfolio")
        holding = next((item for item in document["holdings"] if item.get("stock_code") == code), None)
        if holding is None or int(holding.get("shares") or 0) < request.shares:
            raise ValueError("模拟盘持仓不足")
        amount = request.price * request.shares
        commission, tax = _fee(amount, sell=True)
        net = amount - commission - tax
        cost_per_share = float(holding.get("cost_per_share") or 0)
        realized = net - cost_per_share * request.shares
        holding["shares"] = int(holding["shares"]) - request.shares
        holding["cost"] = round(cost_per_share * holding["shares"], 2)
        holding["cost_per_share"] = cost_per_share
        holding["current_price"] = request.price
        holding["market_value"] = round(holding["shares"] * request.price, 2)
        holding["profit_pct"] = round((holding["market_value"] / holding["cost"] - 1) * 100, 2) if holding["cost"] else 0.0
        if holding["shares"] == 0:
            document["holdings"].remove(holding)
        document["account"]["available_cash"] = round(float(document["account"]["available_cash"]) + net, 2)
        document["history"].append({
            "id": f"paper_{shanghai_now().strftime('%Y%m%d%H%M%S%f')}", "date": shanghai_now().isoformat(),
            "action": "sell", "stock_code": code, "stock_name": holding.get("stock_name", ""),
            "price": request.price, "shares": request.shares, "amount": round(amount, 2),
            "commission": round(commission, 2), "tax": round(tax, 2), "realized_pnl": round(realized, 2),
            "reason": request.reason,
        })
        PaperPortfolio._revalue(document)
        return quant_store.write("paper_portfolio", document)

    @staticmethod
    def reset(payload: PaperResetRequest | dict | None = None) -> dict:
        request = payload if isinstance(payload, PaperResetRequest) else PaperResetRequest.model_validate(payload or {})
        document = {
            "version": 1,
            "account": {
                "initial_capital": request.initial_capital,
                "available_cash": request.initial_capital,
                "total_value": request.initial_capital,
                "total_return_pct": 0.0,
            },
            "holdings": [], "history": [], "updated_at": shanghai_now().isoformat(),
            "price_source": "none", "price_updated_at": None,
            "price_is_realtime": False, "price_warning": None,
        }
        return quant_store.write("paper_portfolio", document)

    @staticmethod
    def refresh_prices(snapshot: dict | None, *, warning: str | None = None) -> dict:
        snapshot = snapshot or {}
        by_code = {str(item.get("code")): item for item in snapshot.get("stocks") or []}
        document = quant_store.read("paper_portfolio")
        for holding in document.get("holdings", []):
            quote = by_code.get(holding.get("stock_code"))
            if not quote or quote.get("price") in (None, ""):
                continue
            holding["current_price"] = float(quote["price"])
            holding["market_value"] = round(holding["shares"] * holding["current_price"], 2)
            cost = float(holding.get("cost") or 0)
            holding["profit_pct"] = round((holding["market_value"] / cost - 1) * 100, 2) if cost else 0.0
        document["price_source"] = snapshot.get("source", "unavailable")
        document["price_updated_at"] = snapshot.get("source_updated_at") or snapshot.get("fetched_at")
        document["price_is_realtime"] = bool(snapshot.get("is_realtime"))
        document["price_warning"] = warning
        PaperPortfolio._revalue(document)
        return quant_store.write("paper_portfolio", document)


paper_portfolio = PaperPortfolio()
