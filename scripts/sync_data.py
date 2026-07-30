#!/usr/bin/env python3
"""数据同步脚本：本地AKShare → Render后端"""
import json, sys
from datetime import date, datetime
from urllib.request import urlopen, Request

RENDER_URL = "https://ai-buffett-backend.onrender.com"

def fetch_and_push():
    try:
        import akshare as ak
        df = ak.stock_fund_flow_concept()
        if df is None or df.empty:
            print("AKShare返回空")
            return False

        stocks = []
        for _, row in df.iterrows():
            stocks.append({
                "code": str(row.get("序号", "")),
                "name": str(row.get("行业", "")),
                "close_price": float(row.get("行业指数", 0) or 0),
                "change_pct": float(row.get("行业-涨跌幅", 0) or 0),
                "main_net_inflow": int(float(row.get("净额", 0) or 0) * 1e8),
                "main_net_inflow_pct": 0,
                "super_large_net_inflow": 0, "large_net_inflow": 0,
                "medium_net_inflow": 0, "small_net_inflow": 0,
                "up_count": int(row.get("公司家数", 0) or 0),
                "down_count": 0,
                "leading_stock": str(row.get("领涨股", "")),
                "volume_ratio": 1.0, "turnover": 3.0,
            })

        # 直接用模拟交易的候选股接口推送
        data = json.dumps({"stocks": stocks}).encode()
        req = Request(
            f"{RENDER_URL}/api/v1/data/push",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urlopen(req, timeout=60)
        result = json.loads(resp.read())
        print(f"✅ 推送{len(stocks)}条: {result}")
        return True
    except Exception as e:
        print(f"❌ 失败: {e}")
        return False

if __name__ == "__main__":
    today = date.today()
    now = datetime.now()
    if today.weekday() >= 5:
        print("周末跳过"); sys.exit(0)
    if not (9 <= now.hour <= 15) and "--force" not in sys.argv:
        print(f"[{now:%H:%M}] 非交易时段，加 --force 强制"); sys.exit(0)
    fetch_and_push()
