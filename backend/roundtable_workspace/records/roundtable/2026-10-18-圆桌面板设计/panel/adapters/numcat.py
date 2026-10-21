"""Numcat（猫爪）数据适配器。

- 凭据只由 meoz CLI 自己从受限文件或环境变量读取；本模块不读、不传、不打印密钥；
  子进程用 shell=False 与固定参数，请求体写入临时文件，不进命令行参数；
- 每个响应原样存入内容寻址证据仓（captured_at、请求参数、原始字节 SHA-256）；
- 429 / 超时 / 5xx / 非 JSON → RetryableError（尊重 Retry-After），认证/权限错误 → JobFailed；
- 解析用 parse_float=Decimal：供应商的数字文本不经过二进制浮点；
- 「过去会话」的原始响应可按 raw_reuse_policy_v1 复用（已沉淀的历史日不会再变）；当天会话总是重取。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ..data.evidence_store import EvidenceStore
from ..domain import clock
from ..domain.canonical import canonical_json, content_hash, strict_json_loads
from ..jobs.worker import JobFailed, RetryableError
from .shared_hooks import shared_numcat

RAW_REUSE_POLICY = "raw_reuse_policy_v1"
PASS_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "NUMCAT_API_KEY", "NUMCAT_ENV_FILE",
            "SSL_CERT_FILE", "SSL_CERT_DIR")
PROXY_ENV = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")


@dataclass
class RawResponse:
    operation_id: str
    params: dict
    rows: Sequence[dict]
    raw_sha256: str
    captured_at: str
    reused: bool = False
    source_id: str = ""


class NumcatAdapter:
    source_prefix = "numcat"

    def __init__(self, cli: Path, store: EvidenceStore, cache_dir: Path, timeout: float = 90.0,
                 max_retry: int = 2):
        self.cli = Path(cli)
        self.store = store
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.max_retry = max_retry
        self.calls = 0

    def available(self) -> bool:
        query_sync, available = shared_numcat()
        if query_sync is not None:
            return True if available is None else bool(available())
        return self.cli.is_file()

    # ── 底层调用 ──
    def _invoke(self, operation_id: str, apiname: str, fields: str, params: dict) -> bytes:
        query_sync, available = shared_numcat()
        if query_sync is not None:
            if available is not None and not available():
                raise JobFailed("SOURCE_NOT_CONFIGURED", "宿主 NumCat 数据源不可用")
            try:
                envelope = query_sync(apiname, dict(params), fields)
            except JobFailed:
                raise
            except Exception as exc:
                raise RetryableError("SOURCE_UNAVAILABLE", f"{operation_id} 宿主数据源不可用：{type(exc).__name__}") from exc
            if not isinstance(envelope, dict):
                raise RetryableError("SOURCE_UNAVAILABLE", f"{operation_id} 宿主数据源返回格式不可识别")
            # Store the host envelope as evidence without exposing credentials.
            return json.dumps({"code": envelope.get("code", 0), "data": envelope.get("data", envelope)},
                              ensure_ascii=False, default=str).encode("utf-8")
        if not self.available():
            raise JobFailed("SOURCE_NOT_CONFIGURED", "未找到 meoz CLI（MEOZ_CLI），无法从供应商取数")
        req = {"apiname": apiname, "fields": fields, "params": params}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(req, f, ensure_ascii=False)
            req_path = f.name
        try:
            # 只透传 meoz 自己需要的环境：MEOZ_*（API Key / 环境 / 线路，由 meoz 读取，本进程不读）、
            # 代理与 CA 证书设置（国内网络常需要代理），以及基础路径/语言变量
            env = {k: v for k, v in os.environ.items()
                   if k in PASS_ENV or k.startswith("MEOZ_") or k.lower() in PROXY_ENV}
            p = subprocess.run([sys.executable, "-I", str(self.cli), "query", "--operation-id", operation_id,
                                "--request-file", req_path], capture_output=True, timeout=self.timeout,
                               shell=False, env=env)
        except subprocess.TimeoutExpired:
            raise RetryableError("SOURCE_TIMEOUT", f"{operation_id} 超时（{self.timeout}s）")
        finally:
            try:
                os.unlink(req_path)
            except OSError:
                pass
        self.calls += 1
        out = p.stdout or b""
        try:
            doc = strict_json_loads(out)
        except (ValueError, UnicodeDecodeError):
            raise RetryableError("SOURCE_UNAVAILABLE", f"{operation_id} 返回非 JSON（退出码 {p.returncode}）")
        if not isinstance(doc, dict) or not doc.get("results"):
            # meoz 自己的错误（{"ok": false, "error_type": ...}）：按类型给固定说明，不回显原文（原文可能带请求细节）
            et = doc.get("error_type") if isinstance(doc, dict) else None
            if et == "AuthError":
                raise JobFailed("SOURCE_CREDENTIALS_MISSING",
                                "数据源凭据不可用：meoz 没有可用的 API Key。可在终端运行 "
                                "python3 ~/Downloads/meoz-2/scripts/meoz.py configure-key 配置，或设置环境变量 MEOZ_API_KEY")
            if et == "NetworkError":
                raise RetryableError("SOURCE_UNREACHABLE", f"{operation_id} 连不上数据源（网络、代理或线路问题）")
            if et == "UsageError":
                raise JobFailed("SOURCE_REQUEST_REJECTED", f"meoz 不接受这次 {operation_id} 请求（接口文档或参数不一致）")
            msg = json.dumps(doc, ensure_ascii=False, default=str)[:200] if isinstance(doc, dict) else ""
            low = msg.lower()
            if "429" in low or "rate" in low or "频" in msg:
                raise RetryableError("SOURCE_RATE_LIMITED", f"{operation_id} 被限流", retry_after=_retry_after(doc))
            if "auth" in low or "key" in low or "permission" in low or "权限" in msg:
                raise JobFailed("SOURCE_PERMISSION_DENIED", f"{operation_id} 无权限或凭据不可用")
            raise RetryableError("SOURCE_UNAVAILABLE", f"{operation_id} 未返回结果")
        res0 = doc["results"][0]
        code = res0.get("code") if isinstance(res0, dict) else None
        if code not in (None, 200):
            if code == 429:
                raise RetryableError("SOURCE_RATE_LIMITED", f"{operation_id} 被限流", retry_after=_retry_after(res0))
            if code in (401, 403):
                raise JobFailed("SOURCE_PERMISSION_DENIED", f"{operation_id} 无权限（{code}）")
            if code and code >= 500:
                raise RetryableError("SOURCE_UNAVAILABLE", f"{operation_id} 上游错误 {code}")
        return out

    def query(self, operation_id: str, apiname: str, fields: str, params: dict, *,
              past_session_only: bool = False) -> RawResponse:
        key = content_hash({"op": operation_id, "fields": fields, "params": params})
        cache_file = self.cache_dir / f"{key}.json"
        if past_session_only and cache_file.exists():
            meta = json.loads(cache_file.read_text(encoding="utf-8"))
            try:
                raw = self.store.get(meta["raw_sha256"])
                return RawResponse(operation_id, params, _rows(raw, self.cache_dir / "rows" / f"{meta['raw_sha256']}.rows"), meta["raw_sha256"], meta["captured_at"],
                                   reused=True, source_id=f"{self.source_prefix}_{apiname}")
            except (FileNotFoundError, ValueError):
                pass
        attempt = 0
        while True:
            try:
                raw = self._invoke(operation_id, apiname, fields, params)
                break
            except RetryableError as ex:
                if attempt >= self.max_retry:
                    raise
                attempt += 1
                time.sleep(min(ex.retry_after if ex.retry_after is not None else 2 ** attempt, 30))
        captured = clock.utc_iso()
        sha = self.store.put(raw)
        if past_session_only:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(canonical_json({"raw_sha256": sha, "captured_at": captured,
                                                  "operation_id": operation_id, "params": params,
                                                  "policy": RAW_REUSE_POLICY}), encoding="utf-8")
        return RawResponse(operation_id, params, _rows(raw, self.cache_dir / "rows" / f"{sha}.rows"), sha, captured, False, f"{self.source_prefix}_{apiname}")

    # ── 具体数据集 ──
    def tradecal(self, start: str, end: str) -> RawResponse:
        # end_latest=false：供应商默认把 enddate 截到「当前可用最新交易日」，那样今天与未来的休市/开市都拿不到，
        # 日历就无法回答「今天是不是交易日、最近完整交易日是哪天」。交易所年度日历是提前公布的，按文档显式要未来区间。
        return self.query("reference_calendar_range", "tradecal", "",
                          {"startdate": start, "enddate": end, "end_latest": False})

    def stockbasic(self) -> RawResponse:
        return self.query("reference_stock_basic", "stockbasic",
                          "code,symbol,name,industry,market,exchange,list_status,list_date,delist_date", {})

    def st(self, day: str, past: bool) -> RawResponse:
        return self.query("reference_stock_st", "st", "tradedate,symbol,name,type,type_name",
                          {"startdate": day, "enddate": day}, past_session_only=past)

    def suspend(self, day: str, past: bool) -> RawResponse:
        return self.query("reference_stock_suspend", "suspend", "symbol,tradedate,suspend_type,suspend_timing",
                          {"startdate": day, "enddate": day}, past_session_only=past)

    def pricelimit(self, day: str, past: bool) -> RawResponse:
        return self.query("reference_stock_pricelimit", "pricelimit",
                          "tradedate,symbol,name,pre_close,up_limit,down_limit", {"tradedate": day},
                          past_session_only=past)

    def daily(self, day: str, past: bool) -> RawResponse:
        return self.query("reference_stock_daily", "daily",
                          "tradedate,symbol,name,open,high,low,close,pre_close,pct_chg,amount,vol",
                          {"tradedate": day}, past_session_only=past)

    def index_daily(self, symbols: list[str], start: str, end: str, past: bool) -> RawResponse:
        return self.query("reference_index_indexdaily", "indexdaily",
                          "tradedate,symbol,name,open,high,low,close,pre_close,pct_chg,amount",
                          {"symbols": symbols, "startdate": start, "enddate": end}, past_session_only=past)

    def valuation(self, day: str, past: bool) -> RawResponse:
        return self.query("reference_stock_valuation", "valuation",
                          "tradedate,symbol,name,close,turnover_rate,pe_ttm,pb,dv_ttm,total_mv,circ_mv",
                          {"tradedate": day}, past_session_only=past)

    def limit_events(self, day: str, past: bool) -> RawResponse:
        return self.query("reference_stock_limit_event_v2_history", "limit_event_v2_history",
                          "tradedate,symbol,name,time,event,servertime,last_price,limit_price,fd_amount,limit_times,open_times,turnover_rate",
                          {"tradedate": day, "event_types": [1, 2, 3, 4]}, past_session_only=past)


def _retry_after(doc) -> float | None:
    if isinstance(doc, dict):
        for k in ("retry_after", "Retry-After", "retryAfter"):
            v = doc.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
    return None


def _jsonable(value):
    if isinstance(value, Decimal):
        return {"__roundtable_decimal__": str(value)}
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _restore(value):
    if isinstance(value, dict):
        if set(value) == {"__roundtable_decimal__"}:
            return Decimal(value["__roundtable_decimal__"])
        return {k: _restore(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_restore(v) for v in value]
    return value


class DiskRows(Sequence[dict]):
    """Content-addressed JSONL rows; only one row is resident per read."""
    def __init__(self, path: Path, count: int):
        self.path, self.count = Path(path), int(count)

    def __len__(self):
        return self.count

    def __iter__(self):
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield _restore(json.loads(line))

    def __getitem__(self, index):
        if isinstance(index, slice):
            return list(self)[index]
        index = int(index)
        if index < 0:
            index += self.count
        if index < 0 or index >= self.count:
            raise IndexError(index)
        with self.path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == index:
                    return _restore(json.loads(line))
        raise IndexError(index)


def _rows(raw: bytes, path: Path) -> DiskRows:
    doc = strict_json_loads(raw)
    if isinstance(doc, list):
        data = {"items": doc}
    elif isinstance(doc.get("results"), list):
        res = doc["results"][0]
        data = res.get("data") or {}
    else:
        data = doc.get("data") or {}
    fields = data.get("fields") or []
    items = data.get("items") or data.get("rows") or data.get("data") or []
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    fd, tmp = tempfile.mkstemp(prefix=".rows-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for item in items:
                row = dict(zip(fields, item)) if isinstance(item, (list, tuple)) else dict(item)
                f.write(json.dumps(_jsonable(row), ensure_ascii=False, separators=(",", ":")) + "\n")
                count += 1
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return DiskRows(path, count)


def dec(v) -> Decimal | None:
    """供应商数值（已经是 Decimal 或 int 或字符串）→ Decimal；None/空 → None。"""
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return v if v.is_finite() else None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, str):
        try:
            d = Decimal(v)
            return d if d.is_finite() else None
        except Exception:
            return None
    if isinstance(v, float):     # 旧缓存文件里的 JSON 数字（json.load 默认 float）
        return Decimal(repr(v))
    return None
