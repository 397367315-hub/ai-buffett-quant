"""Complete-market signal scans with cache fallback and observable progress."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select

from config import settings
from database import async_session
from models import StockDailyBar
from quant.engine import get_strategy, list_strategies, match_stock
from quant.indicators import enrich_with_indicators, normalize_snapshot_stock
from quant.jobs import create_job, get_job, latest_running_job, spawn, update_job
from quant.report import build_feature_coverage_report
from quant.rules import TECHNICAL_RULE_TYPES, static_group_can_match
from quant.storage import quant_store
from services.data_collector import collector, shanghai_now
from services.stock_features import required_feature_fields, stock_feature_service


def _uses_technical_rules(strategy: dict) -> bool:
    rules = [
        *((strategy.get("filter") or {}).get("rules") or []),
        *((strategy.get("entry") or {}).get("rules") or []),
    ]
    return any(rule.get("type") in TECHNICAL_RULE_TYPES for rule in rules)


def _strategy_can_match_without_history(strategy: dict, stock: dict) -> bool:
    return static_group_can_match(strategy.get("filter") or {}, stock) and static_group_can_match(
        strategy.get("entry") or {}, stock
    )


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class QuantSignalService:
    def __init__(self):
        self._run_lock = asyncio.Lock()

    async def start_scan(
        self,
        strategy_id: str | None = None,
        force: bool = False,
        scheduled_only: bool = False,
    ) -> dict:
        if strategy_id and get_strategy(strategy_id) is None:
            raise KeyError("策略不存在")
        if not strategy_id:
            eligible = [
                item for item in list_strategies()
                if item.get("active") and (not scheduled_only or item.get("scan_schedule", "daily") == "daily")
            ]
            if not eligible:
                message = "没有可扫描的盘中定时策略" if scheduled_only else "没有可扫描的启用策略"
                raise ValueError(message)
        running = latest_running_job("scan")
        if running and not force:
            return running
        job = create_job("scan", "scan", {"strategy_id": strategy_id, "scheduled_only": scheduled_only})
        spawn(self._run_job(job["job_id"], strategy_id, force, scheduled_only))
        return job

    async def _run_job(
        self,
        job_id: str,
        strategy_id: str | None,
        force: bool,
        scheduled_only: bool,
    ) -> None:
        async with self._run_lock:
            update_job(
                "scan", job_id, status="running", phase="market_snapshot", progress=3,
                message="正在拉取全市场实时行情", started_at=shanghai_now().isoformat(),
            )
            try:
                result = await self.scan(
                    strategy_id=strategy_id,
                    force=force,
                    job_id=job_id,
                    scheduled_only=scheduled_only,
                )
                update_job(
                    "scan", job_id, status="completed", phase="completed", progress=100,
                    message=f"扫描完成，生成 {len(result.get('signals', []))} 条信号",
                    completed_at=shanghai_now().isoformat(), result={
                        "count": len(result.get("signals", [])),
                        "scanned_stocks": result.get("scanned_stocks", 0),
                        "warning": result.get("warning"),
                    },
                )
            except Exception as exc:
                update_job(
                    "scan", job_id, status="failed", phase="failed", progress=100,
                    message="信号扫描失败", error=str(exc)[:300], completed_at=shanghai_now().isoformat(),
                )

    async def _market_snapshot(self, force: bool) -> tuple[dict, bool, str | None]:
        cached = quant_store.read("market_snapshot")
        fetched_at = _parse_time(cached.get("fetched_at"))
        age = (shanghai_now() - fetched_at).total_seconds() if fetched_at else None
        if not force and cached.get("stocks") and age is not None and age <= settings.quant_scan_cache_seconds:
            return cached, False, None
        try:
            snapshot = await collector.fetch_quant_market_snapshot()
            quant_store.write("market_snapshot", {"version": 1, **snapshot})
            return snapshot, False, None
        except Exception as exc:
            if cached.get("stocks"):
                warning = f"实时行情不可用，已降级到 {cached.get('fetched_at') or '上次'} 的缓存快照"
                return {**cached, "is_realtime": False}, True, warning
            raise RuntimeError("实时行情与本地行情缓存均不可用") from exc

    async def _annotate_board_codes(self, stocks: list[dict], strategies: list[dict]) -> None:
        board_codes = {
            str(value).strip().upper()
            for strategy in strategies
            for group_name in ("filter", "entry")
            for rule in ((strategy.get(group_name) or {}).get("rules") or [])
            if rule.get("type") == "sector"
            for value in (rule.get("value") or [])
            if str(value).strip().upper().startswith("BK")
        }
        if not board_codes:
            return
        memberships = await asyncio.gather(
            *(collector.fetch_all_board_stocks(code, sector_name=code) for code in sorted(board_codes)),
            return_exceptions=True,
        )
        by_code = {stock["code"]: stock for stock in stocks}
        for board_code, payload in zip(sorted(board_codes), memberships):
            if isinstance(payload, Exception):
                continue
            for member in payload.get("stocks") or []:
                stock = by_code.get(member.get("code"))
                if stock is not None:
                    stock.setdefault("sectors", []).append(board_code)

    async def _load_bars(self, codes: list[str], job_id: str | None = None) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        cutoff = shanghai_now().date() - timedelta(days=180)
        batches = [codes[index:index + 200] for index in range(0, len(codes), 200)]
        for index, batch in enumerate(batches, start=1):
            async with async_session() as session:
                rows = (await session.execute(
                    select(StockDailyBar)
                    .where(StockDailyBar.stock_code.in_(batch), StockDailyBar.trade_date >= cutoff)
                    .order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
                )).scalars().all()
            for row in rows:
                grouped.setdefault(row.stock_code, []).append({
                    "date": row.trade_date.isoformat(), "open": row.open_price, "close": row.close_price,
                    "high": row.high_price, "low": row.low_price, "volume": row.volume,
                    "amount": row.amount, "turnover": row.turnover,
                })
            if job_id and batches:
                progress = 35 + round(index / len(batches) * 40)
                update_job(
                    "scan", job_id, phase="technical_indicators", progress=progress,
                    message=f"正在计算技术指标 {index}/{len(batches)} 批",
                )
        return grouped

    @staticmethod
    def _deduplicate(signals: list[dict]) -> list[dict]:
        by_code: dict[str, dict] = {}
        for signal in sorted(signals, key=lambda item: item.get("match_score", 0), reverse=True):
            code = signal["stock_code"]
            strategy_match = {
                "strategy_id": signal["strategy_id"], "strategy_name": signal["strategy_name"],
                "match_score": signal["match_score"], "matched_rules": signal["matched_rules"],
            }
            if code not in by_code:
                by_code[code] = {
                    **signal,
                    "signal_id": f"sig_{shanghai_now().strftime('%Y%m%d')}_{code}",
                    "strategy_ids": [signal["strategy_id"]],
                    "strategy_matches": [strategy_match],
                }
            else:
                by_code[code]["strategy_ids"].append(signal["strategy_id"])
                by_code[code]["strategy_matches"].append(strategy_match)
        return sorted(by_code.values(), key=lambda item: (item.get("match_score", 0), item["stock_code"]), reverse=True)

    async def scan(
        self,
        *,
        strategy_id: str | None = None,
        force: bool = False,
        job_id: str | None = None,
        persist: bool = True,
        strategies_override: list[dict] | None = None,
        scheduled_only: bool = False,
    ) -> dict:
        strategies = strategies_override if strategies_override is not None else (
            [get_strategy(strategy_id)] if strategy_id else [
                item for item in list_strategies()
                if item.get("active") and (not scheduled_only or item.get("scan_schedule", "daily") == "daily")
            ]
        )
        strategies = [item for item in strategies if item]
        if not strategies:
            raise ValueError("没有可扫描的启用策略")
        snapshot, stale, warning = await self._market_snapshot(force)
        contexts = [normalize_snapshot_stock(item) for item in snapshot.get("stocks") or []]
        await self._annotate_board_codes(contexts, strategies)
        # Every live signal is subject to the same non-compensating profit and
        # near-term lock-up checks, even when a custom strategy omits them.
        feature_fields = required_feature_fields(strategies) | {
            "net_profit", "is_profitable_non_st", "lockup_days", "lockup_ratio_pct",
        }
        feature_coverage: dict = {"total": len(contexts)}
        feature_warnings: list[str] = []
        feature_updated_at: str | None = None
        if feature_fields:
            if job_id:
                update_job(
                    "scan", job_id, phase="feature_data", progress=18,
                    message="正在合并财务披露、股东户数、解禁与市场环境",
                )
            try:
                feature_result = await stock_feature_service.enrich(
                    contexts,
                    feature_fields,
                    full_market=bool(snapshot.get("complete")),
                )
                contexts = feature_result["stocks"]
                feature_coverage = feature_result.get("coverage") or feature_coverage
                feature_warnings = feature_result.get("warnings") or []
                feature_updated_at = feature_result.get("source_updated_at")
            except Exception as exc:
                feature_warnings.append(f"高级特征合并失败，相关规则按数据不足处理（{type(exc).__name__}）")
        if feature_warnings:
            extra = "；".join(feature_warnings)
            warning = f"{warning}；{extra}" if warning else extra
        if job_id:
            update_job(
                "scan", job_id, phase="static_filter", progress=25,
                message=f"已获取 {len(contexts)} 只股票，正在执行静态规则",
                total_stocks=len(contexts), processed_stocks=0,
            )

        technical_candidates: dict[str, dict] = {}
        for stock in contexts:
            for strategy in strategies:
                if _uses_technical_rules(strategy) and _strategy_can_match_without_history(strategy, stock):
                    technical_candidates[stock["code"]] = stock
        technical_truncated = False
        technical_limit = settings.quant_scan_max_technical_stocks
        candidate_values = list(technical_candidates.values())
        if len(candidate_values) > technical_limit:
            candidate_values.sort(
                key=lambda item: (abs(item.get("main_inflow") or 0), item.get("vol_ratio") or 0),
                reverse=True,
            )
            candidate_values = candidate_values[:technical_limit]
            technical_truncated = True
            extra = f"技术指标候选超过资源上限，仅计算优先级最高的 {technical_limit} 只"
            warning = f"{warning}；{extra}" if warning else extra
        bars: dict[str, list[dict]] = {}
        if candidate_values:
            try:
                bars = await self._load_bars([item["code"] for item in candidate_values], job_id)
            except Exception:
                extra = "历史日线缓存读取失败，技术指标策略本次不会生成匹配信号"
                warning = f"{warning}；{extra}" if warning else extra
        enriched_by_code = {
            item["code"]: enrich_with_indicators(item, bars.get(item["code"], []))
            for item in candidate_values
        }
        if job_id:
            update_job("scan", job_id, phase="matching", progress=82, message="正在合并规则并生成信号")

        matches = []
        for index, stock in enumerate(contexts, start=1):
            for strategy in strategies:
                context = enriched_by_code.get(stock["code"], stock) if _uses_technical_rules(strategy) else stock
                signal = match_stock(strategy, context)
                if signal:
                    matches.append(signal)
            if job_id and index % 500 == 0:
                update_job("scan", job_id, processed_stocks=index, progress=min(96, 82 + round(index / max(len(contexts), 1) * 14)))

        signals = self._deduplicate(matches)
        generated_at = shanghai_now().isoformat()
        payload = {
            "version": 1,
            "generated_at": generated_at,
            "data_date": snapshot.get("data_date"),
            "source": snapshot.get("source", "cache"),
            "is_realtime": bool(snapshot.get("is_realtime")) and not stale,
            "stale": stale,
            "warning": warning,
            "scanned_stocks": len(contexts),
            "technical_candidate_count": len(technical_candidates),
            "technical_evaluated_count": len(candidate_values),
            "technical_history_coverage": sum(len(bars.get(item["code"], [])) >= 60 for item in candidate_values),
            "technical_truncated": technical_truncated,
            "strategy_count": len(strategies),
            "feature_updated_at": feature_updated_at,
            "feature_coverage": build_feature_coverage_report(
                feature_coverage, feature_fields, feature_warnings,
            ),
            "signals": signals,
        }
        if persist:
            quant_store.write("signals", payload)

            def append_history(document: dict) -> None:
                document.setdefault("scans", []).append(payload)
                document["scans"] = document["scans"][-100:]

            quant_store.update("signal_history", append_history)
        return payload

    def get_signals(self, strategy_id: str | None = None) -> dict:
        payload = quant_store.read("signals")
        if strategy_id:
            payload["signals"] = [
                item for item in payload.get("signals", [])
                if strategy_id in (item.get("strategy_ids") or [item.get("strategy_id")])
            ]
        return payload

    def get_history(self, limit: int = 20) -> list[dict]:
        return list(reversed(quant_store.read("signal_history").get("scans", [])[-limit:]))

    @staticmethod
    def get_status(job_id: str) -> dict | None:
        return get_job("scan", job_id)


quant_signal_service = QuantSignalService()
