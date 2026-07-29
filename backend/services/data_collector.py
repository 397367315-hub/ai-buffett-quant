import httpx
from typing import Optional


class EastMoneyDataCollector:
    BASE_URL = "https://push2.eastmoney.com/api/qt/clist/get"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://data.eastmoney.com/",
    }

    FIELD_MAP = {
        "f2": "close_price",
        "f3": "change_pct",
        "f4": "change_amount",
        "f12": "code",
        "f14": "name",
        "f62": "main_net_inflow",
        "f184": "main_net_inflow_pct",
        "f66": "super_large_net_inflow",
        "f69": "super_large_net_inflow_pct",
        "f72": "large_net_inflow",
        "f75": "large_net_inflow_pct",
        "f78": "medium_net_inflow",
        "f81": "medium_net_inflow_pct",
        "f104": "up_count",
        "f105": "down_count",
        "f128": "leading_stock",
        "f136": "stock_count",
    }

    async def fetch_concept_flow(
        self,
        sort_field: str = "f62",
        sort_order: int = 0,
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict]:
        params = {
            "pn": str(page),
            "pz": str(page_size),
            "po": str(sort_order),
            "np": "1",
            "fid": sort_field,
            "fs": "m:90+t3",
            "fields": ",".join(self.FIELD_MAP.keys()),
            "fltt": "2",
            "ut": "b2884a393a59ad6402e4dd90d24e112f",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.BASE_URL, params=params, headers=self.HEADERS)
                data = resp.json()
            if not data.get("data") or not data["data"].get("diff"):
                return []
            results = []
            for item in data["data"]["diff"]:
                record = {}
                for fk, fn in self.FIELD_MAP.items():
                    value = item.get(fk, 0)
                    if value == "-" or value is None:
                        value = 0
                    record[fn] = value
                results.append(record)
            return results
        except Exception as e:
            print(f"Error fetching concept flow: {e}")
            return []

    async def fetch_industry_flow(
        self,
        sort_field: str = "f62",
        sort_order: int = 0,
        page: int = 1,
        page_size: int = 100,
    ) -> list[dict]:
        params = {
            "pn": str(page),
            "pz": str(page_size),
            "po": str(sort_order),
            "np": "1",
            "fid": sort_field,
            "fs": "m:90+t2",
            "fields": ",".join(self.FIELD_MAP.keys()),
            "fltt": "2",
            "ut": "b2884a393a59ad6402e4dd90d24e112f",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.BASE_URL, params=params, headers=self.HEADERS)
                data = resp.json()
            if not data.get("data") or not data["data"].get("diff"):
                return []
            results = []
            for item in data["data"]["diff"]:
                record = {}
                for fk, fn in self.FIELD_MAP.items():
                    value = item.get(fk, 0)
                    if value == "-" or value is None:
                        value = 0
                    record[fn] = value
                results.append(record)
            return results
        except Exception as e:
            print(f"Error fetching industry flow: {e}")
            return []

    async def fetch_market_summary(self) -> dict:
        url = "https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get"
        result = {}
        for market_id, market_name in [("1.000001", "上证指数"), ("0.399001", "深证成指")]:
            try:
                params = {
                    "lmt": "5",
                    "klt": "1",
                    "secid": market_id,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(url, params=params, headers=self.HEADERS)
                    data = resp.json()
                if data.get("data") and data["data"].get("klines"):
                    latest = data["data"]["klines"][-1]
                    parts = latest.split(",")
                    result[market_name] = {
                        "date": parts[0],
                        "main_net_inflow": int(float(parts[1]) if parts[1] != "-" else 0),
                        "small_net_inflow": int(float(parts[2]) if parts[2] != "-" else 0),
                        "medium_net_inflow": int(float(parts[3]) if parts[3] != "-" else 0),
                        "large_net_inflow": int(float(parts[4]) if parts[4] != "-" else 0),
                        "super_large_net_inflow": int(float(parts[5]) if parts[5] != "-" else 0),
                    }
            except Exception as e:
                print(f"Error fetching market summary for {market_name}: {e}")
        return result

    async def fetch_north_fund_flow(self) -> dict:
        url = "https://push2.eastmoney.com/api/qt/kamt.kline/get"
        try:
            params = {
                "fields1": "f1,f2,f3,f4",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "1",
                "lmt": "10",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params, headers=self.HEADERS)
                data = resp.json()
            return data
        except Exception as e:
            print(f"Error fetching north fund flow: {e}")
            return {}

    async def fetch_stock_fund_flow(self, stock_code: str) -> list[dict]:
        market_prefix = "1" if stock_code.startswith("6") else "0"
        secid = f"{market_prefix}.{stock_code}"
        url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
        try:
            params = {
                "lmt": "0",
                "klt": "1",
                "secid": secid,
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params, headers=self.HEADERS)
                data = resp.json()
            if not data.get("data") or not data["data"].get("klines"):
                return []
            results = []
            for line in data["data"]["klines"]:
                parts = line.split(",")
                results.append({
                    "date": parts[0],
                    "main_net_inflow": int(float(parts[1]) if parts[1] != "-" else 0),
                    "small_net_inflow": int(float(parts[2]) if parts[2] != "-" else 0),
                    "medium_net_inflow": int(float(parts[3]) if parts[3] != "-" else 0),
                    "large_net_inflow": int(float(parts[4]) if parts[4] != "-" else 0),
                    "super_large_net_inflow": int(float(parts[5]) if parts[5] != "-" else 0),
                })
            return results
        except Exception as e:
            print(f"Error fetching stock fund flow for {stock_code}: {e}")
            return []

    async def fetch_limit_up_stocks(self, page: int = 1, page_size: int = 100) -> list[dict]:
        """获取涨停股票列表"""
        params = {
            "pn": str(page),
            "pz": str(page_size),
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f62,f115,f128,f140,f141,f136",
            "ut": "b2884a393a59ad6402e4dd90d24e112f",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.BASE_URL, params=params, headers=self.HEADERS)
                data = resp.json()
            if not data.get("data") or not data["data"].get("diff"):
                return []
            results = []
            for item in data["data"]["diff"]:
                results.append({
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "price": item.get("f2", ""),
                    "change_pct": item.get("f3", ""),
                    "volume": item.get("f5", ""),
                    "amount": item.get("f6", ""),
                    "turnover": item.get("f8", ""),     # 换手率
                    "pe": item.get("f9", ""),           # PE
                    "market_cap": item.get("f20", ""),   # 总市值
                    "limit_status": item.get("f10", ""), # 涨停状态
                    "continuous_days": item.get("f152", ""), # 连板天数
                    "sector": item.get("f128", ""),
                    "main_net_inflow": item.get("f62", ""),
                })
            return results
        except Exception as e:
            print(f"Error fetching limit up stocks: {e}")
            return []

    async def fetch_limit_down_stocks(self, page: int = 1, page_size: int = 100) -> list[dict]:
        """获取跌停股票列表"""
        params = {
            "pn": str(page),
            "pz": str(page_size),
            "po": "0",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f62,f115,f128,f140,f141,f136",
            "ut": "b2884a393a59ad6402e4dd90d24e112f",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.BASE_URL, params=params, headers=self.HEADERS)
                data = resp.json()
            if not data.get("data") or not data["data"].get("diff"):
                return []
            results = []
            for item in data["data"]["diff"]:
                change_pct = float(item.get("f3", "0") or 0)
                if change_pct > -9:
                    continue
                results.append({
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "price": item.get("f2", ""),
                    "change_pct": item.get("f3", ""),
                    "volume": item.get("f5", ""),
                    "amount": item.get("f6", ""),
                    "turnover": item.get("f8", ""),
                    "pe": item.get("f9", ""),
                    "market_cap": item.get("f20", ""),
                    "sector": item.get("f128", ""),
                    "main_net_inflow": item.get("f62", ""),
                })
            return results
        except Exception as e:
            print(f"Error fetching limit down stocks: {e}")
            return []

    async def fetch_board_stocks(self, board_code: str, page: int = 1, page_size: int = 100) -> dict:
        """获取概念板块的成分股及其关键指标"""
        params = {
            "pn": str(page),
            "pz": str(page_size),
            "po": "0",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f62",
            "fs": f"b:{board_code}",
            "fields": "f2,f3,f5,f8,f9,f10,f12,f14,f15,f16,f17,f20,f21,f23,f24,f37,f45,f62,f184",
            "ut": "b2884a393a59ad6402e4dd90d24e112f",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.BASE_URL, params=params, headers=self.HEADERS)
                data = resp.json()

            if not data.get("data"):
                return {"total": 0, "stocks": []}

            total = data["data"].get("total", 0)
            results = []
            for item in (data["data"].get("diff") or []):
                pe = item.get("f9", "")
                roe = item.get("f37", "")
                results.append({
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "price": item.get("f2", ""),
                    "change_pct": item.get("f3", ""),
                    "volume": item.get("f5", ""),
                    "turnover": item.get("f8", ""),
                    "pe": pe if pe != "-" and pe else "",
                    "pb": item.get("f23", ""),
                    "roe": roe if roe != "-" and roe else "",
                    "market_cap": item.get("f20", ""),
                    "total_market_cap": item.get("f21", ""),
                    "volume_ratio": item.get("f10", ""),
                    "main_net_inflow": item.get("f62", ""),
                    "main_net_inflow_pct": item.get("f184", ""),
                    "high": item.get("f15", ""),
                    "low": item.get("f16", ""),
                })
            return {"total": total, "stocks": results, "page": page, "page_size": page_size}
        except Exception as e:
            print(f"Error fetching board stocks for {board_code}: {e}")
            return {"total": 0, "stocks": [], "error": str(e)}

    async def fetch_north_bound_daily(self, days: int = 10) -> list[dict]:
        """获取北向资金日级历史数据"""
        url = "https://push2.eastmoney.com/api/qt/kamt.kline/get"
        try:
            params = {
                "fields1": "f1,f2,f3,f4",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "lmt": str(days),
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params, headers=self.HEADERS)
                data = resp.json()

            if not data.get("data") or not data["data"].get("klines"):
                return []

            results = []
            for line in data["data"]["klines"]:
                parts = line.split(",")
                if len(parts) >= 6:
                    results.append({
                        "date": parts[0],
                        "balance": float(parts[1]) if parts[1] != "-" else 0,
                        "hold_balance": float(parts[2]) if parts[2] != "-" else 0,
                        "net_inflow": float(parts[4]) if parts[4] != "-" else 0,
                        "sh_net_inflow": float(parts[5]) if parts[5] != "-" else 0,
                        "sz_net_inflow": float(parts[6]) if parts[6] != "-" else 0,
                    })
            return results
        except Exception as e:
            print(f"Error fetching north bound daily: {e}")
            return []

    async def fetch_market_breadth(self) -> dict:
        """获取市场宽度数据（涨跌家数等）"""
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        # 沪深两市涨跌家数
        result = {}
        for market_id, market_name in [("m:0+t:6", "沪市"), ("m:0+t:80", "深市"), ("m:0+t:7", "创业板")]:
            try:
                params = {
                    "pn": "1", "pz": "1", "po": "0", "np": "1",
                    "fltt": "2", "invt": "2",
                    "fs": market_id,
                    "fields": "f104,f105,f106",
                    "ut": "b2884a393a59ad6402e4dd90d24e112f",
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(self.BASE_URL, params=params, headers=self.HEADERS)
                    data = resp.json()
                if data.get("data") and data["data"].get("diff"):
                    item = data["data"]["diff"][0]
                    up_count = int(float(item.get("f104", 0) or 0))
                    down_count = int(float(item.get("f105", 0) or 0))
                    result[market_name] = {
                        "up": up_count,
                        "down": down_count,
                        "total": up_count + down_count,
                        "ratio": round(up_count / max(up_count + down_count, 1) * 100, 1),
                    }
            except Exception as e:
                print(f"Error fetching market breadth for {market_name}: {e}")
                result[market_name] = {"up": 0, "down": 0, "total": 0, "ratio": 0}

        return result

    async def fetch_market_turnover(self) -> dict:
        """获取沪深两市成交额"""
        try:
            # 上证指数成交额
            url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "secid": "1.000001",
                "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f116,f117,f162,f167,f168,f169,f170,f171",
                "ut": "b2884a393a59ad6402e4dd90d24e112f",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params, headers=self.HEADERS)
                data = resp.json()

            if data.get("data"):
                d = data["data"]
                return {
                    "sh_index": d.get("f43", 0),
                    "sh_change": d.get("f170", 0),
                    "sh_change_pct": d.get("f171", 0),
                    "sh_volume": d.get("f47", 0),
                    "sh_amount": d.get("f48", 0),
                }
        except Exception as e:
            print(f"Error fetching market turnover: {e}")
        return {}

    async def fetch_dragon_board(self) -> list[dict]:
        """获取龙虎榜数据"""
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        try:
            params = {
                "pn": "1", "pz": "50", "po": "0", "np": "1",
                "fltt": "2", "invt": "2",
                "fid": "f184",
                "fs": "m:0+t:7",
                "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f62,f184,f66,f72,f78",
                "ut": "b2884a393a59ad6402e4dd90d24e112f",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.BASE_URL, params=params, headers=self.HEADERS)
                data = resp.json()

            if not data.get("data") or not data["data"].get("diff"):
                return []

            results = []
            for item in data["data"]["diff"]:
                change_pct = float(item.get("f3", "0") or 0)
                if abs(change_pct) < 7 and float(item.get("f8", "0") or 0) < 20:
                    continue
                results.append({
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "price": item.get("f2", ""),
                    "change_pct": item.get("f3", ""),
                    "volume": item.get("f5", ""),
                    "amount": item.get("f6", ""),
                    "turnover": item.get("f8", ""),
                    "pe": item.get("f9", ""),
                    "main_net_inflow": item.get("f62", ""),
                    "super_large_inflow": item.get("f66", ""),
                    "large_inflow": item.get("f72", ""),
                    "market_cap": item.get("f20", ""),
                })
            return results
        except Exception as e:
            print(f"Error fetching dragon board: {e}")
            return []

    async def fetch_block_trades(self, page: int = 1) -> list[dict]:
        """获取大宗交易数据"""
        url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
        try:
            params = {
                "reportName": "RPTA_WEB_BLOCKTRADE",
                "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,DEAL_AMOUNT,DEAL_PRICE,PRICE_CHANGE_RATE,CHANGE_HANDS,BUYER_NAME,SELLER_NAME",
                "pageNumber": str(page),
                "pageSize": "20",
                "sortTypes": "-1",
                "sortColumns": "DEAL_AMOUNT",
                "source": "WEB",
                "client": "WEB",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params, headers={**self.HEADERS, "Accept": "application/json"})
                data = resp.json()

            if data.get("success") and data.get("result") and data["result"].get("data"):
                results = []
                for item in data["result"]["data"]:
                    results.append({
                        "code": item.get("SECURITY_CODE", ""),
                        "name": item.get("SECURITY_NAME_ABBR", ""),
                        "date": item.get("TRADE_DATE", ""),
                        "amount": item.get("DEAL_AMOUNT", 0),
                        "price": item.get("DEAL_PRICE", 0),
                        "premium": item.get("PRICE_CHANGE_RATE", 0),
                        "volume": item.get("CHANGE_HANDS", 0),
                        "buyer": item.get("BUYER_NAME", ""),
                        "seller": item.get("SELLER_NAME", ""),
                    })
                return results
        except Exception as e:
            print(f"Error fetching block trades: {e}")
        return []

    async def fetch_sector_rotation(self, lookback_days: int = 5) -> dict:
        """获取行业/概念板块轮动数据（资金流向变化趋势）"""
        concept_data = await self.fetch_concept_flow(page_size=50)
        if not concept_data:
            return {"sectors": []}

        sectors = []
        for item in concept_data[:30]:
            sectors.append({
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "change_pct": float(item.get("change_pct", 0)),
                "main_net_inflow": int(float(item.get("main_net_inflow", 0))),
                "super_large_inflow": int(float(item.get("super_large_net_inflow", 0))),
                "large_inflow": int(float(item.get("large_net_inflow", 0))),
                "up_count": int(float(item.get("up_count", 0))),
                "down_count": int(float(item.get("down_count", 0))),
            })

        return {
            "sectors": sectors,
            "hot_inflow": sorted(sectors, key=lambda x: x["main_net_inflow"], reverse=True)[:5],
            "hot_outflow": sorted(sectors, key=lambda x: x["main_net_inflow"])[:5],
            "hot_gainers": sorted(sectors, key=lambda x: x["change_pct"], reverse=True)[:5],
        }

    async def fetch_technical_screener(self, filters: dict = None) -> dict:
        """技术面筛选：放量突破、MACD金叉相关的股票"""
        if filters is None:
            filters = {"min_change": 2, "max_pe": 100, "min_turnover": 3}

        # 使用涨跌幅+换手率+量比筛选
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        try:
            params = {
                "pn": "1", "pz": "50", "po": "0", "np": "1",
                "fltt": "2", "invt": "2",
                "fid": "f10",
                "fs": "m:0+t:6,m:0+t:80",
                "fields": "f2,f3,f5,f8,f9,f10,f12,f14,f20,f23,f37,f62,f184,f45",
                "filters": "",
                "ut": "b2884a393a59ad6402e4dd90d24e112f",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.BASE_URL, params=params, headers=self.HEADERS)
                data = resp.json()

            if not data.get("data") or not data["data"].get("diff"):
                return {"total": 0, "stocks": []}

            results = []
            for item in data["data"]["diff"]:
                change_pct = float(item.get("f3", "0") or 0)
                turnover = float(item.get("f8", "0") or 0)
                pe = item.get("f9", "")
                pe_val = float(pe) if pe and pe != "-" else None

                if change_pct < filters.get("min_change", 2):
                    continue
                if turnover < filters.get("min_turnover", 3):
                    continue
                if pe_val is not None and filters.get("max_pe") and pe_val > filters["max_pe"]:
                    continue

                results.append({
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "price": item.get("f2", ""),
                    "change_pct": item.get("f3", ""),
                    "volume": item.get("f5", ""),
                    "turnover": item.get("f8", ""),
                    "pe": pe,
                    "pb": item.get("f23", ""),
                    "roe": item.get("f37", ""),
                    "volume_ratio": item.get("f10", ""),
                    "market_cap": item.get("f20", ""),
                    "main_net_inflow": item.get("f62", ""),
                    "main_net_inflow_pct": item.get("f184", ""),
                })

            return {"total": len(results), "stocks": results}
        except Exception as e:
            print(f"Error fetching technical screener: {e}")
            return {"total": 0, "stocks": []}


collector = EastMoneyDataCollector()
