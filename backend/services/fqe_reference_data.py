"""Persistent security-master and three-year PE history for FQE research."""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import async_session
from models import (
    FQEDataSyncRun,
    SecurityMaster,
    SecurityStatusEvent,
    StockDailyBar,
    StockValuationHistory,
)
from quant.storage import quant_store
from services.data_collector import collector, normalize_stock_code, shanghai_now
from services.ftshare_mcp import ftshare_mcp_client


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _exchange(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return "BJ"
    return "SH" if code.startswith(("6", "9")) else "SZ"


def _percentile_rank(value: float | None, values: list[float]) -> float | None:
    if value is None or value <= 0 or not values:
        return None
    return round(sum(item <= value for item in values) / len(values) * 100, 4)


class FQEReferenceDataService:
    """Build and maintain the dated reference data required by strict FQE."""

    _VALUATION_CONCURRENCY = 6
    _VALUATION_BATCH_SIZE = 18
    _STATUS_BATCH_SIZE = 10
    _STATUS_CONCURRENCY = 4

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _insert_for(session, model):
        return postgresql_insert(model) if session.get_bind().dialect.name == "postgresql" else sqlite_insert(model)

    async def _upsert(self, model, rows: list[dict[str, Any]], keys: list[str]) -> int:
        if not rows:
            return 0
        async with async_session() as session:
            statement = self._insert_for(session, model).values(rows)
            updates = {
                column.name: getattr(statement.excluded, column.name)
                for column in model.__table__.columns
                if column.name not in {"id", *keys, "created_at"}
            }
            await session.execute(statement.on_conflict_do_update(index_elements=keys, set_=updates))
            await session.commit()
        return len(rows)

    async def _set_run(self, run_id: int, **values: Any) -> None:
        values["updated_at"] = datetime.utcnow()
        async with async_session() as session:
            await session.execute(update(FQEDataSyncRun).where(FQEDataSyncRun.id == run_id).values(**values))
            await session.commit()

    @staticmethod
    def _run_view(run: FQEDataSyncRun, *, already_running: bool = False) -> dict[str, Any]:
        total = int(run.total_securities or 0)
        completed = int(run.completed_securities or 0)
        stage_progress = completed / total * 85 if total else 0
        progress = 5 if run.stage == "security_master" else 10 + stage_progress if run.stage == "valuation_history" else 96 if run.stage == "market_evidence" else 100 if run.status in {"completed", "partial", "failed"} else 0
        return {
            "id": run.id,
            "run_id": run.id,
            "sync_mode": run.sync_mode,
            "requested_years": run.requested_years,
            "status": run.status,
            "stage": run.stage,
            "message": run.message,
            "progress": round(min(100.0, progress), 1),
            "total_securities": total,
            "completed_securities": completed,
            "master_count": int(run.master_count or 0),
            "inactive_count": int(run.inactive_count or 0),
            "valuation_count": int(run.valuation_count or 0),
            "failed_count": int(run.failed_count or 0),
            "failed_codes": list(run.failed_codes or []),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
            "error": run.error,
            "already_running": already_running,
        }

    def _track(self, run_id: int, task: asyncio.Task) -> None:
        self._tasks[run_id] = task

        def done(finished: asyncio.Task) -> None:
            self._tasks.pop(run_id, None)
            if finished.cancelled():
                return
            try:
                error = finished.exception()
            except asyncio.CancelledError:
                return
            if error:
                print(f"FQE reference sync {run_id} stopped: {type(error).__name__}: {error}")

        task.add_done_callback(done)

    async def queue_sync(self, *, full: bool = True, years: int = 3, force: bool = False) -> dict[str, Any]:
        years = min(max(int(years), 1), 5)
        async with async_session() as session:
            active = (await session.execute(
                select(FQEDataSyncRun)
                .where(FQEDataSyncRun.status.in_(("queued", "running")))
                .order_by(FQEDataSyncRun.id.desc())
                .limit(1)
            )).scalar_one_or_none()
            if active is not None:
                return self._run_view(active, already_running=True)
            run = FQEDataSyncRun(
                sync_mode="full" if full else "incremental",
                requested_years=years,
                force=bool(force),
                status="queued",
                stage="queued",
                message="审计数据同步已排队",
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)
        task = asyncio.create_task(self._run(run.id))
        self._track(run.id, task)
        return self._run_view(run)

    async def resume_incomplete_runs(self) -> list[int]:
        async with async_session() as session:
            rows = list((await session.execute(
                select(FQEDataSyncRun)
                .where(FQEDataSyncRun.status.in_(("queued", "running")))
                .order_by(FQEDataSyncRun.id.asc())
            )).scalars().all())
        resumed = []
        for row in rows:
            if row.id in self._tasks:
                continue
            task = asyncio.create_task(self._run(row.id))
            self._track(row.id, task)
            resumed.append(row.id)
        return resumed

    async def _run(self, run_id: int) -> None:
        async with self._lock:
            async with async_session() as session:
                run = await session.get(FQEDataSyncRun, run_id)
            if run is None:
                return
            try:
                await self._set_run(
                    run_id,
                    status="running",
                    stage="security_master",
                    message="正在同步现存及历史退市证券主表",
                    error=None,
                )
                master = await self._sync_security_master()
                master_warnings = list(master.get("warnings") or [])
                await self._set_run(
                    run_id,
                    master_count=master["master_count"],
                    inactive_count=master["inactive_count"],
                    message=f"证券主表已合并 {master['master_count']} 条，历史非活跃 {master['inactive_count']} 条",
                )
                if run.sync_mode == "full":
                    failures = await self._sync_full_valuations(run_id, run.requested_years, bool(run.force))
                else:
                    failures = await self._sync_incremental_valuations(run_id, run.requested_years)

                await self._set_run(
                    run_id,
                    stage="market_evidence",
                    message="正在补齐市场宽度、成交额与涨停情绪历史",
                )
                evidence_warning = None
                try:
                    from services.strategic_market_data import strategic_market_data_service

                    await strategic_market_data_service.sync_recent(days=30 if run.sync_mode == "full" else 5)
                except Exception as exc:
                    evidence_warning = f"战略市场证据同步失败: {type(exc).__name__}"

                coverage = await self.coverage()
                warnings = list(master_warnings)
                if failures:
                    warnings.append(f"PE历史仍有 {len(failures)} 只上游失败")
                if evidence_warning:
                    warnings.append(evidence_warning)
                if coverage["listing_dated"] < coverage["currently_listed"]:
                    warnings.append("部分现存股票上市日期仍待数据源补齐")
                status = "partial" if warnings else "completed"
                await self._set_run(
                    run_id,
                    status=status,
                    stage="completed",
                    message="审计数据同步完成" if not warnings else "审计数据主体完成，仍有少量缺口",
                    completed_at=datetime.utcnow(),
                    error="; ".join(warnings) or None,
                )
            except Exception as exc:
                await self._set_run(
                    run_id,
                    status="failed",
                    stage="failed",
                    message="审计数据同步失败",
                    completed_at=datetime.utcnow(),
                    error=f"{type(exc).__name__}: {exc}"[:1000],
                )

    async def _status_events(self, codes: list[str]) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(self._STATUS_CONCURRENCY)

        async def fetch_batch(batch: list[str]) -> list[dict[str, Any]]:
            result: dict[str, Any] | None = None
            for attempt in range(3):
                try:
                    async with semaphore:
                        result = await ftshare_mcp_client.get_stock_status_changes(batch)
                    break
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(0.35 * (2 ** attempt))
            if result is None:
                if len(batch) == 1:
                    return []
                middle = max(1, len(batch) // 2)
                left, right = await asyncio.gather(fetch_batch(batch[:middle]), fetch_batch(batch[middle:]))
                return [*left, *right]
            metadata = result.get("metadata") if isinstance(result, dict) else {}
            if isinstance(metadata, dict) and metadata.get("truncated") and len(batch) > 1:
                middle = max(1, len(batch) // 2)
                left, right = await asyncio.gather(fetch_batch(batch[:middle]), fetch_batch(batch[middle:]))
                return [*left, *right]
            return [item for item in (result.get("data") or []) if isinstance(item, dict)]

        output: list[dict[str, Any]] = []
        batches = list(_chunks(codes, self._STATUS_BATCH_SIZE))
        for group in _chunks(batches, self._STATUS_CONCURRENCY):
            results = await asyncio.gather(*(fetch_batch(batch) for batch in group), return_exceptions=True)
            for result in results:
                if not isinstance(result, Exception):
                    output.extend(result)
        return output

    async def _sync_security_master(self) -> dict[str, Any]:
        warnings: list[str] = []
        try:
            directory_snapshot = await collector.fetch_security_directory_snapshot(allow_partial=True)
        except Exception as exc:
            directory_snapshot = {
                "records": [], "complete": False, "failed_pages": [],
                "errors": {"directory": type(exc).__name__},
            }
        directory = [item for item in (directory_snapshot.get("records") or []) if isinstance(item, dict)]
        if not directory_snapshot.get("complete"):
            failed_pages = directory_snapshot.get("failed_pages") or []
            detail = f"，失败页 {','.join(map(str, failed_pages[:12]))}" if failed_pages else ""
            warnings.append(f"东方财富证券目录部分不可用{detail}，已使用FTShare及数据库主表补齐")

        catalog_rows: list[dict[str, Any]] = []
        try:
            catalog_rows = await ftshare_mcp_client.get_full_stock_filter()
        except Exception as exc:
            print(f"FTShare listing-date catalog unavailable: {type(exc).__name__}")
            warnings.append(f"FTShare现存股票目录暂不可用：{type(exc).__name__}")

        async with async_session() as session:
            existing_rows = list((await session.execute(
                select(SecurityMaster).order_by(SecurityMaster.stock_code.asc())
            )).scalars().all())
        existing = {row.stock_code: row for row in existing_rows}

        catalog: dict[str, dict[str, Any]] = {}
        for item in catalog_rows:
            raw = str(item.get("symbol") or "").split(".", 1)[0]
            try:
                catalog[normalize_stock_code(raw)] = item
            except ValueError:
                continue

        directory_by_code = {
            str(item.get("code")): item
            for item in directory
            if item.get("code")
        }
        all_codes = sorted({*directory_by_code, *catalog, *existing})
        if not all_codes:
            raise RuntimeError("东方财富、FTShare及数据库均无可用证券主表")
        if not directory and not catalog_rows and existing_rows:
            warnings.append("两个证券目录上游均不可用，本轮完整保留数据库已有主表")

        def active_for(code: str) -> bool:
            if code in catalog:
                return True
            if code in directory_by_code:
                return bool(directory_by_code[code].get("is_currently_listed"))
            previous = existing.get(code)
            return bool(previous.is_currently_listed) if previous else False

        inactive_codes = [code for code in all_codes if not active_for(code)]
        events: list[dict[str, Any]] = []
        if inactive_codes:
            try:
                events = await self._status_events(inactive_codes)
            except Exception as exc:
                print(f"FTShare inactive status catalog unavailable: {type(exc).__name__}")
        events_by_code: dict[str, list[dict[str, Any]]] = {}
        event_rows: list[dict[str, Any]] = []
        for item in events:
            raw_code = str(item.get("trade_code") or "").split(".", 1)[0]
            try:
                code = normalize_stock_code(raw_code)
            except ValueError:
                continue
            change_date = _date(item.get("change_date"))
            change_type = str(item.get("change_type") or "").strip()
            if change_date is None or not change_type:
                continue
            events_by_code.setdefault(code, []).append(item)
            event_rows.append({
                "stock_code": code,
                "stock_name": str(item.get("name") or ""),
                "change_date": change_date,
                "change_type": change_type,
                "details": str(item.get("change_details") or "")[:20000] or None,
                "source": "ftshare",
                "updated_at": datetime.utcnow(),
            })
        for batch in _chunks(event_rows, 100):
            await self._upsert(
                SecurityStatusEvent,
                batch,
                ["stock_code", "change_date", "change_type"],
            )

        now = datetime.utcnow()
        masters: list[dict[str, Any]] = []
        for code in all_codes:
            item = directory_by_code.get(code) or {}
            previous = existing.get(code)
            active = active_for(code)
            catalog_item = catalog.get(code) or {}
            listing_date = _date(catalog_item.get("listing_date")) or (previous.list_date if previous else None)
            terminal_dates: list[date] = []
            listing_dates: list[date] = []
            latest_event_type = ""
            dated_events = []
            for event in events_by_code.get(code, []):
                event_date = _date(event.get("change_date"))
                event_type = str(event.get("change_type") or "")
                if event_date is None:
                    continue
                dated_events.append((event_date, event_type))
                if event_type == "上市":
                    listing_dates.append(event_date)
                if "终止上市" in event_type or event_type == "退市":
                    terminal_dates.append(event_date)
            if dated_events:
                latest_event_type = max(dated_events)[1]
            if listing_date is None and listing_dates:
                listing_date = min(listing_dates)
            delist_date = max(terminal_dates) if terminal_dates else (previous.delist_date if previous else None)
            if active:
                status = "listed"
            elif delist_date:
                status = "delisted"
            elif "暂停" in latest_event_type:
                status = "suspended"
            elif previous and previous.status in {"suspended", "inactive", "delisted"}:
                status = previous.status
            else:
                status = "inactive"
            if listing_dates or terminal_dates:
                quality = "status_event"
            elif _date(catalog_item.get("listing_date")):
                quality = "ftshare_catalog"
            elif previous and previous.date_quality:
                quality = previous.date_quality
            else:
                quality = "missing"
            source_parts = []
            if item:
                source_parts.append("eastmoney_directory")
            if catalog_item:
                source_parts.append("ftshare")
            if previous:
                source_parts.append("database")
            masters.append({
                "stock_code": code,
                "stock_name": str(catalog_item.get("name") or item.get("name") or (previous.stock_name if previous else None) or code),
                "exchange": _exchange(code),
                "list_date": listing_date,
                "delist_date": delist_date,
                "status": status,
                "is_currently_listed": active,
                "date_quality": quality,
                "source": "+".join(source_parts) or "database",
                "source_updated_at": now,
                "updated_at": now,
            })
        for batch in _chunks(masters, 500):
            await self._upsert(SecurityMaster, batch, ["stock_code"])
        return {
            "master_count": len(masters),
            "inactive_count": sum(not item["is_currently_listed"] for item in masters),
            "directory_complete": bool(directory_snapshot.get("complete")),
            "directory_count": len(directory),
            "catalog_count": len(catalog),
            "database_count": len(existing),
            "warnings": warnings,
        }

    async def _fetch_eastmoney_valuation(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        base = {
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": "1000",
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,PE_TTM",
            "quoteColumns": "",
            "source": "WEB",
            "client": "WEB",
            "filter": f'(SECURITY_CODE="{code}")(TRADE_DATE>=\'{start.isoformat()}\')(TRADE_DATE<=\'{end.isoformat()}\')',
        }
        first = await collector.fetch_json(collector.DATACENTER_URL, {**base, "pageNumber": "1"})
        result = first.get("result") or {}
        rows = list(result.get("data") or [])
        pages = int(result.get("pages") or 1)
        for page in range(2, pages + 1):
            payload = await collector.fetch_json(collector.DATACENTER_URL, {**base, "pageNumber": str(page)})
            rows.extend((payload.get("result") or {}).get("data") or [])
        return rows

    async def _fetch_valuation(self, code: str, start: date, end: date) -> tuple[list[dict[str, Any]], str]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                rows = await asyncio.wait_for(
                    self._fetch_eastmoney_valuation(code, start, end),
                    timeout=25,
                )
                return rows, "eastmoney_proxy"
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.35 * (attempt + 1))
        try:
            rows = await ftshare_mcp_client.get_stock_valuation_history(
                code, start.isoformat(), end.isoformat(),
            )
            return rows, "ftshare_mcp"
        except Exception as exc:
            raise RuntimeError(f"valuation unavailable after {type(last_error).__name__ if last_error else 'source error'}") from exc

    @staticmethod
    def _valuation_record(
        code: str,
        name: str,
        rows: list[dict[str, Any]],
        requested_start: date,
        source: str,
        *,
        sync_status: str = "available",
    ) -> dict[str, Any]:
        values: dict[date, float] = {}
        resolved_name = name
        for item in rows:
            trade_date = _date(item.get("TRADE_DATE") or item.get("trade_date"))
            pe = _number(item.get("PE_TTM") if "PE_TTM" in item else item.get("pe_ttm"))
            if trade_date is None or pe is None:
                continue
            values[trade_date] = pe
            resolved_name = str(item.get("SECURITY_NAME_ABBR") or item.get("stock_name") or resolved_name)
        ordered = sorted(values.items())
        positive = [value for _, value in ordered if value > 0]
        latest_pe = ordered[-1][1] if ordered else None
        status = sync_status if ordered else "empty"
        return {
            "stock_code": code,
            "stock_name": resolved_name,
            "history": [[day.isoformat(), round(value, 8)] for day, value in ordered],
            "requested_start": requested_start,
            "history_start": ordered[0][0] if ordered else None,
            "history_end": ordered[-1][0] if ordered else None,
            "sample_count": len(ordered),
            "positive_sample_count": len(positive),
            "latest_pe_ttm": latest_pe,
            "pe_percentile_3y": _percentile_rank(latest_pe, positive),
            "sync_status": status,
            "source": source,
            "updated_at": datetime.utcnow(),
        }

    async def _sync_full_valuations(self, run_id: int, years: int, force: bool) -> list[str]:
        end = shanghai_now().date()
        start = end - timedelta(days=365 * years + 10)
        async with async_session() as session:
            masters = list((await session.execute(
                select(SecurityMaster).order_by(SecurityMaster.stock_code.asc())
            )).scalars().all())
            complete_codes = set()
            if not force:
                complete_codes = set((await session.execute(
                    select(StockValuationHistory.stock_code).where(
                        StockValuationHistory.requested_start <= start,
                        StockValuationHistory.sync_status.in_(("available", "empty")),
                    )
                )).scalars().all())
        pending = [row for row in masters if row.stock_code not in complete_codes]
        completed = len(masters) - len(pending)
        failures: list[str] = []
        await self._set_run(
            run_id,
            stage="valuation_history",
            message=f"正在回填近{years}年PE历史",
            total_securities=len(masters),
            completed_securities=completed,
            valuation_count=completed,
        )
        outside_window = [
            row for row in pending
            if not row.is_currently_listed and row.delist_date and row.delist_date < start
        ]
        if outside_window:
            empty_records = [
                self._valuation_record(
                    row.stock_code, row.stock_name, [], start,
                    "security_master", sync_status="empty",
                )
                for row in outside_window
            ]
            await self._upsert(StockValuationHistory, empty_records, ["stock_code"])
            outside_codes = {row.stock_code for row in outside_window}
            pending = [row for row in pending if row.stock_code not in outside_codes]
            completed += len(outside_window)
            await self._set_run(
                run_id,
                completed_securities=completed,
                valuation_count=completed,
                message=f"已跳过估值窗口前退市证券 {len(outside_window)} 只",
            )
        semaphore = asyncio.Semaphore(self._VALUATION_CONCURRENCY)

        async def fetch_one(master: SecurityMaster):
            async with semaphore:
                rows, source = await self._fetch_valuation(master.stock_code, start, end)
            return self._valuation_record(master.stock_code, master.stock_name, rows, start, source)

        for batch in _chunks(pending, self._VALUATION_BATCH_SIZE):
            fetched = await asyncio.gather(*(fetch_one(master) for master in batch), return_exceptions=True)
            records: list[dict[str, Any]] = []
            for master, result in zip(batch, fetched):
                completed += 1
                if isinstance(result, Exception):
                    failures.append(master.stock_code)
                else:
                    records.append(result)
            if records:
                await self._upsert(StockValuationHistory, records, ["stock_code"])
            await self._set_run(
                run_id,
                completed_securities=completed,
                valuation_count=completed - len(failures),
                failed_count=len(failures),
                failed_codes=failures[-100:],
                message=f"PE历史已处理 {completed}/{len(masters)}",
            )
        return failures

    async def _latest_market_date(self) -> date | None:
        snapshot = quant_store.read("market_snapshot")
        resolved = _date(snapshot.get("data_date"))
        if resolved:
            return resolved
        async with async_session() as session:
            return (await session.execute(select(func.max(StockDailyBar.trade_date)))).scalar_one_or_none()

    async def _fetch_market_valuation_date(self, trade_date: date) -> list[dict[str, Any]]:
        base = {
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
            "pageSize": "500",
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,PE_TTM",
            "quoteColumns": "",
            "source": "WEB",
            "client": "WEB",
            "filter": f"(TRADE_DATE='{trade_date.isoformat()}')",
        }
        first = await collector.fetch_json(collector.DATACENTER_URL, {**base, "pageNumber": "1"})
        result = first.get("result") or {}
        rows = list(result.get("data") or [])
        pages = int(result.get("pages") or 1)
        for start_page in range(2, pages + 1, 6):
            payloads = await asyncio.gather(*(
                collector.fetch_json(collector.DATACENTER_URL, {**base, "pageNumber": str(page)})
                for page in range(start_page, min(start_page + 6, pages + 1))
            ))
            for payload in payloads:
                rows.extend((payload.get("result") or {}).get("data") or [])
        return rows

    async def _sync_incremental_valuations(self, run_id: int, years: int) -> list[str]:
        trade_date = await self._latest_market_date()
        if trade_date is None:
            raise RuntimeError("缺少可核验的最近交易日")
        rows = await self._fetch_market_valuation_date(trade_date)
        by_code: dict[str, dict[str, Any]] = {}
        for item in rows:
            try:
                code = normalize_stock_code(item.get("SECURITY_CODE"))
            except ValueError:
                continue
            by_code[code] = item
        cutoff = trade_date - timedelta(days=365 * years + 10)
        codes = sorted(by_code)
        completed = 0
        await self._set_run(
            run_id,
            stage="valuation_history",
            total_securities=len(codes),
            completed_securities=0,
            message=f"正在增量保存 {trade_date.isoformat()} PE快照",
        )
        for code_batch in _chunks(codes, 200):
            async with async_session() as session:
                existing_rows = list((await session.execute(
                    select(StockValuationHistory).where(StockValuationHistory.stock_code.in_(code_batch))
                )).scalars().all())
            existing = {row.stock_code: row for row in existing_rows}
            records = []
            for code in code_batch:
                item = by_code[code]
                current = existing.get(code)
                history = list(current.history or []) if current else []
                history.append([trade_date.isoformat(), item.get("PE_TTM")])
                normalized_rows = [
                    {"TRADE_DATE": day, "PE_TTM": pe, "SECURITY_NAME_ABBR": item.get("SECURITY_NAME_ABBR")}
                    for day, pe in history
                    if (_date(day) or date.min) >= cutoff
                ]
                record = self._valuation_record(
                    code,
                    str(item.get("SECURITY_NAME_ABBR") or (current.stock_name if current else code)),
                    normalized_rows,
                    current.requested_start if current else cutoff,
                    "eastmoney_proxy",
                    sync_status="available" if normalized_rows else (current.sync_status if current else "empty"),
                )
                records.append(record)
            await self._upsert(StockValuationHistory, records, ["stock_code"])
            completed += len(code_batch)
            await self._set_run(
                run_id,
                completed_securities=completed,
                valuation_count=completed,
                message=f"当日PE快照已合并 {completed}/{len(codes)}",
            )
        return []

    async def coverage(self) -> dict[str, Any]:
        async with async_session() as session:
            security_total = int((await session.execute(select(func.count()).select_from(SecurityMaster))).scalar_one() or 0)
            currently_listed = int((await session.execute(
                select(func.count()).select_from(SecurityMaster).where(SecurityMaster.is_currently_listed.is_(True))
            )).scalar_one() or 0)
            listing_dated = int((await session.execute(
                select(func.count()).select_from(SecurityMaster).where(
                    SecurityMaster.is_currently_listed.is_(True), SecurityMaster.list_date.is_not(None),
                )
            )).scalar_one() or 0)
            inactive_total = int((await session.execute(
                select(func.count()).select_from(SecurityMaster).where(SecurityMaster.is_currently_listed.is_(False))
            )).scalar_one() or 0)
            inactive_dated = int((await session.execute(
                select(func.count()).select_from(SecurityMaster).where(
                    SecurityMaster.is_currently_listed.is_(False),
                    SecurityMaster.list_date.is_not(None),
                    SecurityMaster.delist_date.is_not(None),
                )
            )).scalar_one() or 0)
            status_events = int((await session.execute(select(func.count()).select_from(SecurityStatusEvent))).scalar_one() or 0)
            valuation_series = int((await session.execute(
                select(func.count()).select_from(StockValuationHistory).where(
                    StockValuationHistory.sync_status == "available",
                    StockValuationHistory.sample_count > 0,
                )
            )).scalar_one() or 0)
            valuation_percentiles = int((await session.execute(
                select(func.count()).select_from(StockValuationHistory).where(
                    StockValuationHistory.sync_status == "available",
                    StockValuationHistory.pe_percentile_3y.is_not(None),
                )
            )).scalar_one() or 0)
            current_valuation_series = int((await session.execute(
                select(func.count()).select_from(StockValuationHistory)
                .join(SecurityMaster, SecurityMaster.stock_code == StockValuationHistory.stock_code)
                .where(
                    SecurityMaster.is_currently_listed.is_(True),
                    StockValuationHistory.sync_status == "available",
                    StockValuationHistory.sample_count > 0,
                )
            )).scalar_one() or 0)
            current_valuation_percentiles = int((await session.execute(
                select(func.count()).select_from(StockValuationHistory)
                .join(SecurityMaster, SecurityMaster.stock_code == StockValuationHistory.stock_code)
                .where(
                    SecurityMaster.is_currently_listed.is_(True),
                    StockValuationHistory.sync_status == "available",
                    StockValuationHistory.pe_percentile_3y.is_not(None),
                )
            )).scalar_one() or 0)
            valuation_date = (await session.execute(select(func.max(StockValuationHistory.history_end)))).scalar_one_or_none()
        return {
            "security_total": security_total,
            "currently_listed": currently_listed,
            "listing_dated": listing_dated,
            "inactive_total": inactive_total,
            "inactive_dated": inactive_dated,
            "status_events": status_events,
            "valuation_series": valuation_series,
            "valuation_percentiles": valuation_percentiles,
            "current_valuation_series": current_valuation_series,
            "current_valuation_percentiles": current_valuation_percentiles,
            "valuation_date": valuation_date.isoformat() if valuation_date else None,
        }

    async def enrich(self, stocks: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
        codes = [str(item.get("code") or "") for item in stocks if item.get("code")]
        masters: dict[str, tuple[date | None, date | None, str]] = {}
        valuations: dict[str, dict[str, Any]] = {}
        if codes:
            async with async_session() as session:
                master_rows = (await session.execute(
                    select(
                        SecurityMaster.stock_code, SecurityMaster.list_date,
                        SecurityMaster.delist_date, SecurityMaster.status,
                    ).where(SecurityMaster.stock_code.in_(codes))
                )).all()
                valuation_rows = (await session.execute(
                    select(
                        StockValuationHistory.stock_code,
                        StockValuationHistory.history,
                        StockValuationHistory.sample_count,
                        StockValuationHistory.positive_sample_count,
                        StockValuationHistory.history_end,
                        StockValuationHistory.sync_status,
                    ).where(StockValuationHistory.stock_code.in_(codes))
                )).all()
            masters = {str(code): (listed, delisted, status) for code, listed, delisted, status in master_rows}
            valuations = {
                str(code): {
                    "history": history if isinstance(history, list) else [],
                    "sample_count": int(samples or 0),
                    "positive_sample_count": int(positive or 0),
                    "history_end": history_end,
                    "status": status,
                }
                for code, history, samples, positive, history_end, status in valuation_rows
            }

        enriched = []
        for stock in stocks:
            item = dict(stock)
            code = str(item.get("code") or "")
            master = masters.get(code)
            if master and master[0] and master[0] <= as_of:
                item["list_date"] = master[0].isoformat()
                item["list_days"] = (as_of - master[0]).days
                item["security_status"] = master[2]
            valuation = valuations.get(code)
            if valuation:
                cutoff = as_of - timedelta(days=365 * 3 + 10)
                dated_history = []
                for raw in valuation["history"]:
                    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                        continue
                    history_date = _date(raw[0])
                    pe_value = _number(raw[1])
                    if history_date is None or pe_value is None or not cutoff <= history_date <= as_of:
                        continue
                    dated_history.append((history_date, pe_value))
                positive_history = [pe for _, pe in dated_history if pe > 0]
                current_pe = _number(item.get("pe_ttm") if "pe_ttm" in item else item.get("pe"))
                item["pe_percentile_3y"] = _percentile_rank(current_pe, positive_history)
                item["pe_history_count"] = len(dated_history)
                item["pe_positive_history_count"] = len(positive_history)
                latest_history_date = max((day for day, _ in dated_history), default=None)
                item["pe_history_end"] = latest_history_date.isoformat() if latest_history_date else None
                item["pe_history_status"] = valuation["status"]
            enriched.append(item)

        global_coverage = await self.coverage()
        listing_covered = sum(1 for item in enriched if item.get("list_days") is not None)
        positive_pe_total = sum(1 for item in enriched if (_number(item.get("pe_ttm") if "pe_ttm" in item else item.get("pe")) or 0) > 0)
        percentile_covered = sum(
            1 for item in enriched
            if (_number(item.get("pe_ttm") if "pe_ttm" in item else item.get("pe")) or 0) > 0
            and _number(item.get("pe_percentile_3y")) is not None
            and item.get("pe_history_status") == "available"
        )
        inactive_total = int(global_coverage["inactive_total"])
        inactive_dated = int(global_coverage["inactive_dated"])

        def status(covered: int, total: int) -> str:
            if total > 0 and covered >= total:
                return "available"
            return "partial" if covered > 0 else "missing"

        warnings = []
        if listing_covered < len(enriched):
            warnings.append(f"上市日期覆盖 {listing_covered}/{len(enriched)}")
        if percentile_covered < positive_pe_total:
            warnings.append(f"正PE股票三年分位覆盖 {percentile_covered}/{positive_pe_total}")
        if inactive_dated < inactive_total:
            warnings.append(f"历史非活跃证券日期覆盖 {inactive_dated}/{inactive_total}")
        return {
            "stocks": enriched,
            "coverage": global_coverage,
            "warnings": warnings,
            "data_contract": {
                "listing_history": {
                    "status": status(listing_covered, len(enriched)),
                    "covered": listing_covered,
                    "total": len(enriched),
                    "note": "上市日期来自FTShare目录、交易状态事件及可核验历史推断",
                },
                "pe_history_percentile": {
                    "status": status(percentile_covered, positive_pe_total),
                    "covered": percentile_covered,
                    "total": positive_pe_total,
                    "note": "分母为当前PE(TTM)>0的股票；使用近三年日PE历史计算",
                },
                "survivorship_bias": {
                    "status": status(inactive_dated, inactive_total),
                    "covered": inactive_dated,
                    "total": inactive_total,
                    "note": f"证券主表含{inactive_total}只历史非活跃/退市证券及{global_coverage['status_events']}条状态事件",
                },
            },
        }

    async def latest_status(self) -> dict[str, Any]:
        async with async_session() as session:
            run = (await session.execute(
                select(FQEDataSyncRun).order_by(FQEDataSyncRun.id.desc()).limit(1)
            )).scalar_one_or_none()
        return {
            "run": self._run_view(run) if run else None,
            "coverage": await self.coverage(),
        }

    async def ensure_initialized(self) -> dict[str, Any]:
        """Queue the first recoverable full sync without blocking application startup."""
        status = await self.latest_status()
        active = status.get("run") or {}
        if active.get("status") in {"queued", "running"}:
            return status
        coverage = status.get("coverage") or {}
        if not coverage.get("security_total") or not coverage.get("valuation_series"):
            return {
                **status,
                "run": await self.queue_sync(full=True, years=3, force=False),
            }
        return status

    async def get_history(self, stock_code: str, days: int = 1095) -> dict[str, Any] | None:
        code = normalize_stock_code(stock_code)
        cutoff = shanghai_now().date() - timedelta(days=min(max(int(days), 1), 1825))
        async with async_session() as session:
            row = await session.get(StockValuationHistory, code)
        if row is None:
            return None
        history = [item for item in (row.history or []) if _date(item[0]) and _date(item[0]) >= cutoff]
        return {
            "stock_code": code,
            "stock_name": row.stock_name,
            "history": [{"date": item[0], "pe_ttm": item[1]} for item in history],
            "sample_count": len(history),
            "pe_percentile_3y": row.pe_percentile_3y,
            "history_start": history[0][0] if history else None,
            "history_end": history[-1][0] if history else None,
            "source": row.source,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


fqe_reference_data = FQEReferenceDataService()
