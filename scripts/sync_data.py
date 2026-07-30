#!/usr/bin/env python3
"""数据同步脚本：从本地AKShare拉取A股数据，推送到Render后端"""
import json
import time
import sys
from datetime import date, datetime
from urllib.request import urlopen, Request

RENDER_URL = "https://ai-buffett-backend.onrender.com"

def fetch_concept_data():
    """本地用AKShare拉取行业板块资金数据"""
    try:
        import akshare as ak
        df = ak.stock_fund_flow_concept()
        if df is None or df.empty:
            return []

        stocks = []
        for _, row in df.iterrows():
            stocks.append({
                "code": f"AK{row.get('序号', '')}",
                "name": str(row.get("行业", "")),
                "close_price": float(row.get("行业指数", 0) or 0),
                "change_pct": float(row.get("行业-涨跌幅", 0) or 0),
                "main_net_inflow": int(float(row.get("净额", 0) or 0) * 1e8),
                "leading_stock": str(row.get("领涨股", "")),
                "up_count": int(float(row.get("公司家数", 0) or 0)),
            })
        return stocks
    except Exception as e:
        print(f"  AKShare拉取失败: {e}")
        return []

def push_to_render(stocks):
    """推送数据到Render后端"""
    if not stocks:
        return False
    try:
        data = json.dumps({"stocks": stocks}).encode()
        req = Request(
            f"{RENDER_URL}/api/v1/data/sync-local",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urlopen(req, timeout=120)
        result = json.loads(resp.read())
        return result.get("code") == 0
    except Exception as e:
        print(f"  推送到Render失败: {e}")
        return False

def main():
    today = date.today()
    now = datetime.now()

    # 只在交易日运行（周一到周五 9:25-15:05）
    if today.weekday() >= 5:
        print(f"[{now:%H:%M:%S}] 周末跳过")
        return

    if not (9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 5)):
        print(f"[{now:%H:%M:%S}] 非交易时段，如需强制同步请加 --force")
        if "--force" not in sys.argv:
            return

    print(f"[{now:%H:%M:%S}] 开始同步...")
    stocks = fetch_concept_data()
    print(f"  拉取到 {len(stocks)} 条行业板块数据")

    if push_to_render(stocks):
        print(f"  ✅ 同步成功! {len(stocks)}条数据已推送到 {RENDER_URL}")
    else:
        print(f"  ❌ 同步失败")

if __name__ == "__main__":
    main()
