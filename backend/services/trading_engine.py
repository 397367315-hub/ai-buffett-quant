import json
import random
import asyncio
from datetime import date, datetime, timedelta
from typing import Optional
from sqlalchemy import select, func, and_, desc
from database import async_session
from sim_models import SimAccount, SimPosition, SimTradeRecord, SimDailySummary
from services.data_collector import EASTMONEY_UT, as_float, collector, normalize_stock_code, stock_secid
from services.ai_service import ai_service
from services.quant_scorer import enhanced_scorer, DynamicWeights
from services.risk_analysis import StrategyProfile


TRADING_SYSTEM_PROMPT = """你是一位A股量化交易AI。你的任务是从市场数据中选出最有潜力的5只股票进行交易。

## 你的交易风格
- 中短线波段操作，持股周期3-10个交易日
- 结合主力资金流向+技术面+市场情绪选股
- 严格风控：单只股票仓位不超过总资金的25%
- 止损线：-8%，止盈线：+15%

## 选股标准（按重要性排序）
1. 当日主力资金大幅净流入（权重35%）
2. 量比>1.5且换手率适中3%-15%（权重25%）
3. PE合理或行业改善中（权重20%）
4. 当日涨幅2%-7%，不追涨停板（权重15%）
5. 属于当日热门板块（权重5%）

## 输出格式（严格遵守JSON）
{
  "market_analysis": "一句话概括今日市场",
  "trades": [
    {
      "stock_code": "000858",
      "stock_name": "五粮液",
      "action": "buy",
      "shares": 1000,
      "max_price": 150.0,
      "reason": "主力连续3日净流入，白酒板块回暖，PE处于历史低位",
      "score": 8.5,
      "stop_loss_pct": -8,
      "take_profit_pct": 15
    }
  ],
  "risk_warning": "一句风险提示"
}

只输出JSON，不要其他文字。最多推荐5笔交易。"""


class AITradingEngine:
    def __init__(self, account_id: int = 1, initial_capital: float = 1_000_000.0):
        self.account_id = account_id
        self.initial_capital = initial_capital

    async def _get_or_create_account(self) -> SimAccount:
        async with async_session() as session:
            stmt = select(SimAccount).where(SimAccount.id == self.account_id)
            result = await session.execute(stmt)
            account = result.scalar_one_or_none()

            if not account:
                account = SimAccount(
                    id=self.account_id,
                    initial_capital=self.initial_capital,
                    cash=self.initial_capital,
                    total_value=self.initial_capital,
                )
                session.add(account)
                await session.commit()

            return account

    async def _update_positions_prices(self):
        """更新持仓股票的当前价格和市值"""
        async with async_session() as session:
            stmt = select(SimPosition).where(
                SimPosition.account_id == self.account_id,
                SimPosition.shares > 0,
            )
            result = await session.execute(stmt)
            positions = result.scalars().all()

            for pos in positions:
                price_data = await self._fetch_stock_price(pos.stock_code)
                if price_data:
                    pos.current_price = price_data["price"]
                    pos.market_value = pos.current_price * pos.shares
                    pos.pnl = (pos.current_price - pos.avg_cost) * pos.shares
                    pos.pnl_pct = ((pos.current_price - pos.avg_cost) / pos.avg_cost * 100) if pos.avg_cost > 0 else 0
                    pos.hold_days = (date.today() - pos.buy_date).days if pos.buy_date else 0

            await session.commit()

    async def _fetch_stock_price(self, stock_code: str) -> Optional[dict]:
        """Fetch a validated stock quote from EastMoney's stock endpoint."""
        try:
            code = normalize_stock_code(stock_code)
            url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "secid": stock_secid(code),
                "fields": "f43,f44,f45,f47,f48,f57,f58,f169,f170",
                "ut": EASTMONEY_UT,
            }
            data = await collector.fetch_json(url, params)
            row = data.get("data") or {}
            price = as_float(row.get("f43")) / 100
            if price <= 0:
                return None
            return {
                "price": price,
                "change_pct": as_float(row.get("f170")) / 100,
                "high": as_float(row.get("f44")) / 100,
                "low": as_float(row.get("f45")) / 100,
                "volume": int(as_float(row.get("f47"))),
                "amount": int(as_float(row.get("f48"))),
            }
        except Exception as e:
            print(f"Error fetching price for {stock_code}: {e}")
        return None

    async def get_market_data_for_ai(self) -> dict:
        """Collect only source-backed market data for AI decisions."""
        concepts, technical, limit_ups, market_info = await asyncio.gather(
            collector.fetch_concept_flow(page_size=30),
            collector.fetch_technical_screener({"min_change": 2, "max_pe": 100, "min_turnover": 3}),
            collector.fetch_limit_up_stocks(),
            collector.fetch_market_turnover(),
        )

        # 构建简洁的市场数据摘要
        top_concepts = []
        for c in concepts[:10]:
            top_concepts.append({
                "name": c.get("name", ""),
                "change_pct": c.get("change_pct", 0),
                "main_inflow_yi": int(float(c.get("main_net_inflow", 0) or 0) / 1e8),
                "leading_stock": c.get("leading_stock", ""),
            })

        candidate_stocks = []
        for s in (technical.get("stocks", []) or [])[:30]:
            candidate_stocks.append({
                "code": s.get("code", ""),
                "name": s.get("name", ""),
                "price": s.get("price", ""),
                "change_pct": s.get("change_pct", ""),
                "turnover": s.get("turnover", ""),
                "pe": s.get("pe", ""),
                "volume_ratio": s.get("volume_ratio", ""),
                "main_inflow_yi": int(float(s.get("main_net_inflow", "0") or 0) / 1e8),
                "roe": s.get("roe", ""),
            })

        current_positions = []
        async with async_session() as session:
            stmt = select(SimPosition).where(
                SimPosition.account_id == self.account_id,
                SimPosition.shares > 0,
            )
            result = await session.execute(stmt)
            for pos in result.scalars().all():
                current_positions.append({
                    "code": pos.stock_code,
                    "name": pos.stock_name,
                    "shares": pos.shares,
                    "cost": pos.avg_cost,
                    "current_price": pos.current_price,
                    "pnl_pct": pos.pnl_pct,
                    "hold_days": pos.hold_days,
                })

        return {
            "available": bool(concepts and technical.get("stocks") and market_info),
            "market_index": market_info,
            "hot_concepts": top_concepts,
            "candidate_stocks": candidate_stocks,
            "current_positions": current_positions,
            "limit_up_count": len(limit_ups),
        }

    async def get_ai_trading_decision(self) -> dict:
        """AI分析市场数据，输出交易决策"""
        market_data = await self.get_market_data_for_ai()
        if not market_data.get("available"):
            return {
                "available": False,
                "market_analysis": "实时市场数据暂不可用，本次不生成交易建议。",
                "trades": [],
                "risk_warning": "行情源未确认可用，系统不会以模拟价格执行交易。",
            }

        prompt = f"""以下是今日A股市场数据，请根据交易策略选出5只最适合交易的股票。

## 当前市场
- 上证指数: {market_data.get('market_index', {}).get('sh_index', 'N/A')}
- 涨幅: {market_data.get('market_index', {}).get('sh_change_pct', 'N/A')}%
- 涨停家数: {market_data.get('limit_up_count', 0)}

## 热门概念板块 TOP5
{json.dumps(market_data.get('hot_concepts', [])[:5], ensure_ascii=False, indent=2)}

## 候选股票（放量突破筛选）
{json.dumps(market_data.get('candidate_stocks', [])[:20], ensure_ascii=False, indent=2)}

## 当前持仓
{json.dumps(market_data.get('current_positions', []), ensure_ascii=False, indent=2)}

请输出JSON格式的交易决策。如果当前持仓有需要止损(-8%)或止盈(+15%)的，优先卖出。然后从候选股中选最多5只买入。"""

        try:
            response_text = await ai_service.generate(prompt, TRADING_SYSTEM_PROMPT)
            # 提取JSON
            import re
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                decision = json.loads(json_match.group())
                return decision
        except Exception as e:
            print(f"AI trading decision failed: {e}")

        return {"available": False, "market_analysis": "AI分析失败，本次不执行交易。", "trades": [], "risk_warning": ""}

    async def execute_daily_trading(self, dry_run: bool = False) -> dict:
        """执行每日自动交易"""
        today = date.today()
        if today.weekday() >= 5:
            return {"status": "skip", "reason": "周末不交易"}

        account = await self._get_or_create_account()
        await self._update_positions_prices()

        # 止损止盈检查
        sell_orders = await self._check_stop_profit_loss()

        # AI决策
        decision = await self.get_ai_trading_decision()
        if not decision.get("available", True):
            return {
                "status": "unavailable",
                "date": today.isoformat(),
                "market_analysis": decision.get("market_analysis", ""),
                "risk_warning": decision.get("risk_warning", ""),
                "trades": [],
                "total_buy_amount": 0,
            }
        trades_executed = []
        total_buy_amount = 0

        # 先执行卖出
        for order in sell_orders:
            if not dry_run:
                await self._execute_sell(order["stock_code"], order["shares"], order["price"], order["reason"])
            trades_executed.append({**order, "type": "sell"})

        # 计算可用资金（考虑即将执行的卖出）
        available_cash = account.cash + sum(o["amount"] for o in sell_orders if o.get("amount"))

        # 执行AI买入决策
        for trade in decision.get("trades", [])[:5]:
            if trade.get("action") != "buy":
                continue

            code = trade.get("stock_code", "")
            max_price = trade.get("max_price", 0)
            shares = min(trade.get("shares", 500), int(available_cash * 0.25 / max_price)) if max_price > 0 else 500

            if shares <= 0:
                continue

            actual_price = max_price
            price_data = await self._fetch_stock_price(code)
            if price_data:
                actual_price = price_data["price"]

            amount = actual_price * shares
            if amount > available_cash:
                shares = int(available_cash / actual_price / 100) * 100
                if shares < 100:
                    continue
                amount = actual_price * shares

            if not dry_run:
                await self._execute_buy(
                    code, trade.get("stock_name", ""), shares, actual_price,
                    trade.get("reason", ""), trade.get("score", 0),
                )

            trades_executed.append({
                "stock_code": code,
                "stock_name": trade.get("stock_name", ""),
                "type": "buy",
                "shares": shares,
                "price": actual_price,
                "amount": amount,
                "reason": trade.get("reason", ""),
                "score": trade.get("score", 0),
            })

            available_cash -= amount
            total_buy_amount += amount

        # 更新账户汇总
        if not dry_run and trades_executed:
            await self._update_account_summary()

        # 生成每日摘要
        if not dry_run:
            await self._generate_daily_summary(decision.get("market_analysis", ""))

        return {
            "status": "success",
            "date": today.isoformat(),
            "market_analysis": decision.get("market_analysis", ""),
            "risk_warning": decision.get("risk_warning", ""),
            "trades": trades_executed,
            "total_buy_amount": total_buy_amount,
        }

    async def _check_stop_profit_loss(self) -> list[dict]:
        """检查止盈止损"""
        orders = []
        async with async_session() as session:
            stmt = select(SimPosition).where(
                SimPosition.account_id == self.account_id,
                SimPosition.shares > 0,
            )
            result = await session.execute(stmt)
            positions = result.scalars().all()

            for pos in positions:
                pnl_pct = pos.pnl_pct
                reason = None
                if pnl_pct <= -8:
                    reason = f"止损：亏损{pnl_pct:.1f}%触发-8%止损线"
                elif pnl_pct >= 15:
                    reason = f"止盈：盈利{pnl_pct:.1f}%触发+15%止盈线"
                elif pos.hold_days >= 10 and pnl_pct < 3:
                    reason = f"持仓{pos.hold_days}天盈利不足3%，优化换股"

                if reason:
                    orders.append({
                        "stock_code": pos.stock_code,
                        "stock_name": pos.stock_name,
                        "shares": pos.shares,
                        "price": pos.current_price,
                        "amount": pos.market_value,
                        "reason": reason,
                        "pnl": pos.pnl,
                        "pnl_pct": pnl_pct,
                    })

        return orders

    async def _execute_buy(self, code: str, name: str, shares: int, price: float, reason: str, score: float):
        """执行买入"""
        amount = price * shares
        today = date.today()

        async with async_session() as session:
            # 扣款
            account = await session.get(SimAccount, self.account_id)
            account.cash -= amount

            # 更新或创建持仓
            stmt = select(SimPosition).where(
                SimPosition.account_id == self.account_id,
                SimPosition.stock_code == code,
            )
            result = await session.execute(stmt)
            pos = result.scalar_one_or_none()

            if pos and pos.shares > 0:
                total_cost = pos.avg_cost * pos.shares + amount
                pos.shares += shares
                pos.avg_cost = total_cost / pos.shares
            else:
                if not pos:
                    pos = SimPosition(
                        account_id=self.account_id,
                        stock_code=code,
                        stock_name=name,
                        avg_cost=price,
                        current_price=price,
                        buy_date=today,
                    )
                    session.add(pos)
                pos.shares = shares
                pos.avg_cost = price
                pos.current_price = price
                pos.market_value = amount
                pos.buy_date = today

            # 记录交易
            record = SimTradeRecord(
                account_id=self.account_id,
                trade_type="buy",
                stock_code=code,
                stock_name=name,
                shares=shares,
                price=price,
                amount=amount,
                ai_reason=reason,
                ai_score=score,
                trade_date=today,
            )
            session.add(record)

            account.trade_count = (account.trade_count or 0) + 1
            await session.commit()

    async def _execute_sell(self, code: str, shares: int, price: float, reason: str):
        """执行卖出"""
        amount = price * shares
        today = date.today()

        async with async_session() as session:
            account = await session.get(SimAccount, self.account_id)

            stmt = select(SimPosition).where(
                SimPosition.account_id == self.account_id,
                SimPosition.stock_code == code,
            )
            result = await session.execute(stmt)
            pos = result.scalar_one_or_none()

            if not pos or pos.shares < shares:
                return

            pnl = (price - pos.avg_cost) * shares
            pnl_pct = ((price - pos.avg_cost) / pos.avg_cost * 100) if pos.avg_cost > 0 else 0

            account.cash += amount
            pos.shares -= shares

            if pos.shares <= 0:
                pos.market_value = 0
                pos.pnl = 0
            else:
                pos.market_value = pos.shares * price
                pos.pnl = (price - pos.avg_cost) * pos.shares
                pos.pnl_pct = ((price - pos.avg_cost) / pos.avg_cost * 100) if pos.avg_cost > 0 else 0

            record = SimTradeRecord(
                account_id=self.account_id,
                trade_type="sell",
                stock_code=code,
                stock_name=pos.stock_name,
                shares=shares,
                price=price,
                amount=amount,
                pnl=pnl,
                pnl_pct=pnl_pct,
                ai_reason=reason,
                trade_date=today,
            )
            session.add(record)

            if pnl > 0:
                account.win_count = (account.win_count or 0) + 1
            account.trade_count = (account.trade_count or 0) + 1

            await session.commit()

    async def _update_account_summary(self):
        """更新账户总览"""
        async with async_session() as session:
            account = await session.get(SimAccount, self.account_id)

            stmt = select(func.sum(SimPosition.market_value)).where(
                SimPosition.account_id == self.account_id,
            )
            result = await session.execute(stmt)
            positions_value = result.scalar() or 0

            account.total_value = account.cash + positions_value
            account.total_pnl = account.total_value - account.initial_capital
            account.total_pnl_pct = (account.total_pnl / account.initial_capital * 100) if account.initial_capital > 0 else 0

            await session.commit()

    async def _generate_daily_summary(self, ai_comment: str = ""):
        """生成每日交易摘要"""
        today = date.today()

        async with async_session() as session:
            account = await session.get(SimAccount, self.account_id)

            # 今日交易
            stmt = select(SimTradeRecord).where(
                SimTradeRecord.account_id == self.account_id,
                SimTradeRecord.trade_date == today,
            )
            result = await session.execute(stmt)
            today_trades = result.scalars().all()

            # 当前持仓
            stmt = select(SimPosition).where(
                SimPosition.account_id == self.account_id,
                SimPosition.shares > 0,
            )
            result = await session.execute(stmt)
            positions = result.scalars().all()

            daily_pnl = sum(t.pnl or 0 for t in today_trades)
            positions_value = sum(p.market_value or 0 for p in positions)

            top_gainer = ""
            top_loser = ""
            for t in today_trades:
                if t.pnl and (not top_gainer or t.pnl > 0):
                    top_gainer = f"{t.stock_name}({t.pnl/1e4:.1f}万)" if t.pnl > 0 else top_gainer
                if t.pnl and (not top_loser or t.pnl < 0):
                    top_loser = f"{t.stock_name}({t.pnl/1e4:.1f}万)" if t.pnl < 0 else top_loser

            existing = await session.execute(
                select(SimDailySummary).where(
                    SimDailySummary.account_id == self.account_id,
                    SimDailySummary.summary_date == today,
                )
            )
            summary = existing.scalar_one_or_none()

            if not summary:
                summary = SimDailySummary(account_id=self.account_id, summary_date=today)
                session.add(summary)

            summary.daily_pnl = daily_pnl
            summary.daily_pnl_pct = (daily_pnl / account.total_value * 100) if account.total_value > 0 else 0
            summary.total_value = account.total_value
            summary.cash = account.cash
            summary.positions_value = positions_value
            summary.trade_count = len(today_trades)
            summary.positions_count = len(positions)
            summary.top_gainer = top_gainer
            summary.top_loser = top_loser
            summary.ai_summary = ai_comment

            account.daily_pnl = daily_pnl

            await session.commit()


    async def execute_all_strategies(self, dry_run: bool = False) -> dict:
        """执行所有策略账户的交易"""
        results = {}
        for key, strategy in StrategyProfile.STRATEGIES.items():
            self.account_id = strategy["account_id"]
            self.initial_capital = 1_000_000.0
            try:
                result = await self.execute_daily_trading(dry_run=dry_run)
                results[key] = {"name": strategy["name"], **result}
            except Exception as e:
                results[key] = {"name": strategy["name"], "error": str(e)}

        self.account_id = 1
        return {"status": "success", "strategies": results}


trading_engine = AITradingEngine()
