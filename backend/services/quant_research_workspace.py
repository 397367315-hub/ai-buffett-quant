"""Auditable research workspace for the quantitative module.

This module is deliberately a research registry and orchestration layer, not
an AI code runner.  Factor definitions and strategy DSLs are allowlisted,
experiments are reproducible from a locked parameter payload, and missing
point-in-time data is reported as a blocker instead of being filled in.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import time
from datetime import date, datetime
from typing import Any

from sqlalchemy import case, func, select

from database import async_session
from models import (
    FinancialPITSnapshot,
    MarketDataCache,
    MarketSentimentDaily,
    SecurityMaster,
    SecurityStatusEvent,
    StockAuctionSnapshot,
    StockDailyBar,
    StockMinuteBar,
    StockUniverseSnapshot,
)
from quant.jobs import create_job, get_job, latest_running_job, spawn, update_job
from services.quant_research import quant_research_engine


STATUS_LABELS = {
    "DRAFT": "草稿",
    "AUDITED": "数据审计通过",
    "VALIDATED": "单因子验证通过",
    "COMPOSABLE": "可组合",
    "DECAYING": "效果衰减",
    "REJECTED": "淘汰",
    "RETIRED": "停用",
}


def _factor(
    factor_id: str,
    name: str,
    category: str,
    formula: str,
    direction: str,
    required_fields: list[str],
    available_at: str,
    source: str,
    logic: str,
    *,
    frequency: str = "daily",
    status: str = "DRAFT",
    blocker: str | None = None,
) -> dict[str, Any]:
    return {
        "id": factor_id,
        "name": name,
        "category": category,
        "version": "1.0.0",
        "formula": formula,
        "direction": direction,
        "frequency": frequency,
        "required_fields": required_fields,
        "available_at": available_at,
        "source": source,
        "economic_logic": logic,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "blocker": blocker,
        "registered": True,
    }


# The first catalog is intentionally metadata-first.  Registration does not
# imply that a factor has passed IC/IR gates; the research report makes that
# distinction explicit for every item.
FACTOR_CATALOG: list[dict[str, Any]] = [
    _factor("momentum_1d", "1日动量", "动量/趋势", "close(T) / close(T-1) - 1", "positive", ["close_price"], "T close", "A股日线缓存", "短期价格延续。"),
    _factor("momentum_3d", "3日动量", "动量/趋势", "close(T) / close(T-3) - 1", "positive", ["close_price"], "T close", "A股日线缓存", "短周期趋势延续。"),
    _factor("momentum_5d", "5日动量", "动量/趋势", "close(T) / close(T-5) - 1", "positive", ["close_price"], "T close", "A股日线缓存", "周频价格强度。"),
    _factor("momentum_10d", "10日动量", "动量/趋势", "close(T) / close(T-10) - 1", "positive", ["close_price"], "T close", "A股日线缓存", "中短期趋势延续。"),
    _factor("momentum_20d", "20日动量", "动量/趋势", "close(T) / close(T-20) - 1", "positive", ["close_price"], "T close", "A股日线缓存", "月度趋势强度；已有点时日线基线回测。", status="AUDITED"),
    _factor("relative_strength_index_5d", "5日相对强弱", "动量/趋势", "rank(momentum_5d) within cross-section", "positive", ["close_price"], "T close", "A股日线缓存", "识别横截面相对强势。"),
    _factor("ma_alignment_score", "均线排列分", "动量/趋势", "sum(close > MA5, MA10, MA20, MA60)", "positive", ["close_price"], "T close", "A股日线缓存", "趋势结构越完整越稳定。"),
    _factor("price_to_ma20", "价格/MA20", "动量/趋势", "close / MA20 - 1", "positive", ["close_price"], "T close", "A股日线缓存", "价格相对中期趋势的位置。"),
    _factor("price_to_ma60", "价格/MA60", "动量/趋势", "close / MA60 - 1", "positive", ["close_price"], "T close", "A股日线缓存", "价格相对长期趋势的位置。"),
    _factor("high_distance_20d", "距20日高点", "动量/趋势", "close / rolling_max(high,20) - 1", "positive", ["high_price", "close_price"], "T close", "A股日线缓存", "接近高点代表趋势延续，远离则可能是反转或弱势。"),
    _factor("volume_ratio_5d", "5日量比", "量价/活跃度", "volume(T) / mean(volume,T-5:T-1)", "positive", ["volume"], "T close", "A股日线缓存", "量能确认价格变化。"),
    _factor("volume_ratio_20d", "20日量比", "量价/活跃度", "volume(T) / mean(volume,T-20:T-1)", "positive", ["volume"], "T close", "A股日线缓存", "中期资金参与度；可由现有日线计算。", status="AUDITED"),
    _factor("volume_slope_3d", "3日成交量斜率", "量价/活跃度", "slope(volume,T-2:T)", "positive", ["volume"], "T close", "A股日线缓存", "量能加速或衰减。"),
    _factor("volume_step_3d", "3日台阶放量", "量价/活跃度", "volume(T)>volume(T-1)>volume(T-2)", "positive", ["volume"], "T close", "A股日线缓存", "连续放量的结构确认。"),
    _factor("price_volume_corr_20d", "20日价量相关", "量价/活跃度", "corr(change_pct, volume,20)", "positive", ["change_pct", "volume"], "T close", "A股日线缓存", "价格方向与资金参与是否一致。"),
    _factor("turnover_rate", "换手率", "量价/活跃度", "turnover(T)", "contextual", ["turnover"], "T close", "A股日线缓存", "交易活跃度与容量约束。"),
    _factor("turnover_change_5d", "5日换手变化", "量价/活跃度", "turnover(T) / mean(turnover,5) - 1", "contextual", ["turnover"], "T close", "A股日线缓存", "活跃度变化。"),
    _factor("close_location", "收盘位置", "量价/活跃度", "(close-low)/(high-low)", "positive", ["high_price", "low_price", "close_price"], "T close", "A股日线缓存", "收盘靠近区间高位表示承接较强。"),
    _factor("upper_shadow_ratio", "上影线比例", "量价/活跃度", "(high-max(open,close))/close", "negative", ["open_price", "high_price", "close_price"], "T close", "A股日线缓存", "上方抛压代理。"),
    _factor("lower_shadow_ratio", "下影线比例", "量价/活跃度", "(min(open,close)-low)/close", "contextual", ["open_price", "low_price", "close_price"], "T close", "A股日线缓存", "下方承接代理。"),
    _factor("atr_20", "20日ATR", "波动/反转", "mean(true_range,20)", "negative", ["high_price", "low_price", "close_price"], "T close", "A股日线缓存", "用于止损和仓位而非直接预测。"),
    _factor("realized_volatility_20d", "20日实现波动", "波动/反转", "std(log_return,20)*sqrt(252)", "negative", ["close_price"], "T close", "A股日线缓存", "高波动环境下控制风险。"),
    _factor("downside_volatility_20d", "20日下行波动", "波动/反转", "std(min(log_return,0),20)", "negative", ["close_price"], "T close", "A股日线缓存", "区分下行风险与总波动。"),
    _factor("max_drawdown_20d", "20日最大回撤", "波动/反转", "max(1-close/rolling_max(close,20))", "negative", ["close_price"], "T close", "A股日线缓存", "识别近期风险和反转空间。"),
    _factor("rsi_6", "6日RSI", "波动/反转", "100 - 100/(1+avg_gain/avg_loss,6)", "contextual", ["close_price"], "T close", "A股日线缓存", "短期超买超卖状态。"),
    _factor("bollinger_position", "布林带位置", "波动/反转", "(close-MA20)/(2*std20)", "contextual", ["close_price"], "T close", "A股日线缓存", "价格相对波动区间的位置。"),
    _factor("index_above_ma20", "指数在MA20上方", "市场/板块", "index_close > index_MA20", "positive", ["index_close"], "T close", "指数日线缓存", "市场状态过滤；需完整指数点时缓存。"),
    _factor("index_above_ma60", "指数在MA60上方", "市场/板块", "index_close > index_MA60", "positive", ["index_close"], "T close", "指数日线缓存", "长期市场状态过滤。"),
    _factor("market_breadth", "市场涨跌比", "市场/板块", "up_count / max(down_count,1)", "positive", ["market_snapshot"], "T close", "全市场行情快照", "市场广度确认。"),
    _factor("market_amount_percentile", "市场成交额分位", "市场/板块", "percentile(market_amount,252)", "contextual", ["market_amount"], "T close", "大盘资金流缓存", "流动性环境状态。"),
    _factor("sector_strength_rank", "板块强度排名", "市场/板块", "rank(sector_return + breadth + flow)", "positive", ["sector_flow", "market_board"], "T close", "板块资金流缓存", "板块共振和相对强度。"),
    _factor("relative_strength_vs_sector", "相对板块强度", "市场/板块", "stock_return - sector_return", "positive", ["close_price", "sector_flow"], "T close", "日线与板块缓存", "剥离行业贝塔后的个股强弱。"),
    _factor("roe", "ROE", "基本面/估值", "net_profit / average_equity", "positive", ["financial_snapshot"], "disclosure", "财务披露缓存", "资本回报质量；需公告日点时字段。"),
    _factor("revenue_yoy", "营收增速", "基本面/估值", "revenue(TTM) / revenue(prior TTM) - 1", "positive", ["financial_snapshot"], "disclosure", "财务披露缓存", "业务增长质量。"),
    _factor("net_profit_yoy", "净利润增速", "基本面/估值", "net_profit(TTM) / net_profit(prior TTM) - 1", "positive", ["financial_snapshot"], "disclosure", "财务披露缓存", "盈利增长。"),
    _factor("ocf_to_net_profit", "经营现金流/净利润", "基本面/估值", "operating_cash_flow / net_profit", "positive", ["financial_snapshot"], "disclosure", "财务披露缓存", "盈利含金量。"),
    _factor("debt_ratio", "资产负债率", "基本面/估值", "total_liabilities / total_assets", "negative", ["financial_snapshot"], "disclosure", "财务披露缓存", "财务杠杆风险。"),
    _factor("pe_percentile", "PE历史分位", "基本面/估值", "percentile(pe, historical_window)", "negative", ["pe_ttm"], "T close", "估值与日线缓存", "估值相对位置。"),
    _factor("pb_percentile", "PB历史分位", "基本面/估值", "percentile(pb, historical_window)", "negative", ["pb"], "T close", "估值与日线缓存", "资产估值相对位置。"),
    _factor("peg", "PEG", "基本面/估值", "pe / max(net_profit_yoy, epsilon)", "negative", ["pe_ttm", "net_profit_yoy"], "disclosure", "财务披露缓存", "成长与估值的联合约束。"),
    _factor("auction_gap", "竞价高开幅度", "竞价/事件", "auction_price / previous_close - 1", "contextual", ["auction_price", "previous_close"], "09:25", "竞价分钟缓存", "隔夜情绪和开盘承接。", frequency="intraday", blocker="历史竞价分钟数据覆盖不足"),
    _factor("auction_volume_ratio", "竞价量比", "竞价/事件", "auction_volume / average_auction_volume", "positive", ["auction_volume"], "09:25", "竞价分钟缓存", "竞价成交确认。", frequency="intraday", blocker="历史竞价分钟数据覆盖不足"),
    _factor("auction_amount_ratio", "竞价金额比", "竞价/事件", "auction_amount / average_amount", "positive", ["auction_amount"], "09:25", "竞价分钟缓存", "资金参与强度确认。", frequency="intraday", blocker="历史竞价分钟数据覆盖不足"),
    _factor("sector_auction_breadth", "板块竞价广度", "竞价/事件", "auction_up_count / sector_member_count", "positive", ["auction_snapshot", "sector_membership"], "09:25", "竞价与历史股票池", "板块共振确认。", frequency="intraday", blocker="历史竞价分钟与历史股票池覆盖不足"),
    _factor("market_regime_score", "市场状态分", "战略状态机", "weighted(index_vs_MA20, index_vs_MA60, market_breadth, amount_trend)", "contextual", ["index_close", "market_breadth", "market_amount"], "T close", "指数日线与市场快照", "区分战略防御、相持和反攻阶段，不单用当日涨跌判牛熊。", blocker="真实市场宽度和成交额历史分位尚未完整"),
    _factor("sector_leadership_score", "板块主线强度", "战略状态机", "weighted(sector_relative_return_5d, sector_relative_return_20d, sector_breadth, sector_flow)", "positive", ["sector_flow", "sector_history", "sector_membership"], "T close", "板块资金流与历史成分股", "识别连续强于指数且宽度扩散的主线，避免只追单日热点。", blocker="历史板块成分与上涨比例覆盖不完整"),
    _factor("crowd_extreme_score", "大众情绪极值", "战略状态机", "weighted(limit_breadth, failed_breakout_rate, turnover_percentile, streak_height)", "contextual", ["limit_pool", "failed_breakout_rate", "market_turnover_percentile"], "T close", "涨跌停与情绪历史快照", "识别情绪从升温到极盛的过程；极值不等于立即反转。", blocker="炸板率、连板高度和历史分位尚未形成完整点时库"),
    _factor("supply_exhaustion_score", "抛压衰竭确认", "战略状态机", "weighted(pullback_days, volume_contraction, lower_shadow, distance_to_support)", "positive", ["close_price", "volume", "open_price", "high_price", "low_price"], "T close", "A股日线缓存", "将敌疲我打转为缩量回调、支撑距离和承接确认的可证伪条件。"),
    _factor("breakout_confirmation_score", "突破共振确认", "战略状态机", "weighted(breakout_20d, volume_ratio_5d, close_location, sector_breadth)", "positive", ["high_price", "close_price", "volume", "sector_membership"], "T close", "日线与板块快照", "将敌退我追限定为量价与板块共振后的条件跟随，不追孤立突破。", blocker="历史板块成分宽度不完整"),
]

FACTOR_BY_ID = {item["id"]: item for item in FACTOR_CATALOG}


EXPERIMENT_CATALOG: list[dict[str, Any]] = [
    {
        "id": "weekly_momentum_baseline_v1",
        "name": "周频动量基线",
        "family": "weekly",
        "cadence": "持有5个交易日",
        "status": "READY_RESEARCH_ONLY",
        "supported": True,
        "factor_ids": ["momentum_20d"],
        "description": "固定20日绝对动量，T日收盘计算，T+1开盘买入，T+5收盘退出。",
        "blockers": ["历史股票池未完整覆盖退市与停牌标的", "财务点时字段不进入本实验"],
    },
    {
        "id": "overnight_auction_v1",
        "name": "竞价确认隔夜研究",
        "family": "overnight",
        "cadence": "隔夜/次日早盘",
        "status": "BLOCKED_DATA",
        "supported": False,
        "factor_ids": ["momentum_1d", "volume_ratio_5d", "auction_volume_ratio", "auction_gap"],
        "description": "尾盘信号与次日竞价确认的事件研究模板。",
        "blockers": ["历史竞价分钟数据不足", "历史股票池与涨跌停可成交状态未完整冻结"],
    },
    {
        "id": "garp_monthly_pit_v1",
        "name": "GARP月频点时研究",
        "family": "monthly",
        "cadence": "持有20个交易日",
        "status": "BLOCKED_DATA",
        "supported": False,
        "factor_ids": ["roe", "revenue_yoy", "peg", "pe_percentile", "debt_ratio"],
        "description": "基本面质量、成长和估值的行业中性研究模板。",
        "blockers": ["历史公告日财务快照尚未形成完整点时库", "历史行业与市值中性数据不足"],
    },
    {
        "id": "mao_five_struggles_v1",
        "name": "五个斗争智慧状态机研究",
        "family": "weekly",
        "cadence": "阶段过滤 + 2至5个交易日",
        "status": "BLOCKED_DATA",
        "supported": False,
        "factor_ids": [
            "market_regime_score", "sector_leadership_score", "crowd_extreme_score",
            "supply_exhaustion_score", "breakout_confirmation_score",
        ],
        "description": "验证敌疲我打、敌退我追、极盛转衰、游击优于硬扛与限额主线集中五类可证伪假设。",
        "hypotheses": ["H1 敌疲我打", "H2 敌退我追", "H3 极盛转衰", "H4 游击优于硬扛", "H5 主线集中但不重仓赌博"],
        "blockers": [
            "市场宽度、炸板率、连板高度和换手分位的历史点时库不完整",
            "历史板块成分与板块上涨比例不完整",
            "未满足样本外、交易成本压力测试和至少8周模拟盘门槛",
        ],
    },
]

LIFECYCLE = [
    {"id": "IDEA", "label": "研究想法", "description": "记录可证伪假设，不代表有效。"},
    {"id": "FACTOR_RESEARCH", "label": "因子研究", "description": "完成公式、数据来源和单因子检验。"},
    {"id": "BACKTEST", "label": "回测", "description": "使用锁定参数运行成本后回测。"},
    {"id": "VALIDATION", "label": "验证", "description": "检查参数稳健性、环境和压力测试。"},
    {"id": "OUT_OF_SAMPLE", "label": "样本外", "description": "只在策略锁定后读取的独立时段。"},
    {"id": "PAPER_TRADING", "label": "模拟盘", "description": "至少8周真实时间前向验证。"},
    {"id": "SMALL_CAPITAL", "label": "小资金人工批准", "description": "需要人工批准，不自动交易。"},
    {"id": "PRODUCTION", "label": "生产观察", "description": "仍需持续审计和可撤回。"},
]

HARD_GATES = [
    {"id": "period_count", "label": "有效交易期", "threshold": ">= 200（普通策略）"},
    {"id": "oos_return", "label": "样本外收益", "threshold": "> 0"},
    {"id": "oos_profit_factor", "label": "样本外Profit Factor", "threshold": ">= 1.20"},
    {"id": "max_drawdown", "label": "最大回撤", "threshold": "<= 20%"},
    {"id": "cost_stress", "label": "成本增加50%后仍盈利", "threshold": "> 0"},
    {"id": "parameter_robustness", "label": "参数上下浮动不崩溃", "threshold": "固定窗口敏感性不失效"},
    {"id": "pit_universe", "label": "点时股票池", "threshold": "必须可复核"},
]


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _status_summary(factors: list[dict[str, Any]] | None = None) -> dict[str, int]:
    summary = {key: 0 for key in STATUS_LABELS}
    for factor in factors or FACTOR_CATALOG:
        summary[factor["status"]] = summary.get(factor["status"], 0) + 1
    return summary


def _safe_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10] if value else None


class QuantResearchWorkspaceService:
    CACHE_KEY = "quant_research_workspace_v1"
    MANIFEST_CACHE_KEY = "quant_research_manifest_v2"
    MANIFEST_CACHE_SECONDS = 10 * 60

    def __init__(self) -> None:
        self._manifest_memory: tuple[float, dict[str, Any]] | None = None
        self._manifest_lock = asyncio.Lock()

    @staticmethod
    def _cache_age_seconds(value: Any) -> float | None:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.utcnow()
        return (now - parsed).total_seconds()

    async def _read_manifest_cache(self) -> dict[str, Any] | None:
        memory = self._manifest_memory
        if memory and time.monotonic() - memory[0] <= self.MANIFEST_CACHE_SECONDS:
            return copy.deepcopy(memory[1])
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, self.MANIFEST_CACHE_KEY)
        except Exception:
            return None
        payload = row.payload if row and isinstance(row.payload, dict) else {}
        manifest = payload.get("manifest")
        age = self._cache_age_seconds(payload.get("cached_at"))
        if not isinstance(manifest, dict) or age is None or not 0 <= age <= self.MANIFEST_CACHE_SECONDS:
            return None
        self._manifest_memory = (time.monotonic(), copy.deepcopy(manifest))
        return copy.deepcopy(manifest)

    async def _write_manifest_cache(self, manifest: dict[str, Any]) -> None:
        cached_at = datetime.utcnow().isoformat() + "Z"
        stored = copy.deepcopy({**manifest, "cache_generated_at": cached_at})
        self._manifest_memory = (time.monotonic(), stored)
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, self.MANIFEST_CACHE_KEY)
                payload = {"version": 2, "cached_at": cached_at, "manifest": stored}
                if row is None:
                    session.add(MarketDataCache(key=self.MANIFEST_CACHE_KEY, payload=payload))
                else:
                    row.payload = payload
                    row.updated_at = datetime.utcnow()
                await session.commit()
        except Exception:
            # The freshly built in-process value is still usable if persistence
            # has a temporary outage.
            return

    def invalidate_manifest_cache(self) -> None:
        self._manifest_memory = None

    async def dataset_manifest(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if not force_refresh:
            cached = await self._read_manifest_cache()
            if cached:
                cached["manifest_cache_used"] = True
                return cached
        async with self._manifest_lock:
            if not force_refresh:
                cached = await self._read_manifest_cache()
                if cached:
                    cached["manifest_cache_used"] = True
                    return cached
            manifest = await self._build_dataset_manifest()
            if manifest.get("available"):
                await self._write_manifest_cache(manifest)
            manifest["manifest_cache_used"] = False
            return manifest

    async def _build_dataset_manifest(self) -> dict[str, Any]:
        try:
            async with async_session() as session:
                daily = (await session.execute(select(
                    func.count(StockDailyBar.id),
                    func.count(func.distinct(StockDailyBar.stock_code)),
                    func.count(func.distinct(StockDailyBar.trade_date)),
                    func.min(StockDailyBar.trade_date),
                    func.max(StockDailyBar.trade_date),
                ))).one()
                sources = list((await session.execute(select(StockDailyBar.source).distinct().order_by(StockDailyBar.source))).scalars().all())
                security = (await session.execute(select(
                    func.count(SecurityMaster.stock_code),
                    func.sum(case((SecurityMaster.is_currently_listed.is_(True), 1), else_=0)),
                    func.sum(case((SecurityMaster.is_currently_listed.is_(False), 1), else_=0)),
                    func.sum(case((SecurityMaster.list_date.is_not(None), 1), else_=0)),
                    func.sum(case((
                        SecurityMaster.is_currently_listed.is_(False)
                        & SecurityMaster.delist_date.is_not(None), 1
                    ), else_=0)),
                ))).one()
                status_events = int((await session.execute(
                    select(func.count(SecurityStatusEvent.id))
                )).scalar_one() or 0)
                universe = (await session.execute(select(
                    func.count(StockUniverseSnapshot.id),
                    func.count(func.distinct(StockUniverseSnapshot.stock_code)),
                    func.count(func.distinct(StockUniverseSnapshot.trade_date)),
                    func.min(StockUniverseSnapshot.trade_date),
                    func.max(StockUniverseSnapshot.trade_date),
                    func.sum(case((StockUniverseSnapshot.industry.is_not(None), 1), else_=0)),
                    func.sum(case((StockUniverseSnapshot.market_cap.is_not(None), 1), else_=0)),
                ))).one()
                auction = (await session.execute(select(
                    func.count(StockAuctionSnapshot.id),
                    func.count(func.distinct(StockAuctionSnapshot.stock_code)),
                    func.count(func.distinct(StockAuctionSnapshot.trade_date)),
                    func.min(StockAuctionSnapshot.trade_date),
                    func.max(StockAuctionSnapshot.trade_date),
                    func.sum(case((StockAuctionSnapshot.auction_volume_ratio.is_not(None), 1), else_=0)),
                ))).one()
                financial = (await session.execute(select(
                    func.count(FinancialPITSnapshot.id),
                    func.count(func.distinct(FinancialPITSnapshot.stock_code)),
                    func.count(func.distinct(FinancialPITSnapshot.disclosed_at)),
                    func.min(FinancialPITSnapshot.disclosed_at),
                    func.max(FinancialPITSnapshot.disclosed_at),
                ))).one()
                minute = (await session.execute(select(
                    func.count(StockMinuteBar.id),
                    func.count(func.distinct(StockMinuteBar.stock_code)),
                    func.count(func.distinct(func.date(StockMinuteBar.bar_time))),
                    func.min(StockMinuteBar.bar_time),
                    func.max(StockMinuteBar.bar_time),
                ))).one()
                sentiment = (await session.execute(select(
                    func.count(MarketSentimentDaily.trade_date),
                    func.min(MarketSentimentDaily.trade_date),
                    func.max(MarketSentimentDaily.trade_date),
                    func.sum(case((
                        MarketSentimentDaily.market_amount.is_not(None)
                        & MarketSentimentDaily.up_count.is_not(None)
                        & MarketSentimentDaily.down_count.is_not(None)
                        & MarketSentimentDaily.failed_limit_rate.is_not(None)
                        & MarketSentimentDaily.max_streak_height.is_not(None), 1
                    ), else_=0)),
                    func.sum(case((MarketSentimentDaily.source == "daily_bar_derived", 1), else_=0)),
                    func.sum(case((
                        (MarketSentimentDaily.source != "daily_bar_derived")
                        & MarketSentimentDaily.failed_limit_rate.is_not(None)
                        & MarketSentimentDaily.max_streak_height.is_not(None), 1
                    ), else_=0)),
                ))).one()
        except Exception as exc:
            return {
                "available": False,
                "dataset_id": "ashare_daily_bars_unavailable",
                "error": f"数据清单读取失败：{type(exc).__name__}",
                "warnings": ["数据库不可用，研究任务不会生成结果。"],
            }
        count = int(daily[0] or 0)
        stock_count = int(daily[1] or 0)
        daily_sessions = int(daily[2] or 0)
        start_date = _safe_date(daily[3])
        end_date = _safe_date(daily[4])
        security_total = int(security[0] or 0)
        currently_listed = int(security[1] or 0)
        inactive_total = int(security[2] or 0)
        listing_dated = int(security[3] or 0)
        inactive_dated = int(security[4] or 0)

        def inventory(
            key: str,
            label: str,
            row: tuple,
            *,
            target_sessions: int,
            note: str,
            records_index: int = 0,
            stocks_index: int = 1,
            sessions_index: int = 2,
            start_index: int = 3,
            end_index: int = 4,
        ) -> dict[str, Any]:
            records = int(row[records_index] or 0)
            stocks = int(row[stocks_index] or 0)
            sessions = int(row[sessions_index] or 0)
            status = "missing" if not records else "ready" if sessions >= target_sessions else "collecting"
            return {
                "key": key,
                "label": label,
                "status": status,
                "record_count": records,
                "stock_count": stocks,
                "session_count": sessions,
                "target_sessions": target_sessions,
                "coverage_pct": round(min(sessions / max(target_sessions, 1), 1) * 100, 1),
                "date_range": [_safe_date(row[start_index]), _safe_date(row[end_index])],
                "note": note,
            }

        inventories = [
            {
                "key": "daily_bars",
                "label": "A股日线",
                "status": "ready" if count and daily_sessions >= 120 else "collecting" if count else "missing",
                "record_count": count,
                "stock_count": stock_count,
                "session_count": daily_sessions,
                "target_sessions": 120,
                "coverage_pct": round(min(daily_sessions / 120, 1) * 100, 1),
                "date_range": [start_date, end_date],
                "note": "按股票代码和交易日唯一的行情缓存。",
            },
            {
                "key": "observed_universe",
                "label": "历史日线观测股票池",
                "status": "derived_ready" if count and daily_sessions >= 120 else "derived_partial" if count else "missing",
                "record_count": count,
                "stock_count": stock_count,
                "session_count": daily_sessions,
                "target_sessions": 250,
                "coverage_pct": round(min(daily_sessions / 250, 1) * 100, 1),
                "date_range": [start_date, end_date],
                "note": "由当日存在的真实日线记录重建可观察成员；可做基线研究，但停牌成员仍可能遗漏。",
            },
            inventory(
                "universe", "点时股票池/行业/市值", universe,
                target_sessions=250,
                note="从首次采集日开始保留每日成员、行业和市值；采集前历史不会回填造数。",
            ),
            inventory(
                "auction", "09:25竞价快照", auction,
                target_sessions=60,
                note="交易日09:24-09:27采集竞价价、量、金额、量比和时间戳。",
            ),
            inventory(
                "financial_pit", "公告日财务PIT", financial,
                target_sessions=8,
                note="按真实公告日保存财务字段，同一报告期的后续修订单独留痕。",
            ),
            inventory(
                "minute_bars", "分时分钟线", minute,
                target_sessions=20,
                note="保留策略候选和题材核心的分钟线；不是全市场逐分钟历史。",
            ),
            {
                "key": "market_sentiment",
                "label": "市场宽度/炸板/连板/换手",
                "status": (
                    "ready" if int(sentiment[5] or 0) >= 120
                    else "derived_ready" if int(sentiment[3] or 0) >= 120
                    else "derived_partial" if int(sentiment[4] or 0)
                    else "collecting" if int(sentiment[0] or 0)
                    else "missing"
                ),
                "record_count": int(sentiment[0] or 0),
                "stock_count": 1,
                "session_count": int(sentiment[3] or 0),
                "target_sessions": 120,
                "coverage_pct": round(min(int(sentiment[3] or 0) / 120, 1) * 100, 1),
                "date_range": [_safe_date(sentiment[1]), _safe_date(sentiment[2])],
                "exact_sessions": int(sentiment[5] or 0),
                "derived_sessions": int(sentiment[4] or 0),
                "complete_sessions": int(sentiment[3] or 0),
                "observed_sessions": int(sentiment[0] or 0),
                "note": "宽度/成交额/换手来自真实日线聚合；历史涨停、炸板与连板若无源生事件池则明确标记为日线近似。",
            },
        ]
        for item in inventories:
            if item["key"] in {"universe", "auction"} and item["record_count"] == 0:
                item["status"] = "forward_only"
        inventory_by_key = {item["key"]: item for item in inventories}
        universe_sessions = inventory_by_key["universe"]["session_count"]
        auction_sessions = inventory_by_key["auction"]["session_count"]
        financial_dates = inventory_by_key["financial_pit"]["session_count"]
        sentiment_complete_sessions = inventory_by_key["market_sentiment"]["session_count"]
        universe_ready = bool(
            universe_sessions >= 250
            and inactive_total > 0
            and inactive_dated >= inactive_total * 0.8
        )
        manifest = {
            "dataset_id": f"ashare_daily_bars_{end_date or 'unknown'}_v1",
            "source": [str(item) for item in sources if item],
            "date_range": [start_date, end_date],
            "record_count": count,
            "stock_count": stock_count,
            "universe": {
                "status": "ready" if universe_ready else "partial" if security_total or universe_sessions else "missing",
                "historical_membership": universe_ready,
                "security_total": security_total,
                "currently_listed": currently_listed,
                "inactive_total": inactive_total,
                "inactive_dated": inactive_dated,
                "listing_dated": listing_dated,
                "status_events": status_events,
                "snapshot_sessions": universe_sessions,
                "observed_daily_sessions": daily_sessions,
                "observed_from_daily_bars": bool(count),
                "note": (
                    f"证券主表{security_total}只（历史非活跃/退市{inactive_total}只），"
                    f"每日点时股票池已积累{universe_sessions}个交易日；"
                    f"另有{daily_sessions}个交易日可按真实日线观测成员做有偏差基线研究。"
                ),
            },
            "point_in_time": {
                "status": "ready" if universe_ready and financial_dates >= 8 else "partial" if any((universe_sessions, auction_sessions, financial_dates)) else "missing",
                "observation_time": "trade_date",
                "available_time_field": "disclosed_at / observed_at / quote_at",
                "note": "新采集记录均带可用时间；系统上线前未采集的竞价和行业历史不会静默回填。",
            },
            "cache_used": True,
            "data_inventory": inventories,
            "warnings": [item for item in [
                "当前日线基线可计算，但数据集清单不能替代逐原始文件审计。",
                (
                    f"点时股票池从{inventory_by_key['universe']['date_range'][0] or '尚未开始'}起前向积累；"
                    "现有日线可重建观察成员，但停牌与历史行业信息仍有偏差。"
                    if not universe_ready else None
                ),
                (
                    f"竞价快照已积累{auction_sessions}/60个交易日；部署前09:25历史无法由日K真实还原，只能前向积累。"
                    if auction_sessions < 60 else None
                ),
                (
                    f"公告日财务PIT已覆盖{financial_dates}个披露日；完整跨周期研究仍需继续积累。"
                    if financial_dates < 8 else None
                ),
                (
                    f"市场情绪完整字段{sentiment_complete_sessions}日，其中日线近似{int(sentiment[4] or 0)}日；"
                    "近似口径仅供研究，不作为逐笔事件真值。"
                    if int(sentiment[4] or 0) else None
                ),
            ] if item],
            "researchability": {
                "daily_bar_baseline": "ready_with_bias_warning",
                "observed_universe_history": "ready_with_suspension_bias" if count else "missing",
                "auction_history": inventory_by_key["auction"]["status"],
                "pit_financials": inventory_by_key["financial_pit"]["status"],
                "sector_membership_history": inventory_by_key["universe"]["status"],
                "market_sentiment_history": inventory_by_key["market_sentiment"]["status"],
            },
        }
        manifest["generated_at"] = datetime.utcnow().isoformat() + "Z"
        manifest["manifest_hash"] = _canonical_hash(manifest)
        manifest["available"] = bool(count and start_date and end_date)
        return manifest

    @staticmethod
    def _experiment_view(item: dict[str, Any], manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        blockers = list(item.get("blockers") or [])
        inventory = {
            entry.get("key"): entry
            for entry in (manifest or {}).get("data_inventory") or []
            if isinstance(entry, dict)
        }
        if manifest and item.get("id") == "weekly_momentum_baseline_v1":
            observed = inventory.get("observed_universe") or {}
            blockers = [
                (
                    f"已有{observed.get('session_count', 0)}个交易日的日线观测成员，可运行基线；"
                    "退市、停牌和历史行业成员仍按偏差警告处理"
                ),
                "财务点时字段不进入本实验",
            ]
        elif manifest and item.get("id") == "overnight_auction_v1":
            auction = inventory.get("auction") or {}
            blockers = [
                f"真实09:25竞价已前向积累{auction.get('session_count', 0)}/60个交易日；部署前历史不能由日K还原",
                "历史股票池与涨跌停可成交状态尚未完整冻结",
            ]
        elif manifest and item.get("id") == "garp_monthly_pit_v1":
            financial = inventory.get("financial_pit") or {}
            blockers = [
                f"公告日财务PIT已覆盖{financial.get('session_count', 0)}个披露日，仍需跨财报周期积累",
                "历史行业与市值中性数据不足",
            ]
        elif manifest and item.get("id") == "mao_five_struggles_v1":
            sentiment = inventory.get("market_sentiment") or {}
            blockers = [
                (
                    f"市场宽度与情绪已有{sentiment.get('session_count', 0)}日；"
                    f"其中{sentiment.get('derived_sessions', 0)}日涨停/炸板为日线近似，不能冒充逐笔事件"
                ),
                "历史板块成分与板块上涨比例不完整",
                "未满足样本外、交易成本压力测试和至少8周模拟盘门槛",
            ]
        return {
            **item,
            "blockers": blockers,
            "factor_names": [
                FACTOR_BY_ID[factor_id]["name"]
                for factor_id in item["factor_ids"]
                if factor_id in FACTOR_BY_ID
            ],
        }

    async def get_latest_report(self) -> dict[str, Any] | None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, self.CACHE_KEY)
            if row and isinstance(row.payload, dict):
                return row.payload.get("latest")
        except Exception:
            return None
        return None

    async def workspace(self, *, force_refresh: bool = False) -> dict[str, Any]:
        manifest = await self.dataset_manifest(force_refresh=force_refresh)
        latest = await self.get_latest_report()
        categories: dict[str, int] = {}
        for factor in FACTOR_CATALOG:
            categories[factor["category"]] = categories.get(factor["category"], 0) + 1
        return {
            "version": "research-workspace-v1",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "factor_catalog": FACTOR_CATALOG,
            "factor_summary": {"total": len(FACTOR_CATALOG), "by_status": _status_summary(), "by_category": categories},
            "experiments": [self._experiment_view(item, manifest) for item in EXPERIMENT_CATALOG],
            "lifecycle": LIFECYCLE,
            "hard_gates": HARD_GATES,
            "dataset": manifest,
            "latest_report": latest,
            "active_job": latest_running_job("research"),
            "research_contract": {
                "ai_role": "提出假设、解释和审查；不执行任意代码、不修改回测数字。",
                "execution": "研究结果只进入报告和模拟盘，不连接券商、不自动下单。",
                "missing_data": "缺失或未验证字段保持缺失并降低状态，不补零、不伪造。",
                "test_isolation": "实验锁定参数哈希后才读取样本外指标。",
            },
        }

    def validate_dsl(self, definition: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        if not isinstance(definition, dict):
            return {"valid": False, "errors": ["DSL必须是对象"], "warnings": [], "factor_ids": []}
        forbidden = {"python", "code", "eval", "exec", "shell", "command", "sql", "script", "lambda", "raw_expression"}
        found_forbidden = sorted({key for key in definition if str(key).lower() in forbidden})
        if found_forbidden:
            errors.append(f"禁止执行字段：{','.join(found_forbidden)}")
        required = {"strategy_id", "name", "family", "version", "universe", "entry", "exit", "portfolio", "cost_model"}
        missing = sorted(required - set(definition))
        if missing:
            errors.append(f"缺少必填字段：{','.join(missing)}")
        factor_ids: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                if "factor" in value and isinstance(value["factor"], str):
                    factor_ids.append(value["factor"])
                for key, item in value.items():
                    if str(key).lower() in forbidden:
                        errors.append(f"禁止字段：{key}")
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(definition)
        unknown_factors = sorted(set(factor_ids) - set(FACTOR_BY_ID))
        if unknown_factors:
            errors.append(f"未注册因子：{','.join(unknown_factors)}")
        family = str(definition.get("family") or "").lower()
        if family not in {"overnight", "weekly", "monthly"}:
            errors.append("family只能是overnight、weekly或monthly")
        exit_config = definition.get("exit") if isinstance(definition.get("exit"), dict) else {}
        if family == "overnight" and not exit_config.get("force_exit_time"):
            errors.append("隔夜策略必须设置force_exit_time")
        if not exit_config.get("stop_loss_pct") and not exit_config.get("take_profit_pct"):
            errors.append("策略必须至少设置止损或止盈")
        portfolio = definition.get("portfolio") if isinstance(definition.get("portfolio"), dict) else {}
        max_single = portfolio.get("max_single_weight")
        if max_single is not None:
            try:
                if not 0 < float(max_single) <= 0.30:
                    errors.append("单票权重必须在0到30%之间")
            except (TypeError, ValueError):
                errors.append("单票权重必须是数字")
        if any(FACTOR_BY_ID[factor_id]["status"] == "DRAFT" for factor_id in set(factor_ids) if factor_id in FACTOR_BY_ID):
            warnings.append("包含尚未完成数据审计的因子，只能进入研究，不得进入模拟盘。")
        if not factor_ids:
            errors.append("策略至少引用一个已注册因子")
        canonical = {
            "strategy_id": definition.get("strategy_id"),
            "version": definition.get("version"),
            "family": family,
            "factors": sorted(set(factor_ids)),
            "definition": definition,
        }
        return {
            "valid": not errors,
            "status": "AUDITED" if not errors else "REJECTED",
            "status_label": "DSL审计通过" if not errors else "DSL审计拒绝",
            "errors": list(dict.fromkeys(errors)),
            "warnings": list(dict.fromkeys(warnings)),
            "factor_ids": sorted(set(factor_ids)),
            "factor_names": [FACTOR_BY_ID[item]["name"] for item in sorted(set(factor_ids)) if item in FACTOR_BY_ID],
            "dsl_hash": _canonical_hash(canonical),
            "safety": ["无任意Python/SQL/shell执行", "因子必须来自注册表", "策略只生成研究或模拟结果"],
        }

    @staticmethod
    def _partition_metrics(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
        rows = list(result.get("_daily_results_internal") or [])
        if not rows:
            return {}
        total = len(rows)
        if total >= 3:
            # Keep one period available for validation and OOS even for a
            # small synthetic dataset.  For normal runs this is a 60/20/20
            # chronological split.
            train_end = min(total - 2, max(1, int(total * 0.6)))
            validation_end = min(total - 1, max(train_end + 1, int(total * 0.8)))
        elif total == 2:
            train_end, validation_end = 1, 2
        else:
            train_end = validation_end = 1
        partitions = {
            "train": rows[:train_end],
            "validation": rows[train_end:validation_end],
            "out_of_sample": rows[validation_end:],
        }
        output: dict[str, dict[str, Any]] = {}
        for name, subset in partitions.items():
            returns = [float(item.get("avg_net_return_pct") or 0) for item in subset]
            positive = [item for item in returns if item > 0]
            negative = [abs(item) for item in returns if item < 0]
            profit_factor = sum(positive) / sum(negative) if negative else (999.0 if positive else 0.0)
            equity = 1.0
            peak = equity
            maximum_drawdown = 0.0
            for period_return in returns:
                equity *= 1 + period_return / 100
                peak = max(peak, equity)
                if peak > 0:
                    maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak * 100)
            output[name] = {
                "trading_periods": len(subset),
                "from": subset[0].get("date") if subset else None,
                "to": subset[-1].get("exit_date") if subset else None,
                "total_return": round((math.prod(1 + value / 100 for value in returns) - 1) * 100, 2) if returns else 0.0,
                "win_rate": round(sum(value > 0 for value in returns) / len(returns) * 100, 1) if returns else 0.0,
                "profit_factor": round(profit_factor, 3),
                "max_drawdown": round(maximum_drawdown, 2),
                "data_sufficient": len(subset) >= 30,
            }
        return output

    @staticmethod
    def _stress_result(rows: list[dict[str, Any]], *, cost_multiplier: float = 1.0, slippage_multiplier: float = 1.0) -> dict[str, Any]:
        """Reprice the recorded portfolio periods without inventing fills."""
        if not rows:
            return {"available": False, "total_return": 0.0, "max_drawdown": 0.0, "trading_periods": 0}
        returns: list[float] = []
        for row in rows:
            gross = float(row.get("avg_gross_return_pct") or 0.0) / 100
            commission = float(row.get("commission_cost_pct") or 0.0) / 100
            stamp_tax = float(row.get("stamp_tax_cost_pct") or 0.0) / 100
            slippage = float(row.get("slippage_cost_pct") or 0.0) / 100
            impact = float(row.get("impact_cost_pct") or 0.0) / 100
            stressed_cost = (commission + stamp_tax + impact) * cost_multiplier + slippage * slippage_multiplier
            returns.append(gross - stressed_cost)
        equity = 1.0
        peak = equity
        maximum_drawdown = 0.0
        for period_return in returns:
            equity *= 1 + period_return
            peak = max(peak, equity)
            if peak > 0:
                maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak * 100)
        return {
            "available": True,
            "trading_periods": len(returns),
            "total_return": round((math.prod(1 + value for value in returns) - 1) * 100, 2),
            "max_drawdown": round(maximum_drawdown, 2),
        }

    @staticmethod
    def _public_result(result: dict[str, Any]) -> dict[str, Any]:
        # The engine keeps the full rows only during this request.  Returning
        # the last 20 rows keeps the API bounded and still makes the report
        # inspectable.
        return {key: value for key, value in result.items() if not key.startswith("_")}

    async def run_experiment(self, request: dict[str, Any], progress_callback=None) -> dict[str, Any]:
        experiment_id = str(request.get("experiment_id") or "weekly_momentum_baseline_v1")
        experiment = next((item for item in EXPERIMENT_CATALOG if item["id"] == experiment_id), None)
        if experiment is None:
            raise ValueError("实验不存在")
        manifest = await self.dataset_manifest()
        if not experiment.get("supported"):
            params = {
                "days": int(request.get("days", 365)),
                "top_n": int(request.get("top_n", 10)),
                "lookback_days": int(request.get("lookback_days", 20)),
                "holding_days": int(request.get("holding_days", 5)),
                "capital": float(request.get("capital", 400000)),
            }
            strategy_lock_hash = _canonical_hash({
                "experiment_id": experiment_id,
                "dataset_id": manifest.get("dataset_id"),
                "manifest_hash": manifest.get("manifest_hash"),
                "params": params,
            })
            return {
                "report_version": "research-report-v1",
                "experiment_id": experiment_id,
                "experiment": self._experiment_view(experiment),
                "available": False,
                "status": "BLOCKED_DATA",
                "promotion_stage": "FACTOR_RESEARCH",
                "strategy_lock_hash": strategy_lock_hash,
                "dataset": manifest,
                "parameters": params,
                "result": {"available": False, "error": "实验所需数据未达到可得性门槛"},
                "partitions": {},
                "stress_tests": {},
                "gates": [],
                "blockers": experiment["blockers"],
                "audit_log": ["实验未启动：所需数据集未通过数据可得性门槛。"],
                "next_actions": experiment["blockers"],
                "result_hash": _canonical_hash({"lock": strategy_lock_hash, "status": "BLOCKED_DATA"}),
            }
        params = {
            "days": int(request.get("days", 365)),
            "top_n": int(request.get("top_n", 10)),
            "lookback_days": int(request.get("lookback_days", 20)),
            "holding_days": int(request.get("holding_days", 5)),
            "capital": float(request.get("capital", 400000)),
        }
        locked = {"experiment_id": experiment_id, "dataset_id": manifest.get("dataset_id"), "manifest_hash": manifest.get("manifest_hash"), "params": params}
        strategy_lock_hash = _canonical_hash(locked)
        raw_result = await quant_research_engine.run(**params, progress_callback=progress_callback)
        raw_result["_daily_results_internal"] = raw_result.get("_daily_results_internal") or []
        partitions = self._partition_metrics(raw_result)
        public_result = self._public_result(raw_result)
        oos = partitions.get("out_of_sample") or {}
        sensitivity = public_result.get("parameter_sensitivity") or []
        internal_rows = list(raw_result.get("_daily_results_internal") or [])
        stress = {
            "base": {"available": bool(internal_rows), "total_return": public_result.get("total_return", 0), "note": "统一成本模型"},
            "cost_plus_50pct": {
                **self._stress_result(internal_rows, cost_multiplier=1.5, slippage_multiplier=1.5),
                "note": "在已记录的佣金、印花税、滑点和冲击成本上增加50%；不改变成交数量。",
            },
            "slippage_x2": {
                **self._stress_result(internal_rows, slippage_multiplier=2.0),
                "note": "将记录的双边估算滑点翻倍；不冒充逐笔成交回放。",
            },
            "fill_rate_70pct": {"available": False, "note": "需要逐笔候选与成交状态字段"},
            "fill_rate_50pct": {"available": False, "note": "需要逐笔候选与成交状态字段"},
        }
        gates = [
            {"id": "period_count", "label": "有效交易期", "threshold": ">= 200", "actual": public_result.get("trading_periods", 0), "passed": public_result.get("trading_periods", 0) >= 200},
            {"id": "oos_return", "label": "样本外收益", "threshold": "> 0", "actual": oos.get("total_return", 0), "passed": bool(oos.get("data_sufficient")) and oos.get("total_return", 0) > 0},
            {"id": "oos_profit_factor", "label": "样本外Profit Factor", "threshold": ">= 1.20", "actual": oos.get("profit_factor", 0), "passed": bool(oos.get("data_sufficient")) and oos.get("profit_factor", 0) >= 1.2},
            {"id": "max_drawdown", "label": "最大回撤", "threshold": "<= 20%", "actual": public_result.get("max_drawdown", 0), "passed": public_result.get("max_drawdown", 0) <= 20},
            {"id": "cost_stress", "label": "成本增加50%后仍盈利", "threshold": "> 0", "actual": stress["cost_plus_50pct"].get("total_return"), "passed": stress["cost_plus_50pct"].get("available", False) and stress["cost_plus_50pct"].get("total_return", 0) > 0},
            {"id": "parameter_robustness", "label": "参数敏感性", "threshold": "15/20/25日均可复核", "actual": len(sensitivity), "passed": len(sensitivity) == 3 and all(item.get("trading_periods", 0) > 0 for item in sensitivity)},
            {"id": "pit_universe", "label": "点时股票池", "threshold": "必须可复核", "actual": manifest.get("universe", {}).get("status"), "passed": False, "reason": "历史股票池未登记"},
        ]
        if not raw_result.get("available"):
            status = "INSUFFICIENT_DATA"
            promotion_stage = "FACTOR_RESEARCH"
        elif all(item["passed"] for item in gates):
            status = "VALIDATION_PENDING"
            promotion_stage = "VALIDATION"
        else:
            status = "RESEARCH_ONLY"
            promotion_stage = "FACTOR_RESEARCH"
        report = {
            "report_version": "research-report-v1",
            "experiment_id": experiment_id,
            "experiment": self._experiment_view(experiment),
            "status": status,
            "promotion_stage": promotion_stage,
            "strategy_lock_hash": strategy_lock_hash,
            "dataset": manifest,
            "parameters": params,
            "result": public_result,
            "partitions": partitions,
            "stress_tests": stress,
            "gates": gates,
            "audit_log": [
                "参数与数据清单哈希在运行前锁定。",
                "信号按T日收盘计算，执行规则为T+1开盘，未使用同日收盘成交。",
                "样本外分段只在锁定参数后生成；不足门槛时不得晋级。",
                "未覆盖的逐笔成本、历史股票池和竞价数据保持阻断，不补造结果。",
            ],
            "next_actions": [
                "补齐历史股票池与公告日财务快照后重跑。",
                "将逐笔候选、成交率和滑点字段接入统一执行器。",
                "完成至少8周模拟盘前向验证后再申请人工晋级。",
            ],
            "result_hash": _canonical_hash({"lock": strategy_lock_hash, "result": public_result, "partitions": partitions, "gates": gates}),
        }
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, self.CACHE_KEY)
                history = list((row.payload or {}).get("history") or []) if row else []
                history = [report, *history[:9]]
                payload = {"latest": report, "history": history, "updated_at": datetime.utcnow().isoformat() + "Z"}
                if row is None:
                    session.add(MarketDataCache(key=self.CACHE_KEY, payload=payload))
                else:
                    row.payload = payload
                await session.commit()
        except Exception as exc:
            report["persistence_warning"] = f"研究报告未能持久化：{type(exc).__name__}"
        return report

    async def start_experiment(self, request: dict[str, Any]) -> dict[str, Any]:
        experiment_id = str(request.get("experiment_id") or "weekly_momentum_baseline_v1")
        if not any(item["id"] == experiment_id for item in EXPERIMENT_CATALOG):
            raise ValueError("实验不存在")
        running = latest_running_job("research")
        if running:
            return {**running, "already_running": True}
        job = create_job("research", "research", {
            "experiment_id": experiment_id,
            "request": dict(request),
        })
        spawn(self._run_job(job["job_id"], dict(request)))
        return job

    async def _run_job(self, job_id: str, request: dict[str, Any]) -> None:
        update_job(
            "research", job_id,
            status="running", phase="manifest", progress=5,
            message="正在锁定数据清单与研究参数", started_at=datetime.utcnow().isoformat() + "Z",
        )

        async def progress(value: int, phase: str, message: str) -> None:
            update_job("research", job_id, progress=value, phase=phase, message=message)

        try:
            report = await self.run_experiment(request, progress_callback=progress)
            update_job(
                "research", job_id,
                status="completed", phase="completed", progress=100,
                message="研究报告已生成并持久化", result=report,
                completed_at=datetime.utcnow().isoformat() + "Z",
            )
        except Exception as exc:
            update_job(
                "research", job_id,
                status="failed", phase="failed", progress=100,
                message="研究任务运行失败，请查看具体错误后重试",
                error=f"{type(exc).__name__}: {exc}"[:500],
                completed_at=datetime.utcnow().isoformat() + "Z",
            )

    @staticmethod
    def job(job_id: str) -> dict[str, Any] | None:
        return get_job("research", job_id)


quant_research_workspace = QuantResearchWorkspaceService()
