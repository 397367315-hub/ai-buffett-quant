import asyncio
from datetime import date, datetime, timedelta
from sqlalchemy import select, func
from database import async_session
from models import ConceptFundFlowDaily, MarketFundFlowDaily
from services.data_collector import collector


class DataSyncService:
    """双数据源同步服务：东方财富 + AKShare 兜底"""

    @staticmethod
    async def sync_concept_flow(force: bool = False) -> dict:
        """同步概念板块数据到DB"""
        today = date.today()
        if today.weekday() >= 5:
            return {"status": "skip", "reason": "周末不更新"}

        # 检查今天是否已有数据
        if not force:
            async with async_session() as session:
                stmt = select(func.count()).where(
                    ConceptFundFlowDaily.trade_date == today
                )
                result = await session.execute(stmt)
                count = result.scalar() or 0
                if count > 0:
                    return {"status": "cached", "message": f"今日已有{count}条缓存数据", "count": count}

        # 方式1: 东方财富API
        data = await collector.fetch_concept_flow(page_size=200)

        # 方式2: AKShare兜底
        if not data:
            data = await DataSyncService._fetch_via_akshare()

        if not data:
            return {"status": "empty", "reason": "所有数据源均无数据（可能非交易时段）"}

        # 写入数据库
        count = 0
        async with async_session() as session:
            for item in data:
                try:
                    record = ConceptFundFlowDaily(
                        board_code=item.get("code", ""),
                        trade_date=today,
                        close_price=float(item.get("close_price", 0) or 0),
                        change_pct=float(item.get("change_pct", 0) or 0),
                        main_net_inflow=int(float(item.get("main_net_inflow", 0) or 0)),
                        main_net_inflow_pct=float(item.get("main_net_inflow_pct", 0) or 0),
                        super_large_net_inflow=int(float(item.get("super_large_net_inflow", 0) or 0)),
                        large_net_inflow=int(float(item.get("large_net_inflow", 0) or 0)),
                        medium_net_inflow=int(float(item.get("medium_net_inflow", 0) or 0)),
                        small_net_inflow=0,
                        up_count=int(float(item.get("up_count", 0) or 0)),
                        down_count=int(float(item.get("down_count", 0) or 0)),
                        leading_stock=item.get("leading_stock", ""),
                    )
                    session.add(record)
                    count += 1
                except Exception as e:
                    print(f"Error saving record: {e}")

            await session.commit()

        return {"status": "success", "message": f"同步{count}条数据", "count": count, "source": "akshare" if not collector else "eastmoney"}

    @staticmethod
    async def _fetch_via_akshare() -> list[dict]:
        """通过AKShare获取概念板块数据"""
        try:
            import akshare as ak
            df = ak.stock_concept_fund_flow_daily()
            if df is None or df.empty:
                return []

            results = []
            for _, row in df.iterrows():
                results.append({
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "close_price": float(row.get("最新价", 0) or 0),
                    "change_pct": float(row.get("涨跌幅", 0) or 0),
                    "main_net_inflow": int(float(row.get("主力净流入", 0) or 0)),
                    "main_net_inflow_pct": float(row.get("主力净流入占比", 0) or 0),
                    "super_large_net_inflow": int(float(row.get("超大单净流入", 0) or 0)),
                    "large_net_inflow": int(float(row.get("大单净流入", 0) or 0)),
                    "medium_net_inflow": int(float(row.get("中单净流入", 0) or 0)),
                    "small_net_inflow": int(float(row.get("小单净流入", 0) or 0)),
                    "up_count": int(float(row.get("上涨家数", 0) or 0)),
                    "down_count": int(float(row.get("下跌家数", 0) or 0)),
                    "leading_stock": str(row.get("领涨股", "")),
                })
            return results
        except Exception as e:
            print(f"AKShare fetch error: {e}")
            return []

    @staticmethod
    async def sync_daily_market_data() -> dict:
        """同步每日大盘指数数据"""
        try:
            import akshare as ak
            df = ak.stock_zh_index_daily(symbol="sh000001")
            if df is None or df.empty:
                return {"status": "empty"}

            latest = df.iloc[-1]
            today = date.today()

            async with async_session() as session:
                existing = await session.execute(
                    select(MarketFundFlowDaily).where(
                        MarketFundFlowDaily.trade_date == today
                    )
                )
                if existing.scalar_one_or_none():
                    return {"status": "cached"}

                record = MarketFundFlowDaily(
                    trade_date=today,
                    market="沪深两市",
                    main_net_inflow=0,
                )
                session.add(record)
                await session.commit()

            return {"status": "success", "message": f"同步大盘指数: {latest['close']}"}
        except Exception as e:
            print(f"Market data sync error: {e}")
            return {"status": "error", "error": str(e)}

    @staticmethod
    async def get_cache_stats() -> dict:
        """获取缓存统计"""
        async with async_session() as session:
            stmt = select(func.count(), func.max(ConceptFundFlowDaily.trade_date))
            result = await session.execute(stmt)
            count, latest_date = result.one()

            stmt2 = select(ConceptFundFlowDaily.trade_date).distinct().order_by(
                ConceptFundFlowDaily.trade_date.desc()
            ).limit(7)
            result2 = await session.execute(stmt2)
            recent_dates = [r[0].isoformat() for r in result2.all()]

        return {
            "total_records": count,
            "latest_date": latest_date.isoformat() if latest_date else None,
            "recent_dates": recent_dates,
            "cache_healthy": count > 0,
        }


data_sync = DataSyncService()
