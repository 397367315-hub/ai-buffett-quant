import math
from datetime import date, timedelta
from sqlalchemy import select, func
from database import async_session
from sim_models import SimAccount, SimPosition, SimTradeRecord, SimDailySummary


class StrategyProfile:
    """策略配置"""

    STRATEGIES = {
        "balanced": {
            "name": "均衡型",
            "account_id": 1,
            "description": "资金面+技术面+估值均衡，适合大多数市场",
            "weights_override": {
                "fund_flow": 0.28, "momentum": 0.22, "valuation": 0.20,
                "liquidity": 0.15, "sector_strength": 0.10, "risk": 0.05,
            },
            "max_positions": 5,
            "stop_loss": -8,
            "take_profit": 15,
            "max_single_position": 0.25,
            "min_score": 55,
        },
        "aggressive": {
            "name": "激进型",
            "account_id": 2,
            "description": "重动量追强势股，高换手，适合牛市",
            "weights_override": {
                "fund_flow": 0.35, "momentum": 0.30, "valuation": 0.05,
                "liquidity": 0.15, "sector_strength": 0.12, "risk": 0.03,
            },
            "max_positions": 3,
            "stop_loss": -5,
            "take_profit": 25,
            "max_single_position": 0.40,
            "min_score": 60,
        },
        "conservative": {
            "name": "保守型",
            "account_id": 3,
            "description": "重估值+ROE，低换手，适合熊市或震荡市",
            "weights_override": {
                "fund_flow": 0.15, "momentum": 0.10, "valuation": 0.35,
                "liquidity": 0.10, "sector_strength": 0.10, "risk": 0.20,
            },
            "max_positions": 8,
            "stop_loss": -10,
            "take_profit": 12,
            "max_single_position": 0.15,
            "min_score": 50,
        },
    }


class RiskMetrics:
    """风控指标计算"""

    @staticmethod
    async def calculate(account_id: int, days: int = 30) -> dict:
        """计算核心风控指标"""
        async with async_session() as session:
            account = await session.get(SimAccount, account_id)
            if not account:
                return {}

            stmt = (
                select(SimDailySummary)
                .where(
                    SimDailySummary.account_id == account_id,
                    SimDailySummary.summary_date >= date.today() - timedelta(days=days),
                )
                .order_by(SimDailySummary.summary_date.asc())
            )
            result = await session.execute(stmt)
            daily = result.scalars().all()

        if len(daily) < 2:
            return {"error": "数据不足"}

        returns = [d.daily_pnl_pct or 0 for d in daily if d.daily_pnl_pct is not None]
        values = [d.total_value or 0 for d in daily]

        if not returns:
            return {"error": "无收益数据"}

        # 年化收益率
        total_return = (values[-1] - account.initial_capital) / account.initial_capital if account.initial_capital > 0 else 0
        annual_return = total_return * (252 / len(returns)) if len(returns) > 0 else 0

        # 年化波动率
        avg_return = sum(returns) / len(returns)
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
        daily_vol = math.sqrt(variance)
        annual_vol = daily_vol * math.sqrt(252)

        # 夏普比率（假设无风险利率2%）
        rf_daily = 0.02 / 252
        sharpe = (avg_return - rf_daily) / max(daily_vol, 0.0001) * math.sqrt(252)

        # 最大回撤
        peak = values[0]
        max_dd = 0
        max_dd_date = ""
        for i, v in enumerate(values):
            peak = max(peak, v)
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                max_dd_date = daily[i].summary_date.isoformat() if i < len(daily) else ""

        # Calmar比率
        calmar = annual_return / max(max_dd, 0.01)

        # 胜率
        win_trades_stmt = (
            select(func.count()).where(
                SimTradeRecord.account_id == account_id,
                SimTradeRecord.trade_type == "sell",
                SimTradeRecord.pnl > 0,
            )
        )
        total_trades_stmt = (
            select(func.count()).where(
                SimTradeRecord.account_id == account_id,
                SimTradeRecord.trade_type == "sell",
            )
        )
        win_result = await session.execute(win_trades_stmt)
        total_result = await session.execute(total_trades_stmt)
        win_count = win_result.scalar() or 0
        total_count = total_result.scalar() or 1
        win_rate = round(win_count / total_count * 100, 1) if total_count > 0 else 0

        # 平均盈亏比
        avg_win_stmt = select(func.avg(SimTradeRecord.pnl)).where(
            SimTradeRecord.account_id == account_id,
            SimTradeRecord.trade_type == "sell",
            SimTradeRecord.pnl > 0,
        )
        avg_loss_stmt = select(func.avg(SimTradeRecord.pnl)).where(
            SimTradeRecord.account_id == account_id,
            SimTradeRecord.trade_type == "sell",
            SimTradeRecord.pnl < 0,
        )
        avg_win_result = await session.execute(avg_win_stmt)
        avg_loss_result = await session.execute(avg_loss_stmt)
        avg_win = avg_win_result.scalar() or 0
        avg_loss = abs(avg_loss_result.scalar() or 0)
        profit_loss_ratio = round(avg_win / max(avg_loss, 1), 2)

        return {
            "total_return": round(total_return * 100, 2),
            "annual_return": round(annual_return * 100, 2),
            "annual_volatility": round(annual_vol * 100, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_date": max_dd_date,
            "calmar_ratio": round(calmar, 2),
            "win_rate": win_rate,
            "profit_loss_ratio": profit_loss_ratio,
            "trading_days": len(returns),
        }


class Attribution:
    """收益归因分析"""

    @staticmethod
    async def analyze(account_id: int) -> dict:
        """分解收益来源"""
        async with async_session() as session:
            # 所有卖出的交易
            stmt = (
                select(SimTradeRecord)
                .where(
                    SimTradeRecord.account_id == account_id,
                    SimTradeRecord.trade_type == "sell",
                )
            )
            result = await session.execute(stmt)
            trades = result.scalars().all()

            if not trades:
                return {"error": "无已平仓交易"}

            total_pnl = sum(t.pnl or 0 for t in trades)
            winning = [t for t in trades if (t.pnl or 0) > 0]
            losing = [t for t in trades if (t.pnl or 0) < 0]

            # 按板块归因（简化：用股票名字推断板块）
            sector_pnl = {}
            for t in trades:
                name = t.stock_name or ""
                if any(kw in name for kw in ["酒", "茅台", "五粮"]):
                    sector = "白酒"
                elif any(kw in name for kw in ["电池", "迪", "绿能", "新能源"]):
                    sector = "新能源"
                elif any(kw in name for kw in ["芯", "微", "华创", "韦尔", "中际"]):
                    sector = "半导体/AI"
                elif any(kw in name for kw in ["证券", "中信"]):
                    sector = "券商"
                elif any(kw in name for kw in ["银行", "平安"]):
                    sector = "银行"
                elif any(kw in name for kw in ["讯飞", "寒武", "智能", "科技"]):
                    sector = "科技"
                else:
                    sector = "其他"

                if sector not in sector_pnl:
                    sector_pnl[sector] = {"pnl": 0, "count": 0, "wins": 0}
                sector_pnl[sector]["pnl"] += (t.pnl or 0)
                sector_pnl[sector]["count"] += 1
                if (t.pnl or 0) > 0:
                    sector_pnl[sector]["wins"] += 1

            # 按持仓天数归因
            hold_day_pnl = {"短期(1-3天)": {"pnl": 0, "count": 0}, "中期(4-7天)": {"pnl": 0, "count": 0}, "长期(8+天)": {"pnl": 0, "count": 0}}
            for t in trades:
                hold_days = (t.trade_date - date.today()).days if t.trade_date else 0
                hold_days = abs(hold_days)
                if hold_days <= 3:
                    bucket = "短期(1-3天)"
                elif hold_days <= 7:
                    bucket = "中期(4-7天)"
                else:
                    bucket = "长期(8+天)"
                hold_day_pnl[bucket]["pnl"] += (t.pnl or 0)
                hold_day_pnl[bucket]["count"] += 1

            return {
                "total_pnl": total_pnl,
                "total_trades": len(trades),
                "winning_trades": len(winning),
                "losing_trades": len(losing),
                "sector_attribution": {k: {"pnl": round(v["pnl"], 0), "count": v["count"], "win_rate": round(v["wins"] / max(v["count"], 1) * 100)} for k, v in sector_pnl.items()},
                "hold_period_attribution": {k: {"pnl": round(v["pnl"], 0), "count": v["count"]} for k, v in hold_day_pnl.items()},
                "avg_win": round(sum((t.pnl or 0) for t in winning) / max(len(winning), 1), 0),
                "avg_loss": round(sum((t.pnl or 0) for t in losing) / max(len(losing), 1), 0),
            }


class Benchmark:
    """大盘基准对比"""

    @staticmethod
    async def get_benchmark_data(days: int = 30) -> list[dict]:
        """获取上证指数作为基准"""
        from services.data_collector import collector

        try:
            url = "https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get"
            params = {
                "lmt": str(days + 5),
                "klt": "101",
                "secid": "1.000001",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52",
            }
            data = await collector.fetch_json(url, params)

            if not data.get("data") or not data["data"].get("klines"):
                return Benchmark._get_simulated_benchmark(days)

            result = []
            for line in data["data"]["klines"]:
                parts = line.split(",")
                if len(parts) >= 2:
                    result.append({"date": parts[0], "close": float(parts[1]) if parts[1] != "-" else 0})
            return result
        except:
            return Benchmark._get_simulated_benchmark(days)

    @staticmethod
    def _get_simulated_benchmark(days: int) -> list[dict]:
        """模拟上证指数基准线"""
        import random
        today = date.today()
        price = 3200
        result = []
        for i in range(days, -1, -1):
            d = today - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            price += random.uniform(-30, 35)
            result.append({"date": d.isoformat(), "close": round(price, 2)})
        return result
