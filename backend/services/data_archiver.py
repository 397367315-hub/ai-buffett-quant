import random
from datetime import date, timedelta
from sqlalchemy import select
from database import async_session
from models import ConceptFundFlowDaily, MarketFundFlowDaily, ConceptBoard


async def generate_historical_data(days: int = 30, base_date: date = None):
    """为演示生成概念板块历史数据（使用随机但合理的数据）"""
    if base_date is None:
        base_date = date.today()

    async with async_session() as session:
        # 获取所有概念板块
        stmt = select(ConceptBoard)
        result = await session.execute(stmt)
        boards = result.scalars().all()

        if not boards:
            print("No concept boards found in database. Run seed_data.py first.")
            return

        names = [b.name for b in boards]
        codes = [b.code for b in boards]

        records = []
        for day_offset in range(days):
            d = base_date - timedelta(days=day_offset + 1)
            # 跳过周末
            if d.weekday() >= 5:
                continue

            # 模拟市场情绪：整体偏牛或偏熊
            market_sentiment = random.uniform(-1, 1)

            for code, name in zip(codes, names):
                change_pct = round(random.uniform(-5, 5) + market_sentiment * 2, 2)
                base_flow = random.randint(-5_000_000_000, 15_000_000_000)
                main_inflow = int(base_flow * (1 + market_sentiment * 0.5 + random.uniform(-0.3, 0.3)))

                record = ConceptFundFlowDaily(
                    board_code=code,
                    trade_date=d,
                    close_price=random.uniform(500, 5000),
                    change_pct=change_pct,
                    main_net_inflow=main_inflow,
                    main_net_inflow_pct=round(random.uniform(-10, 10), 2),
                    super_large_net_inflow=int(main_inflow * random.uniform(0.3, 0.7)),
                    large_net_inflow=int(main_inflow * random.uniform(0.2, 0.4)),
                    medium_net_inflow=int(-main_inflow * random.uniform(0.1, 0.5)),
                    small_net_inflow=int(-main_inflow * random.uniform(0.3, 0.6)),
                    up_count=random.randint(20, 150),
                    down_count=random.randint(10, 100),
                    leading_stock=random.choice(["中芯国际", "宁德时代", "贵州茅台", "中际旭创", "中信证券"]),
                )
                records.append(record)

        # 批量插入
        for r in records:
            session.add(r)
        await session.commit()

    print(f"Generated {len(records)} historical records for {len(codes)} boards over {days} days")


async def archive_today_data():
    """将今日实时数据归档到数据库"""
    from services.data_collector import collector

    today = date.today()
    if today.weekday() >= 5:
        print("Today is weekend, skipping archive")
        return

    async with async_session() as session:
        # 获取概念板块实时数据
        concept_data = await collector.fetch_concept_flow(page_size=200)

        if not concept_data:
            print("No real-time data available (可能非交易时段)")
            return

        count = 0
        for item in concept_data:
            try:
                record = ConceptFundFlowDaily(
                    board_code=item.get("code", ""),
                    trade_date=today,
                    close_price=float(item.get("close_price", 0)),
                    change_pct=float(item.get("change_pct", 0)),
                    main_net_inflow=int(float(item.get("main_net_inflow", 0))),
                    main_net_inflow_pct=float(item.get("main_net_inflow_pct", 0)),
                    super_large_net_inflow=int(float(item.get("super_large_net_inflow", 0))),
                    large_net_inflow=int(float(item.get("large_net_inflow", 0))),
                    medium_net_inflow=int(float(item.get("medium_net_inflow", 0))),
                    small_net_inflow=0,
                    up_count=int(float(item.get("up_count", 0))),
                    down_count=int(float(item.get("down_count", 0))),
                    leading_stock=item.get("leading_stock", ""),
                )
                session.add(record)
                count += 1
            except Exception as e:
                print(f"Error archiving {item.get('name', 'unknown')}: {e}")

        await session.commit()
        print(f"Archived {count} concept board records for {today}")
