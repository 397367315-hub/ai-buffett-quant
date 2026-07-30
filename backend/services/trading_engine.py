import json
import random
from datetime import date, datetime, timedelta
from typing import Optional
from sqlalchemy import select, func, and_, desc
from database import async_session
from sim_models import SimAccount, SimPosition, SimTradeRecord, SimDailySummary
from services.data_collector import collector
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
                stock_data = await collector.fetch_stock_fund_flow(pos.stock_code)
                if stock_data and len(stock_data) > 0:
                    latest = stock_data[-1]
                    flow_amount = latest.get("main_net_inflow", 0)
                else:
                    flow_amount = 0

                # Get current price from market data
                price_data = await self._fetch_stock_price(pos.stock_code)
                if price_data:
                    pos.current_price = price_data["price"]
                    pos.market_value = pos.current_price * pos.shares
                    pos.pnl = (pos.current_price - pos.avg_cost) * pos.shares
                    pos.pnl_pct = ((pos.current_price - pos.avg_cost) / pos.avg_cost * 100) if pos.avg_cost > 0 else 0
                    pos.hold_days = (date.today() - pos.buy_date).days if pos.buy_date else 0

            await session.commit()

    async def _fetch_stock_price(self, stock_code: str) -> Optional[dict]:
        """获取单只股票的当前价格 - 使用已验证可用的 clist API"""
        try:
            # 使用 clist 接口按股票代码查询
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": "1", "pz": "1", "po": "0", "np": "1",
                "fltt": "2", "invt": "2", "fid": "f3",
                "fs": f"b:{stock_code}",
                "fields": "f2,f3,f4,f5,f8,f9,f10,f12,f14,f20,f23,f43,f44,f45,f47,f48,f50,f57,f58,f170",
                "ut": "b2884a393a59ad6402e4dd90d24e112f",
            }
            data = await collector.fetch_json(url, params)

            if data.get("data") and data["data"].get("diff"):
                d = data["data"]["diff"][0]
                return {
                    "price": d.get("f2", 0) or d.get("f43", 0),
                    "change_pct": d.get("f3", 0) or d.get("f170", 0),
                    "high": d.get("f15", 0) or d.get("f44", 0),
                    "low": d.get("f16", 0) or d.get("f45", 0),
                    "volume": d.get("f5", 0) or d.get("f47", 0),
                    "amount": d.get("f6", 0) or d.get("f48", 0),
                }
        except Exception as e:
            print(f"Error fetching price for {stock_code}: {e}")
        return None

    async def get_market_data_for_ai(self) -> dict:
        """收集市场数据供AI分析，无实时数据时使用模拟数据"""
        concepts = await collector.fetch_concept_flow(page_size=30)
        technical = await collector.fetch_technical_screener({
            "min_change": 2, "max_pe": 100, "min_turnover": 3,
        })
        limit_ups = await collector.fetch_limit_up_stocks()
        market_info = await collector.fetch_market_turnover()

        # 如果实时数据为空，使用模拟数据
        if not concepts or not technical.get("stocks"):
            return self._get_simulated_market_data()

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
            "market_index": market_info,
            "hot_concepts": top_concepts,
            "candidate_stocks": candidate_stocks,
            "current_positions": current_positions,
            "limit_up_count": len(limit_ups),
        }

    async def get_ai_trading_decision(self) -> dict:
        """AI分析市场数据，输出交易决策"""
        market_data = await self.get_market_data_for_ai()

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

        return {"market_analysis": "AI分析失败，使用默认策略", "trades": [], "risk_warning": ""}

    def _get_simulated_market_data(self) -> dict:
        """非交易时段使用模拟数据，价格贴近真实市场。交易时段API正常后会自动使用实时数据"""

        simulated_stocks = [
            {"code": "600519", "name": "贵州茅台", "price": 1440.00, "change_pct": 0.8, "turnover": 0.5, "pe": 25.0, "volume_ratio": 1.0, "main_inflow_yi": 2, "roe": 32.5},
            {"code": "000858", "name": "五粮液", "price": 125.00, "change_pct": 1.5, "turnover": 2.8, "pe": 15.5, "volume_ratio": 1.3, "main_inflow_yi": 3, "roe": 25.1},
            {"code": "300750", "name": "宁德时代", "price": 190.00, "change_pct": -0.3, "turnover": 1.8, "pe": 20.0, "volume_ratio": 0.9, "main_inflow_yi": -1, "roe": 18.9},
            {"code": "002594", "name": "比亚迪", "price": 250.00, "change_pct": 2.5, "turnover": 3.5, "pe": 30.0, "volume_ratio": 1.8, "main_inflow_yi": 5, "roe": 15.2},
            {"code": "300308", "name": "中际旭创", "price": 110.00, "change_pct": 4.5, "turnover": 6.0, "pe": 38.0, "volume_ratio": 2.5, "main_inflow_yi": 8, "roe": 12.8},
            {"code": "002371", "name": "北方华创", "price": 320.00, "change_pct": 3.0, "turnover": 4.5, "pe": 45.0, "volume_ratio": 2.0, "main_inflow_yi": 6, "roe": 22.3},
            {"code": "688981", "name": "中芯国际", "price": 45.00, "change_pct": -1.0, "turnover": 1.2, "pe": 75.0, "volume_ratio": 0.8, "main_inflow_yi": -3, "roe": 2.1},
            {"code": "603501", "name": "韦尔股份", "price": 95.00, "change_pct": 5.0, "turnover": 7.0, "pe": 35.0, "volume_ratio": 3.0, "main_inflow_yi": 10, "roe": 8.5},
            {"code": "601012", "name": "隆基绿能", "price": 15.50, "change_pct": 1.2, "turnover": 2.5, "pe": 10.5, "volume_ratio": 1.2, "main_inflow_yi": 1, "roe": 20.3},
            {"code": "002230", "name": "科大讯飞", "price": 40.50, "change_pct": 6.0, "turnover": 8.0, "pe": 55.0, "volume_ratio": 3.5, "main_inflow_yi": 12, "roe": 5.6},
            {"code": "600030", "name": "中信证券", "price": 19.00, "change_pct": -0.5, "turnover": 1.0, "pe": 14.0, "volume_ratio": 0.7, "main_inflow_yi": -1, "roe": 8.2},
            {"code": "688256", "name": "寒武纪", "price": 220.00, "change_pct": 7.0, "turnover": 12.0, "pe": -1, "volume_ratio": 4.5, "main_inflow_yi": 15, "roe": 0},
            {"code": "300760", "name": "迈瑞医疗", "price": 260.00, "change_pct": 1.5, "turnover": 2.0, "pe": 28.0, "volume_ratio": 1.1, "main_inflow_yi": 3, "roe": 35.8},
            {"code": "000001", "name": "平安银行", "price": 10.50, "change_pct": 0.3, "turnover": 0.8, "pe": 5.0, "volume_ratio": 0.9, "main_inflow_yi": 0.5, "roe": 12.1},
            {"code": "600809", "name": "山西汾酒", "price": 200.00, "change_pct": 2.5, "turnover": 3.0, "pe": 22.0, "volume_ratio": 1.5, "main_inflow_yi": 4, "roe": 38.2},
        ]

        return {
            "market_index": {"sh_index": 3250.50, "sh_change_pct": 0.85},
            "hot_concepts": [
                {"name": "人工智能", "change_pct": 3.2, "main_inflow_yi": 45, "leading_stock": "科大讯飞"},
                {"name": "半导体", "change_pct": 2.8, "main_inflow_yi": 32, "leading_stock": "北方华创"},
                {"name": "白酒", "change_pct": 1.5, "main_inflow_yi": 18, "leading_stock": "五粮液"},
                {"name": "新能源", "change_pct": 0.8, "main_inflow_yi": 12, "leading_stock": "比亚迪"},
                {"name": "券商", "change_pct": -0.3, "main_inflow_yi": -8, "leading_stock": "中信证券"},
            ],
            "candidate_stocks": simulated_stocks,
            "current_positions": [],
            "limit_up_count": 45,
            "simulated": True,
        }

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
