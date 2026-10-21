"""只读数据桥（ADR-013 / ADR-014）：总指挥按**接口目录**查猫爪（Numcat）的数据。

目录来自 `data_catalog.json`（由 `tools/gen_data_catalog.py` 从猫爪官方 OpenAPI 生成，94 个接口，89 个可查）：
- 模型只能选「查哪个接口、传什么参数」，不能自己拼操作 id、字段或请求形状；
- 参数只接受目录里声明过的那些，类型、日期格式、代码列表都做校验与归一；
- 订阅类（stream）与文件下载类（download）不在查询白名单里：前者要长连接，后者要落盘，都超出「聊天里查一下」的范围；
- 凭据只由 meoz CLI 自己读（本模块不读、不传、不打印）；失败原样返回原因，不编数据；
- 60 秒内存缓存，一次对话里反复问同一件事不会反复打供应商。

调用方式：`query(settings, op_or_alias, params)`；别名见 `ALIASES`（index_daily / valuation / margin / northbound / …）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from ..domain import clock
from ..adapters.shared_hooks import configure_shared_numcat, shared_numcat

CACHE_TTL = 60.0
MAX_ROWS = 40
MAX_SYMBOLS = 20
MAX_FIELD_CHARS = 600
MAX_PARAM_CHARS = 200
PASS_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR")
DATE_KEYS = ("tradedate", "startdate", "enddate", "date", "ann_date", "end_date", "start_date", "trade_date")
SYMBOL_KEYS = ("symbols", "symbol", "ts_code", "codes")
CLAMP_KEYS = {"recentdays": 60, "limit": 200, "top": 100, "count": 100, "page_size": 200}

ALIASES = {
    "index_daily": "reference_index_indexdaily",
    "valuation": "reference_stock_valuation",
    "limit_events": "reference_stock_limit_event_v2_history",
    "calendar": "reference_calendar_range",
    "margin": "reference_margin_summary",
    "margin_detail": "reference_margin_detail",
    "northbound": "reference_fund-flow_northbound",
    "northbound_top10": "reference_fund-flow_northbound-top10",
    "theme_fundflow": "reference_theme_themefundflow-jx",
    "emotion": "reference_market_emoindic-daily",
    "limit_pool": "reference_stock_limit-pool",
    "daily": "reference_stock_daily",
    "basic": "reference_stock_basic",
    "industry": "reference_theme_industry",
    "longhubang": "reference_longhubang_stock",
}

_catalog_cache: dict | None = None
_query_cache: dict[str, tuple[float, dict]] = {}


# ── 目录 ──
def catalog() -> dict:
    global _catalog_cache
    if _catalog_cache is None:
        p = Path(__file__).with_name("data_catalog.json")
        _catalog_cache = json.loads(p.read_text(encoding="utf-8"))
    return _catalog_cache


def ops() -> dict[str, dict]:
    return {o["op"]: o for o in catalog()["ops"]}


def resolve(op_or_alias: str) -> dict | None:
    key = str(op_or_alias or "").strip()
    key = ALIASES.get(key, key)
    return ops().get(key)


def list_ops(tag: str | None = None, query: str | None = None) -> list[dict]:
    """列出可查询的接口（不含订阅与下载）。"""
    out = []
    q = (query or "").strip().lower()
    for o in catalog()["ops"]:
        if o.get("stream") or o.get("download"):
            continue
        if tag and o.get("tag") != tag:
            continue
        if q and q not in (o.get("summary") or "").lower() and q not in o["op"].lower() and q not in (o.get("tag") or "").lower():
            continue
        out.append({"op": o["op"], "tag": o.get("tag"), "summary": o.get("summary"),
                    "params": list((o.get("params") or {}).keys())[:8]})
    return out


def tags() -> dict[str, int]:
    out: dict[str, int] = {}
    for o in catalog()["ops"]:
        if o.get("stream") or o.get("download"):
            continue
        out[o.get("tag") or "未分类"] = out.get(o.get("tag") or "未分类", 0) + 1
    return out


def available(settings) -> bool:
    query_sync, available_cb = shared_numcat()
    if query_sync is not None:
        return True if available_cb is None else bool(available_cb())
    return bool(getattr(settings, "meoz_cli", None)) and Path(settings.meoz_cli).is_file()


# ── 参数校验 ──
def _norm_date(v) -> str:
    s = str(v).strip()
    m = re.match(r"^(\d{4})[-/.]?(\d{2})[-/.]?(\d{2})$", s)
    if not m:
        raise ValueError(f"日期格式不对：{s}（要 YYYYMMDD）")
    out = f"{m.group(1)}{m.group(2)}{m.group(3)}"
    try:
        datetime.strptime(out, "%Y%m%d")
    except ValueError:
        raise ValueError(f"日期不存在：{s}") from None
    return out


def _norm_symbol(v) -> str:
    s = str(v).strip().upper()
    m = re.match(r"^(?:(SH|SZ|BJ)\.?)?(\d{6})(?:\.(SH|SZ|BJ))?$", s)
    if not m:
        raise ValueError(f"代码格式不对：{s}（要 6 位代码，可带 .SH/.SZ/.BJ）")
    return m.group(2)


def clean_params(spec: dict, params: dict | None) -> dict:
    """只留下目录里声明过的参数，并按声明类型归一。"""
    declared = spec.get("params") or {}
    raw = params or {}
    if not isinstance(raw, dict):
        raise ValueError("params 必须是对象")
    unknown = [k for k in raw if k not in declared]
    if unknown:
        raise ValueError(f"接口 {spec['op']} 不认识这些参数：{'、'.join(unknown[:5])}；可用：{'、'.join(list(declared)[:8]) or '无'}")
    out: dict = {}
    for k, meta in declared.items():
        if k not in raw:
            if meta.get("required"):
                raise ValueError(f"接口 {spec['op']} 缺少必填参数：{k}")
            continue
        v = raw[k]
        if v is None or v == "":
            continue
        typ = (meta.get("type") or "string").lower()
        if k in SYMBOL_KEYS:
            vals = v if isinstance(v, list) else [v]
            out[k] = [_norm_symbol(x) for x in vals][:MAX_SYMBOLS] if isinstance(v, list) else _norm_symbol(v)
        elif k in DATE_KEYS or k.endswith("date"):
            out[k] = _norm_date(v)
        elif "int" in typ:
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} 要是整数")
            cap = CLAMP_KEYS.get(k)
            out[k] = max(1, min(n, cap)) if cap else n
        elif "array" in typ or typ.startswith("list"):
            vals = v if isinstance(v, list) else [v]
            if len(vals) > 50:
                raise ValueError(f"{k} 最多 50 个")
            out[k] = [str(x)[:MAX_PARAM_CHARS] for x in vals]
        elif "bool" in typ:
            out[k] = bool(v) if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes")
        else:
            out[k] = str(v)[:MAX_PARAM_CHARS]
    return out


# ── 调用 ──
def _invoke(settings, spec: dict, params: dict, timeout: float = 25.0) -> list[dict]:
    query_sync, available_cb = shared_numcat()
    if query_sync is not None:
        if available_cb is not None and not available_cb():
            raise RuntimeError("宿主 NumCat 数据源不可用")
        envelope = query_sync(spec["apiname"], dict(params), spec.get("fields") or "")
        if not isinstance(envelope, dict):
            raise RuntimeError("宿主 NumCat 返回格式不可识别")
        data = envelope.get("data") or envelope
        fields = data.get("fields") or [] if isinstance(data, dict) else []
        items = data.get("items") or data.get("rows") or data.get("data") or [] if isinstance(data, dict) else []
        return [dict(zip(fields, row)) if isinstance(row, (list, tuple)) else dict(row)
                for row in items]
    cli = Path(settings.meoz_cli)
    req = {"apiname": spec["apiname"], "fields": spec.get("fields") or "", "params": params}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False)
        req_path = f.name
    try:
        env = {k: v for k, v in os.environ.items() if k in PASS_ENV or k.startswith("MEOZ_")
               or k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
        p = subprocess.run([sys.executable, "-I", str(cli), "query", "--operation-id", spec["op"],
                            "--request-file", req_path], capture_output=True, timeout=timeout,
                           shell=False, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"查询超时（{timeout:.0f}s）")
    finally:
        try:
            os.unlink(req_path)
        except OSError:
            pass
    try:
        doc = json.loads(p.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise RuntimeError(f"供应商返回不是 JSON：{(p.stdout or b'')[:120]!r} {(p.stderr or b'')[:120]!r}")
    results = doc.get("results") or []
    if not results:
        raise RuntimeError(str(doc.get("message") or doc.get("error") or "供应商没有返回结果")[:200])
    data = results[0].get("data") or {}
    fields = data.get("fields") or []
    return [dict(zip(fields, row)) for row in data.get("items") or []]


def query(settings, op_or_alias: str, params: dict | None = None) -> dict:
    """按目录白名单查询。返回 {ok, op, label, rows, count, captured_at, note} 或 {ok:false, reason}。"""
    spec = resolve(op_or_alias)
    if spec is None:
        return {"ok": False, "reason": f"目录里没有这个接口：{op_or_alias}（用 list_data_api 看有哪些）"}
    if spec.get("stream"):
        return {"ok": False, "reason": f"{spec['summary']} 是订阅接口（长连接），不在聊天查询范围"}
    if spec.get("download"):
        return {"ok": False, "reason": f"{spec['summary']} 是文件下载接口，不在聊天查询范围"}
    try:
        clean = clean_params(spec, params)
    except ValueError as ex:
        return {"ok": False, "reason": str(ex), "op": spec["op"]}
    if not available(settings):
        reason = ("宿主 NumCat 数据源不可用：查不了供应商数据；可以先用快照/知识库回答"
                  if shared_numcat()[0] is not None else
                  "没找到 meoz CLI（MEOZ_CLI）：查不了供应商数据；可以先用快照/知识库回答")
        return {"ok": False, "reason": reason,
                "op": spec["op"]}
    key = json.dumps({"op": spec["op"], "p": clean}, ensure_ascii=False, sort_keys=True)
    hit = _query_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < CACHE_TTL:
        return dict(hit[1], cached=True)
    try:
        rows = _invoke(settings, spec, clean)
    except Exception as ex:  # 网络/凭据/超时：如实说
        return {"ok": False, "reason": f"{spec['summary']} 查询失败：{str(ex)[:200]}", "op": spec["op"]}
    out = {"ok": True, "op": spec["op"], "apiname": spec.get("apiname"), "label": spec.get("summary"),
           "tag": spec.get("tag"), "params": clean, "rows": rows[:MAX_ROWS], "count": len(rows),
           "captured_at": clock.utc_iso(),
           "note": "供应商数据（猫爪），未做二次核验；以返回的 tradedate 为准"}
    _query_cache[key] = (now, out)
    return out
