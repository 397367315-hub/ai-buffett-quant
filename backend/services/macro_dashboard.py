"""Verified global-market, calendar, and domestic-liquidity dashboard inputs."""

from __future__ import annotations

import asyncio
import csv
import math
import zipfile
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from zoneinfo import ZoneInfo

import httpx

from config import settings
from database import async_session
from models import MarketDataCache
from services.data_collector import collector, shanghai_now
from services.macro_policy_news import macro_policy_news_collector


SINA_QUOTES_URL = "https://hq.sinajs.cn/list=gb_inx,gb_dji,gb_ixic,hf_GC,hf_CL,DINIW"
# Eastmoney is used as a second public quote source when Sina is incomplete or
# unreachable. It is an observed quote source, not a fabricated historical fill.
EASTMONEY_GLOBAL_URL = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&"
    "fields=f1,f2,f3,f12,f13,f14,f124&secids=100.SPX,100.DJIA,100.NDX,"
    "101.GC00Y,102.CL00Y,100.UDI"
)
ECONOMIC_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FRED_MACRO_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10,DGS2,VIXCLS&cosd=2020-01-01"
# FRED is the preferred consolidated series. These official/public series are
# independent fallbacks for environments where the FRED host is unreachable.
TREASURY_YIELD_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
MOFCOM_SOCIAL_FINANCE_URL = "https://data.mofcom.gov.cn/datamofcom/front/gnmy/shrzgmQuery"
EASTMONEY_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _float(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _change_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current / previous - 1) * 100, 2)


def _source_age_minutes(source_time: object) -> float | None:
    if not source_time:
        return None
    try:
        parsed = datetime.fromisoformat(str(source_time).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
        return max(0.0, (shanghai_now() - parsed.astimezone(SHANGHAI_TZ)).total_seconds() / 60)
    except (TypeError, ValueError):
        return None


def _quote_meta(source_time: object, source: str) -> dict:
    age = _source_age_minutes(source_time)
    return {
        "is_realtime": bool(age is not None and age <= 60),
        "data_age_minutes": round(age, 1) if age is not None else None,
        "cache_used": False,
        "source": source,
    }


def _official_series_meta(source_time: object, source: str) -> dict:
    """Metadata for daily/monthly official series, which are never intraday live."""
    age = _source_age_minutes(source_time)
    return {
        "is_realtime": False,
        "data_age_minutes": round(age, 1) if age is not None else None,
        "cache_used": False,
        "source": source,
    }


def _parse_sina_lines(text: str) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    for raw_line in text.splitlines():
        prefix, separator, raw_payload = raw_line.partition("=")
        if not separator or "hq_str_" not in prefix:
            continue
        symbol = prefix.rsplit("hq_str_", 1)[-1].strip()
        payload = raw_payload.strip().rstrip(";").strip()
        if len(payload) < 2 or payload[0] != '"' or payload[-1] != '"':
            continue
        values = next(csv.reader(StringIO(payload[1:-1])), [])
        if values:
            parsed[symbol] = values
    return parsed


def parse_sina_market_payload(text: str) -> list[dict]:
    rows = _parse_sina_lines(text)
    result = []
    us_config = {
        "gb_inx": ("sp500", "标普500"),
        "gb_dji": ("dow", "道琼斯"),
        "gb_ixic": ("nasdaq", "纳斯达克"),
    }
    for symbol, (key, label) in us_config.items():
        values = rows.get(symbol) or []
        current = _float(values[1]) if len(values) > 1 else None
        pct = _float(values[2]) if len(values) > 2 else None
        result.append({
            "key": key,
            "label": label,
            "value": current,
            "change_pct": pct,
            "currency": "USD",
            "source_time": values[3] if len(values) > 3 else None,
            "available": current is not None,
            **_quote_meta(values[3] if len(values) > 3 else None, "新浪财经"),
        })

    futures_config = {
        "hf_GC": ("gold", "纽约黄金", "USD/盎司"),
        "hf_CL": ("oil", "纽约原油", "USD/桶"),
    }
    for symbol, (key, label, unit) in futures_config.items():
        values = rows.get(symbol) or []
        current = _float(values[0]) if values else None
        previous = _float(values[7]) if len(values) > 7 else None
        source_time = f"{values[12]} {values[6]}" if len(values) > 12 else None
        result.append({
            "key": key,
            "label": label,
            "value": current,
            "change_pct": _change_pct(current, previous),
            "currency": unit,
            "source_time": source_time,
            "available": current is not None,
            **_quote_meta(source_time, "新浪财经"),
        })

    dxy = rows.get("DINIW") or []
    current = _float(dxy[1]) if len(dxy) > 1 else None
    previous = _float(dxy[5]) if len(dxy) > 5 else None
    result.append({
        "key": "dxy",
        "label": "美元指数",
        "value": current,
        "change_pct": _change_pct(current, previous),
        "currency": "index",
        "source_time": f"{dxy[10]} {dxy[0]}" if len(dxy) > 10 else None,
        "available": current is not None,
        **_quote_meta(f"{dxy[10]} {dxy[0]}" if len(dxy) > 10 else None, "新浪财经"),
    })
    return result


def parse_eastmoney_market_payload(payload: object) -> list[dict]:
    rows = payload.get("data", {}).get("diff", []) if isinstance(payload, dict) else []
    configs = {
        "SPX": ("sp500", "标普500", "USD"),
        "DJIA": ("dow", "道琼斯", "USD"),
        "NDX": ("nasdaq", "纳斯达克", "USD"),
        "GC00Y": ("gold", "纽约黄金", "USD/盎司"),
        "CL00Y": ("oil", "纽约原油", "USD/桶"),
        "UDI": ("dxy", "美元指数", "index"),
    }
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("f12") or "")
        config = configs.get(symbol)
        current = _float(row.get("f2"))
        if not config or current is None:
            continue
        timestamp = row.get("f124")
        source_time = None
        try:
            if timestamp:
                source_time = datetime.fromtimestamp(float(timestamp), tz=SHANGHAI_TZ).isoformat()
        except (TypeError, ValueError, OSError):
            source_time = None
        result.append({
            "key": config[0], "label": config[1], "value": current,
            "change_pct": _float(row.get("f3")), "currency": config[2],
            "source_time": source_time, "available": True,
            **_quote_meta(source_time, "东方财富全球行情"),
        })
    return result


def _series_rows(text: str, field: str) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for item in csv.DictReader(StringIO(text)):
        stamp = str(item.get("observation_date") or "")[:10]
        value = _float(item.get(field))
        if stamp and value is not None:
            rows.append((stamp, value))
    return rows


def parse_fred_macro_zip(content: bytes) -> list[dict]:
    """Parse the public FRED graph export without requiring an API key."""
    with zipfile.ZipFile(BytesIO(content)) as archive:
        daily = archive.read("daily.csv").decode("utf-8-sig")
        vix_name = next(name for name in archive.namelist() if name.endswith("_close.csv"))
        vix = archive.read(vix_name).decode("utf-8-sig")

    result: list[dict] = []
    configs = (
        ("DGS10", "us10y", "美国10年期收益率", "%", "change_points"),
        ("DGS2", "us2y", "美国2年期收益率", "%", "change_points"),
    )
    for field, key, label, currency, change_kind in configs:
        rows = _series_rows(daily, field)
        if not rows:
            continue
        stamp, current = rows[-1]
        previous = rows[-2][1] if len(rows) > 1 else None
        result.append({
            "key": key,
            "label": label,
            "value": current,
            "change_pct": round(current - previous, 3) if previous is not None else None,
            "change_kind": change_kind,
            "currency": currency,
            "source_time": stamp,
            "available": True,
            **_official_series_meta(stamp, "FRED公开序列"),
        })

    rows = _series_rows(vix, "VIXCLS")
    if rows:
        stamp, current = rows[-1]
        previous = rows[-2][1] if len(rows) > 1 else None
        result.append({
            "key": "vix",
            "label": "VIX波动率",
            "value": current,
            "change_pct": round((current / previous - 1) * 100, 2) if previous not in (None, 0) else None,
            "change_kind": "relative_pct",
            "currency": "index",
            "source_time": stamp,
            "available": True,
            **_official_series_meta(stamp, "FRED公开序列"),
        })
    return result


def parse_treasury_yield_csv(text: str) -> list[dict]:
    """Parse the US Treasury daily par-yield CSV as a FRED fallback."""
    rows: list[tuple[str, float, float]] = []
    for item in csv.DictReader(StringIO(text)):
        stamp = str(item.get("Date") or "")
        try:
            parsed_date = datetime.strptime(stamp, "%m/%d/%Y").date()
        except (TypeError, ValueError):
            continue
        two_year = _float(item.get("2 Yr"))
        ten_year = _float(item.get("10 Yr"))
        if two_year is not None and ten_year is not None:
            rows.append((parsed_date.isoformat(), two_year, ten_year))
    rows.sort(key=lambda item: item[0])
    if not rows:
        return []
    current = rows[-1]
    previous = rows[-2] if len(rows) > 1 else None
    result = []
    for key, label, index in (("us10y", "美国10年期收益率", 2), ("us2y", "美国2年期收益率", 1)):
        value = current[index]
        previous_value = previous[index] if previous else None
        result.append({
            "key": key,
            "label": label,
            "value": value,
            "change_pct": round(value - previous_value, 3) if previous_value is not None else None,
            "change_kind": "change_points",
            "currency": "%",
            "source_time": current[0],
            "available": True,
            **_official_series_meta(current[0], "美国财政部日收益率"),
        })
    return result


def parse_cboe_vix_csv(text: str) -> list[dict]:
    """Parse Cboe's public VIX history as a FRED fallback."""
    rows: list[tuple[str, float]] = []
    for item in csv.DictReader(StringIO(text)):
        stamp = str(item.get("DATE") or "")
        try:
            parsed_date = datetime.strptime(stamp, "%m/%d/%Y").date()
        except (TypeError, ValueError):
            continue
        close = _float(item.get("CLOSE"))
        if close is not None:
            rows.append((parsed_date.isoformat(), close))
    rows.sort(key=lambda item: item[0])
    if not rows:
        return []
    stamp, current = rows[-1]
    previous = rows[-2][1] if len(rows) > 1 else None
    return [{
        "key": "vix",
        "label": "VIX波动率",
        "value": current,
        "change_pct": round((current / previous - 1) * 100, 2) if previous not in (None, 0) else None,
        "change_kind": "relative_pct",
        "currency": "index",
        "source_time": stamp,
        "available": True,
        **_official_series_meta(stamp, "Cboe公开VIX历史"),
    }]


def parse_mofcom_credit_payload(payload: object) -> dict:
    """Build a transparent credit-pulse proxy from PBOC social-finance flow.

    Credit impulse is represented as the year-over-year change in the
    12-month rolling total of monthly social-financing increments. This is a
    flow-based proxy, not a claim that the raw monthly increment is credit
    impulse itself.
    """
    rows = payload if isinstance(payload, list) else []
    parsed = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        stamp = str(item.get("date") or "")
        total = _float(item.get("tiosfs"))
        if len(stamp) != 6 or total is None:
            continue
        parsed.append({"date": f"{stamp[:4]}-{stamp[4:]}-01", "total": total})
    parsed.sort(key=lambda item: item["date"])
    if len(parsed) < 13:
        return {}
    current = sum(item["total"] for item in parsed[-12:])
    previous = sum(item["total"] for item in parsed[-13:-1])
    if previous == 0:
        return {}
    pulse_pct = round((current / previous - 1) * 100, 2)
    latest = parsed[-1]
    return {
        "key": "credit_pulse",
        "label": "信用脉冲（社融12个月滚动变化）",
        "value": pulse_pct,
        "pulse_pct": pulse_pct,
        "latest_monthly_increment": latest["total"],
        "rolling_12m_increment": current,
        "previous_rolling_12m_increment": previous,
        "currency": "percent",
        "source_time": latest["date"],
        "available": True,
        "method": "社融增量12个月滚动总额同比变化；原始数据来源中国人民银行，商务部数据中心发布。",
        **_official_series_meta(latest["date"], "商务部数据中心/中国人民银行"),
    }


def _parse_eastmoney_indicator(payload: object, *, key: str, label: str, value_field: str, yoy_field: str, mom_field: str | None, source: str) -> dict:
    rows = (payload.get("result") or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    usable = [item for item in rows if isinstance(item, dict) and _float(item.get(value_field)) is not None]
    if not usable:
        return {}
    item = usable[0]
    stamp = str(item.get("REPORT_DATE") or "")[:10]
    return {
        "key": key,
        "label": label,
        "value": _float(item.get(value_field)),
        "yoy_pct": _float(item.get(yoy_field)),
        "mom_pct": _float(item.get(mom_field)) if mom_field else None,
        "source_time": stamp,
        "available": bool(stamp),
        **_official_series_meta(stamp, source),
    } if stamp else {}


class MacroDashboardService:
    _CACHE_KEY = "macro_dashboard_v1"

    @classmethod
    async def _load_cache(cls) -> dict:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, cls._CACHE_KEY)
            return dict(row.payload) if row and isinstance(row.payload, dict) else {}
        except Exception:
            return {}

    @classmethod
    async def _save_cache(cls, payload: dict) -> None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, cls._CACHE_KEY)
                if row is None:
                    session.add(MarketDataCache(key=cls._CACHE_KEY, payload=payload))
                else:
                    row.payload = payload
                await session.commit()
        except Exception:
            pass

    @staticmethod
    def _timeout() -> float:
        try:
            return min(max(float(settings.macro_news_timeout), 2.0), 12.0)
        except (TypeError, ValueError):
            return 8.0

    async def _global_markets(self) -> list[dict]:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
            "Accept": "*/*",
        }
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            async def fetch_sina() -> list[dict]:
                response = await client.get(SINA_QUOTES_URL, headers=headers)
                response.raise_for_status()
                return parse_sina_market_payload(response.content.decode("gb18030", errors="replace"))

            async def fetch_eastmoney() -> list[dict]:
                response = await client.get(EASTMONEY_GLOBAL_URL, headers={"User-Agent": "Mozilla/5.0"})
                response.raise_for_status()
                return parse_eastmoney_market_payload(response.json())

            sina_result, eastmoney_result = await asyncio.gather(fetch_sina(), fetch_eastmoney(), return_exceptions=True)
            sina = [] if isinstance(sina_result, Exception) else sina_result
            eastmoney = [] if isinstance(eastmoney_result, Exception) else eastmoney_result
        by_key = {item.get("key"): item for item in sina if item.get("available")}
        fallback_by_key = {item.get("key"): item for item in eastmoney if item.get("available")}
        for key, item in fallback_by_key.items():
            by_key.setdefault(key, item)
        return [by_key[key] for key in ("sp500", "dow", "nasdaq", "gold", "oil", "dxy") if key in by_key]

    async def _supplemental_indicators(self) -> dict:
        """Fetch macro series that are separate from the intraday quote feed.

        FRED and the domestic macro feeds publish daily or monthly
        observations. They stay labelled as latest-published data instead of
        being presented as intraday real-time quotes.
        """
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        }

        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            async def fetch_fred() -> list[dict]:
                response = await client.get(FRED_MACRO_URL, headers=headers)
                response.raise_for_status()
                return parse_fred_macro_zip(response.content)

            async def fetch_treasury() -> list[dict]:
                year = shanghai_now().year
                response = await client.get(
                    TREASURY_YIELD_URL.format(year=year),
                    headers={**headers, "Referer": "https://home.treasury.gov/"},
                )
                response.raise_for_status()
                return parse_treasury_yield_csv(response.text)

            async def fetch_cboe() -> list[dict]:
                response = await client.get(
                    CBOE_VIX_URL,
                    headers={**headers, "Referer": "https://www.cboe.com/"},
                )
                response.raise_for_status()
                return parse_cboe_vix_csv(response.text)

            async def fetch_credit() -> dict:
                response = await client.post(
                    MOFCOM_SOCIAL_FINANCE_URL,
                    headers={**headers, "Content-Type": "application/json"},
                    json={},
                )
                response.raise_for_status()
                return parse_mofcom_credit_payload(response.json())

            async def fetch_eastmoney(report_name: str) -> dict:
                response = await client.get(
                    EASTMONEY_DATACENTER_URL,
                    params={
                        "reportName": report_name,
                        "columns": "ALL",
                        "pageNumber": "1",
                        "pageSize": "24",
                        "sortTypes": "-1",
                        "sortColumns": "REPORT_DATE",
                        "source": "WEB",
                        "client": "WEB",
                    },
                    headers={**headers, "Referer": "https://data.eastmoney.com/"},
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("success"):
                    raise ValueError(str(payload.get("message") or "东方财富数据中心未返回成功状态"))
                return payload

            fred_result, treasury_result, cboe_result, credit_result, price_result, capex_result = await asyncio.gather(
                fetch_fred(),
                fetch_treasury(),
                fetch_cboe(),
                fetch_credit(),
                fetch_eastmoney("RPT_ECONOMY_GOODS_INDEX"),
                fetch_eastmoney("RPT_ECONOMY_ASSET_INVEST"),
                return_exceptions=True,
            )

        fred = [] if isinstance(fred_result, Exception) else fred_result
        treasury = [] if isinstance(treasury_result, Exception) else treasury_result
        cboe = [] if isinstance(cboe_result, Exception) else cboe_result
        # Prefer the consolidated official FRED response, then fill only the
        # missing series from independent public sources. Never overwrite a
        # newer observed value with a fallback response.
        macro_global = {item.get("key"): item for item in fred if item.get("key")}
        for item in [*treasury, *cboe]:
            if item.get("key") not in macro_global:
                macro_global[item["key"]] = item
        credit = {} if isinstance(credit_result, Exception) else credit_result
        price = {} if isinstance(price_result, Exception) else _parse_eastmoney_indicator(
            price_result,
            key="industry_price",
            label="企业商品价格指数",
            value_field="BASE",
            yoy_field="BASE_SAME",
            mom_field="BASE_SEQUENTIAL",
            source="东方财富企业商品价格指数",
        )
        capex = {} if isinstance(capex_result, Exception) else _parse_eastmoney_indicator(
            capex_result,
            key="capex",
            label="城镇固定资产投资（宏观资本开支代理）",
            value_field="BASE",
            yoy_field="BASE_SAME",
            mom_field="BASE_SEQUENTIAL",
            source="东方财富/国家统计口径城镇固定资产投资",
        )
        if credit:
            credit["note"] = "这是社融增量滚动变化的信用脉冲代理，不等同于单月社融，也不代表上市公司真实信用投放。"
        if price:
            price["note"] = "价格端信号只表示企业商品价格变化，不等同于上市公司盈利已经确认。"
        if capex:
            capex["note"] = "这是国家统计口径固定资产投资的宏观代理，不等同于上市公司财报中的真实CAPEX。"

        macro_indicators = {
            "credit_pulse": credit,
            "industry_price": price,
            "capex": capex,
        }
        return {
            "global_markets": list(macro_global.values()),
            "macro_indicators": macro_indicators,
            "source_status": {
                "FRED公开序列": "available" if fred else "fallback" if (treasury or cboe) else "unavailable",
                "美国财政部日收益率": "available" if treasury else "unavailable",
                "Cboe公开VIX历史": "available" if cboe else "unavailable",
                "商务部数据中心/中国人民银行": "available" if credit else "unavailable",
                "东方财富企业商品价格指数": "available" if price else "unavailable",
                "东方财富/国家统计口径城镇固定资产投资": "available" if capex else "unavailable",
            },
        }

    async def _economic_calendar(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            response = await client.get(ECONOMIC_CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            return []
        now = shanghai_now()
        end = now + timedelta(days=14)
        country_labels = {"USD": "美国", "CNY": "中国", "All": "全球"}
        result = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            country = str(item.get("country") or "")
            impact = str(item.get("impact") or "")
            if country not in country_labels or impact not in {"High", "Medium"}:
                continue
            try:
                event_at = datetime.fromisoformat(str(item.get("date") or "")).astimezone(SHANGHAI_TZ)
            except (TypeError, ValueError):
                continue
            if event_at < now or event_at > end:
                continue
            result.append({
                "title": str(item.get("title") or ""),
                "country": country_labels[country],
                "country_code": country,
                "impact": "高" if impact == "High" else "中",
                "event_at": event_at.isoformat(),
                "forecast": str(item.get("forecast") or ""),
                "previous": str(item.get("previous") or ""),
                "source": "Forex Factory calendar feed",
            })
        return sorted(result, key=lambda item: item["event_at"])

    @staticmethod
    def _a_share_outlook(global_markets: list[dict], domestic: dict, policy: dict) -> dict:
        """Translate verified macro inputs into a plain-language A-share bias."""
        market_by_key = {item.get("key"): item for item in global_markets}
        score = 0.0
        evidence_count = 0
        drivers: list[dict] = []
        favored: list[str] = []
        pressured: list[str] = []

        def add(factor: str, value: float, explanation: str, affected: str) -> None:
            nonlocal score, evidence_count
            score += value
            evidence_count += 1
            drivers.append({
                "factor": factor,
                "direction": "positive" if value > 0 else "negative" if value < 0 else "neutral",
                "explanation": explanation,
                "affected": affected,
                "score": round(value, 1),
            })

        sp500_change = _float((market_by_key.get("sp500") or {}).get("change_pct"))
        if sp500_change is not None:
            contribution = max(-16.0, min(16.0, sp500_change * 8))
            add(
                "隔夜美股",
                contribution,
                f"标普500 {sp500_change:+.2f}%，{'改善' if contribution > 0 else '压低' if contribution < 0 else '未明显改变'}全球风险偏好。",
                "成长、科技与外资偏好板块",
            )

        dxy_change = _float((market_by_key.get("dxy") or {}).get("change_pct"))
        if dxy_change is not None:
            contribution = max(-12.0, min(12.0, -dxy_change * 12))
            add(
                "美元指数",
                contribution,
                f"美元指数 {dxy_change:+.2f}%，{'人民币资产外部压力减轻' if contribution > 0 else '可能抑制外资风险偏好' if contribution < 0 else '影响中性'}。",
                "北向偏好、港股映射与高估值成长",
            )

        north = (domestic.get("northbound") or {}).get("net_inflow")
        north_value = _float(north)
        if north_value is not None:
            north_yi = north_value / 1e8
            contribution = max(-18.0, min(18.0, north_yi / 3))
            add(
                "北向资金",
                contribution,
                f"最近可核验净流入 {north_yi:+.2f} 亿元，{'增添' if contribution > 0 else '削弱' if contribution < 0 else '未改变'}增量资金支持。",
                "大盘权重、消费、金融与核心资产",
            )

        sh_change = _float((domestic.get("turnover") or {}).get("sh_change_pct"))
        if sh_change is not None:
            contribution = max(-18.0, min(18.0, sh_change * 9))
            add(
                "A股当下走势",
                contribution,
                f"上证指数最近变化 {sh_change:+.2f}%，市场自身趋势{'偏强' if contribution > 0 else '偏弱' if contribution < 0 else '震荡'}。",
                "全市场风险偏好",
            )

        oil_change = _float((market_by_key.get("oil") or {}).get("change_pct"))
        if oil_change is not None:
            if oil_change >= 1.5:
                add("国际原油", -4.0, f"原油上涨 {oil_change:+.2f}%，输入成本与通胀预期升温。", "航空、物流、化工下游")
                favored.append("油气开采")
                pressured.extend(["航空", "物流", "化工下游"])
            elif oil_change <= -1.5:
                add("国际原油", 3.0, f"原油下跌 {oil_change:+.2f}%，部分中下游成本压力缓和。", "航空、物流与制造中下游")
                favored.extend(["航空", "物流", "制造中下游"])

        gold_change = _float((market_by_key.get("gold") or {}).get("change_pct"))
        if gold_change is not None and abs(gold_change) >= 1.0:
            contribution = -3.0 if gold_change > 0 else 1.0
            add("黄金", contribution, f"黄金 {gold_change:+.2f}%，{'避险需求升温' if gold_change > 0 else '避险交易降温'}。", "黄金、有色与高波动成长")
            if gold_change > 0:
                favored.append("黄金")

        policy_adjustment = _float(policy.get("macro_adjustment")) if isinstance(policy, dict) else None
        if policy_adjustment is not None and policy.get("available"):
            contribution = max(-10.0, min(10.0, policy_adjustment))
            add(
                "国内政策",
                contribution,
                "已核验政策新闻的行业匹配结果偏支持。" if contribution > 0 else "已核验政策新闻暂未形成明显增量支持。",
                "政策明确支持的行业",
            )

        score = round(max(-100.0, min(100.0, score)), 1)
        if score >= 22:
            stance, label = "bullish", "偏多"
        elif score <= -22:
            stance, label = "cautious", "偏谨慎"
        else:
            stance, label = "neutral", "震荡中性"
        confidence = round(min(92.0, 28.0 + evidence_count * 10.0), 1) if evidence_count else 0.0
        ranked_drivers = sorted(drivers, key=lambda item: abs(item["score"]), reverse=True)
        key_text = "、".join(item["factor"] for item in ranked_drivers[:3]) or "可核验数据不足"
        if stance == "bullish":
            action = "风险偏好有支撑，但仍需等待成交额与板块资金同步确认。"
        elif stance == "cautious":
            action = "控制追高与总仓位，优先观察抗跌、低估值及政策有支撑方向。"
        else:
            action = "指数更可能维持结构性震荡，宜看板块资金而不是只看大盘涨跌。"
        return {
            "stance": stance,
            "label": label,
            "score": score,
            "confidence": confidence,
            "headline": f"A股综合方向：{label}。主要由{key_text}共同决定。",
            "summary": action,
            "drivers": ranked_drivers,
            "favored_sectors": list(dict.fromkeys(favored))[:6],
            "pressured_sectors": list(dict.fromkeys(pressured))[:6],
            "data_points": evidence_count,
            "method": "基于可核验海外市场、美元、商品、北向资金、A股走势与政策信号的规则加权；不是收益预测。",
        }

    async def dashboard(self) -> dict:
        cached = await self._load_cache()
        global_result, supplemental_result, calendar_result, north_result, turnover_result, policy_result = await asyncio.gather(
            self._global_markets(),
            self._supplemental_indicators(),
            self._economic_calendar(),
            collector.fetch_north_bound_daily(days=10),
            collector.fetch_market_turnover(),
            macro_policy_news_collector.get_context(),
            return_exceptions=True,
        )
        global_markets = [] if isinstance(global_result, Exception) else global_result
        supplemental = supplemental_result if isinstance(supplemental_result, dict) else {}
        supplemental_global = list(supplemental.get("global_markets") or []) if isinstance(supplemental, dict) else []
        supplemental_indicators = dict(supplemental.get("macro_indicators") or {}) if isinstance(supplemental, dict) else {}
        calendar = [] if isinstance(calendar_result, Exception) else calendar_result
        north = [] if isinstance(north_result, Exception) else north_result
        turnover = {} if isinstance(turnover_result, Exception) else turnover_result
        policy = macro_policy_news_collector.empty_context() if isinstance(policy_result, Exception) else policy_result
        if not isinstance(policy, dict):
            policy = macro_policy_news_collector.empty_context()
        cache_used = False
        global_from_cache = False
        calendar_from_cache = False
        policy_from_cache = False
        cached_global = {item.get("key"): item for item in (cached.get("global_markets") or []) if isinstance(item, dict)}
        current_global = {
            item.get("key"): item
            for item in [*global_markets, *supplemental_global]
            if isinstance(item, dict) and item.get("key")
        }
        for key, item in cached_global.items():
            if key and key not in current_global:
                current_global[key] = {
                    **item,
                    "cache_used": True,
                    "is_realtime": False,
                    "data_age_minutes": None,
                    "source_status": "cache",
                }
                global_from_cache = True
        global_markets = [current_global[key] for key in ("sp500", "dow", "nasdaq", "gold", "oil", "dxy", "us10y", "us2y", "vix") if key in current_global]
        if global_from_cache:
            cache_used = True
        if not calendar and cached.get("economic_calendar"):
            calendar = list(cached["economic_calendar"])
            cache_used = True
            calendar_from_cache = True
        if not policy.get("available") and cached.get("policy", {}).get("available"):
            policy = {**cached["policy"], "source_status": {"宏观政策快照": "cache"}}
            cache_used = True
            policy_from_cache = True

        cached_indicators = {
            key: value for key, value in (cached.get("macro_indicators") or {}).items()
            if isinstance(value, dict) and value
        }
        indicators_from_cache = False
        for key, item in cached_indicators.items():
            if not supplemental_indicators.get(key):
                supplemental_indicators[key] = {
                    **item,
                    "cache_used": True,
                    "is_realtime": False,
                    "data_age_minutes": None,
                    "source_status": "cache",
                }
                indicators_from_cache = True
        if indicators_from_cache:
            cache_used = True

        north_available = [item for item in north if item.get("net_inflow") is not None]
        latest_north = north_available[-1] if north_available else (north[-1] if north else None)
        consecutive_inflow_days = 0
        for item in reversed(north_available):
            if (item.get("net_inflow") or 0) > 0:
                consecutive_inflow_days += 1
            else:
                break
        domestic = {
            "northbound": {
                "available": latest_north is not None and latest_north.get("net_inflow") is not None,
                "date": latest_north.get("date") if latest_north else None,
                "net_inflow": latest_north.get("net_inflow") if latest_north else None,
                "consecutive_inflow_days": consecutive_inflow_days,
                "source": latest_north.get("source", "eastmoney") if latest_north else "eastmoney",
            },
            "turnover": {
                "available": bool(turnover),
                "date": turnover.get("data_date"),
                "sh_amount": turnover.get("sh_amount"),
                "sh_index": turnover.get("sh_index"),
                "sh_change_pct": turnover.get("sh_change_pct"),
                "source": "东方财富",
            },
            "margin_balance": {
                "available": False,
                "value": None,
                "message": "当前数据源未提供可核验的全市场融资余额，未用示例值替代。",
            },
        }
        cached_domestic = cached.get("domestic_liquidity") or {}
        north_from_cache = False
        turnover_from_cache = False
        if not domestic["northbound"]["available"] and cached_domestic.get("northbound", {}).get("date"):
            domestic["northbound"] = dict(cached_domestic["northbound"])
            cache_used = True
            north_from_cache = True
        if not domestic["turnover"]["available"] and cached_domestic.get("turnover", {}).get("date"):
            domestic["turnover"] = dict(cached_domestic["turnover"])
            cache_used = True
            turnover_from_cache = True

        market_by_key = {item["key"]: item for item in global_markets}
        sp500 = market_by_key.get("sp500") or {}
        gold = market_by_key.get("gold") or {}
        oil = market_by_key.get("oil") or {}
        dxy = market_by_key.get("dxy") or {}
        north_view = domestic["northbound"]
        premarket_questions = [
            {
                "id": "overseas",
                "question": "隔夜海外市场风险偏好如何？",
                "answer": f"标普500 {sp500.get('change_pct'):+.2f}%" if sp500.get("change_pct") is not None else "海外指数源暂不可用",
                "status": "positive" if (sp500.get("change_pct") or 0) > 0.3 else "negative" if (sp500.get("change_pct") or 0) < -0.3 else "neutral",
            },
            {
                "id": "commodities",
                "question": "黄金和原油是否释放通胀或避险信号？",
                "answer": (
                    f"黄金 {gold.get('change_pct'):+.2f}% · 原油 {oil.get('change_pct'):+.2f}%"
                    if gold.get("change_pct") is not None and oil.get("change_pct") is not None else "商品行情源暂不完整"
                ),
                "status": "neutral",
            },
            {
                "id": "dollar",
                "question": "美元强弱是否影响风险资产？",
                "answer": f"美元指数 {dxy.get('value'):.2f} ({dxy.get('change_pct'):+.2f}%)" if dxy.get("value") is not None and dxy.get("change_pct") is not None else "美元指数暂不可用",
                "status": "negative" if (dxy.get("change_pct") or 0) > 0.4 else "neutral",
            },
            {
                "id": "liquidity",
                "question": "国内资金面是否支持风险偏好？",
                "answer": (
                    f"北向净流入 {north_view['net_inflow'] / 1e8:+.2f}亿元，连续流入 {north_view.get('consecutive_inflow_days', 0)} 日"
                    if north_view.get("net_inflow") is not None else "北向净流入字段当前不可核验"
                ),
                "status": "positive" if (north_view.get("net_inflow") or 0) > 0 else "neutral",
            },
            {
                "id": "calendar",
                "question": "未来两周有哪些高影响事件？",
                "answer": f"{len([item for item in calendar if item['impact'] == '高'])} 项高影响事件" if calendar else "经济日历暂未返回未来事件",
                "status": "warning" if any(item["impact"] == "高" for item in calendar[:3]) else "neutral",
            },
        ]
        has_sina_live = any(item.get("source") == "新浪财经" and item.get("available") and not item.get("cache_used") for item in global_markets)
        has_eastmoney_global = any(item.get("source") == "东方财富全球行情" and item.get("available") for item in global_markets)
        source_status = {
            "新浪财经": "available" if has_sina_live else "cache" if global_from_cache else "unavailable",
            "东方财富全球行情": "available" if has_eastmoney_global else "unavailable",
            "全球行情缓存": "cache" if global_from_cache else "unused",
            "经济日历": "cache" if calendar_from_cache else "available" if calendar else "unavailable",
            "东方财富资金": "cache" if north_from_cache or turnover_from_cache else "available" if north or turnover else "unavailable",
            **(policy.get("source_status") or {}),
        }
        source_status.update(supplemental.get("source_status") or {})
        if indicators_from_cache:
            for item in supplemental_indicators.values():
                if item.get("cache_used"):
                    source_status[item.get("source", "宏观指标")] = "cache"
        if policy_from_cache:
            source_status["宏观政策快照"] = "cache"
        updated_at = shanghai_now().isoformat()
        output = {
            "updated_at": updated_at,
            "global_markets": global_markets,
            "macro_indicators": supplemental_indicators,
            "economic_calendar": calendar,
            "domestic_liquidity": domestic,
            "policy": {
                "available": bool(policy.get("available")),
                "summary": policy.get("summary"),
                "international_items": policy.get("international_items") or [],
                "policy_items": policy.get("policy_items") or [],
            },
            "premarket_questions": premarket_questions,
            "a_share_outlook": self._a_share_outlook(global_markets, domestic, policy),
            "source_status": source_status,
            "cache_used": cache_used,
            "snapshot_updated_at": (
                cached.get("snapshot_updated_at") or cached.get("updated_at")
                if cache_used else updated_at
            ),
            "disclaimer": "不同市场交易时段不同；页面按各源时间戳展示，不把隔夜收盘标记为A股盘中实时。",
        }
        await self._save_cache(output)
        return output


macro_dashboard_service = MacroDashboardService()
