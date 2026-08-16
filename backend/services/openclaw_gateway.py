"""Allowlisted, stateless MCP gateway for OpenClaw.

The gateway intentionally exposes application capabilities instead of an
arbitrary route or code executor. Every tool is read-only unless its name
explicitly describes a small, validated application action.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime
from typing import Any, Awaitable, Callable

from config import settings
from quant.market_cache import load_quant_market_snapshot
from services.data_collector import collector, normalize_stock_code
from services.dragon_board import dragon_board_service
from services.flow_analysis import flow_analysis_service
from services.mao_strategy_agent import mao_strategy_agent
from services.macro_dashboard import macro_dashboard_service
from services.midday_research import midday_research_service
from services.openclaw_database import query_system_database
from services.overnight_strategy import overnight_strategy_service
from services.personal_portfolio import personal_portfolio_service
from services.stock_selection_agents import stock_selection_agents
from services.technical_screener import technical_screener_service


def _int_arg(arguments: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(arguments.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是整数") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


async def _market_snapshot(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        live = await collector.fetch_quant_market_snapshot()
        return {"data": live, "cache_used": False}
    except Exception as exc:
        cached = await load_quant_market_snapshot()
        return {
            "data": cached,
            "cache_used": True,
            "live_error": type(exc).__name__,
        }


async def _stock_quote(arguments: dict[str, Any]) -> dict[str, Any]:
    raw_codes = arguments.get("stock_codes") or arguments.get("codes")
    if isinstance(raw_codes, str):
        raw_codes = [item.strip() for item in raw_codes.split(",") if item.strip()]
    if not isinstance(raw_codes, list) or not raw_codes:
        raw_codes = [arguments.get("stock_code") or arguments.get("code")]
    if len(raw_codes) > 100:
        raise ValueError("一次最多查询100只股票")
    codes = list(dict.fromkeys(normalize_stock_code(item) for item in raw_codes if item))
    if not codes:
        raise ValueError("stock_code 或 stock_codes 不能为空")
    return await collector.fetch_stock_quotes(codes)


async def _stock_history(arguments: dict[str, Any]) -> dict[str, Any]:
    code = normalize_stock_code(arguments.get("stock_code") or arguments.get("code"))
    days = _int_arg(arguments, "days", 365, 1, 365)
    return await collector.fetch_stock_price_history(code, days)


async def _smart_selection(arguments: dict[str, Any]) -> dict[str, Any]:
    mode = str(arguments.get("mode") or "quick").strip().lower()
    risk_profile = str(arguments.get("risk_profile") or "balanced").strip().lower()
    horizon = str(arguments.get("horizon") or "week").strip().lower()
    top_n = _int_arg(arguments, "top_n", 5, 3, 10)
    sector = arguments.get("sector")
    sector_code = arguments.get("sector_code")
    if sector is not None and not isinstance(sector, str):
        raise ValueError("sector 必须是字符串")
    if sector_code:
        sector_code = str(sector_code).strip().upper()
    factor_filters = arguments.get("factor_filters")
    if factor_filters is not None and not isinstance(factor_filters, dict):
        raise ValueError("factor_filters 必须是对象")
    return await stock_selection_agents.run(
        mode=mode,
        risk_profile=risk_profile,
        top_n=top_n,
        sector=sector.strip() if isinstance(sector, str) else None,
        sector_code=sector_code,
        horizon=horizon,
        factor_filters=factor_filters,
    )


async def _technical_screen(arguments: dict[str, Any]) -> dict[str, Any]:
    criteria = arguments.get("criteria")
    if criteria is None:
        criteria = {key: value for key, value in arguments.items() if key != "criteria"}
    if not isinstance(criteria, dict):
        raise ValueError("criteria 必须是对象")
    return await technical_screener_service.run(criteria)


async def _overnight_dashboard(arguments: dict[str, Any]) -> dict[str, Any]:
    return await overnight_strategy_service.dashboard()


async def _overnight_run(arguments: dict[str, Any]) -> dict[str, Any]:
    stage = str(arguments.get("stage") or "preliminary").strip().lower()
    if stage not in {"preliminary", "entry", "auction", "exit", "force_exit"}:
        raise ValueError("stage 仅支持 preliminary、entry、auction、exit、force_exit")
    strategy_id = str(arguments.get("strategy_id") or "").strip() or None
    return await overnight_strategy_service.start(
        stage, trigger="openclaw", background=True, strategy_id=strategy_id,
    )


async def _personal_pool(arguments: dict[str, Any]) -> dict[str, Any]:
    return await personal_portfolio_service.overview()


async def _add_personal_stock(arguments: dict[str, Any]) -> dict[str, Any]:
    code = normalize_stock_code(arguments.get("stock_code") or arguments.get("code"))
    payload = {
        "code": code,
        "name": str(arguments.get("name") or code).strip(),
        "pool": str(arguments.get("pool") or "watchlist").strip().lower(),
        "industry": str(arguments.get("industry") or arguments.get("sector") or "").strip(),
        "thesis": str(arguments.get("thesis") or "OpenClaw 调用系统分析工具后加入").strip(),
        "source": "openclaw",
    }
    return await personal_portfolio_service.create_item(payload)


async def _flow_analysis(arguments: dict[str, Any]) -> dict[str, Any]:
    board_type = str(arguments.get("board_type") or "industry").strip().lower()
    window = str(arguments.get("window") or "week").strip().lower()
    return await flow_analysis_service.analyze(board_type, window)


async def _dragon_analysis(arguments: dict[str, Any]) -> dict[str, Any]:
    window = str(arguments.get("window") or "week").strip().lower()
    return await dragon_board_service.analyze(window)


async def _macro_dashboard(arguments: dict[str, Any]) -> dict[str, Any]:
    return await macro_dashboard_service.dashboard()


async def _mao_strategy(arguments: dict[str, Any]) -> dict[str, Any]:
    message = str(arguments.get("message") or "").strip()
    stock_code = arguments.get("stock_code") or arguments.get("code")
    if stock_code:
        code = normalize_stock_code(stock_code)
        if not message:
            message = f"请对{code}做毛选战略研判"
        elif code not in message:
            message = f"{message}（标的：{code}）"
    if not message:
        message = "请对当前A股市场做毛选战略研判"
    if len(message) > 4000:
        raise ValueError("message不能超过4000字")
    return await mao_strategy_agent.analyze(message)


async def _data_source_health(arguments: dict[str, Any]) -> dict[str, Any]:
    return await collector.check_data_source()


async def _midday_research(arguments: dict[str, Any]) -> dict[str, Any]:
    session_id = str(arguments.get("session_id") or "").strip()
    result = await midday_research_service.get(session_id) if session_id else await midday_research_service.latest()
    if result is None:
        return {"available": False, "message": "尚无午间研究记录"}
    return result


async def _run_midday_research(arguments: dict[str, Any]) -> dict[str, Any]:
    force = arguments.get("force", False)
    if not isinstance(force, bool):
        raise ValueError("force 必须是布尔值")
    return await midday_research_service.start(force=force, background=True)


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_market_snapshot",
        "description": "读取完整A股行情快照；实时失败时明确返回已验证缓存及失败原因。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_stock_quote",
        "description": "查询一只或多只A股实时/最近行情，返回代码、名称、价格、更新时间和数据源。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "stock_code": {"type": "string", "description": "六位A股代码，例如600519"},
                "stock_codes": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_stock_history",
        "description": "读取指定A股近1至365个自然日的日线历史；缺失字段保持为空。",
        "inputSchema": {"type": "object", "required": ["stock_code"], "properties": {"stock_code": {"type": "string"}, "days": {"type": "integer", "minimum": 1, "maximum": 365}}, "additionalProperties": False},
    },
    {
        "name": "run_smart_stock_selection",
        "description": "运行系统智能选股Agent，支持短期/半月/月度周期、板块和因子筛选，并保留数据审计。",
        "inputSchema": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["quick", "full"]}, "risk_profile": {"type": "string", "enum": ["conservative", "balanced", "aggressive"]}, "horizon": {"type": "string", "enum": ["week", "half_month", "month"]}, "top_n": {"type": "integer", "minimum": 3, "maximum": 10}, "sector": {"type": "string"}, "sector_code": {"type": "string"}, "factor_filters": {"type": "object"}}, "additionalProperties": False},
    },
    {
        "name": "run_technical_screen",
        "description": "运行短线或长线技术筛选器；实时不可用时返回缓存标记，不伪造实时结果。",
        "inputSchema": {"type": "object", "properties": {"criteria": {"type": "object"}, "preset": {"type": "string", "enum": ["basic", "short", "long", "custom"]}}, "additionalProperties": True},
    },
    {
        "name": "get_macro_dashboard",
        "description": "读取国际经济、国内政策、A股行情和可解释综合方向分析。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "analyze_mao_strategy",
        "description": "按主要矛盾、资金阵营、周期阶段、战术红线和闭环复盘分析A股或指定个股；缺数据时只返回观察结论。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "maxLength": 4000},
                "stock_code": {"type": "string", "description": "可选的六位A股代码"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "analyze_fund_flow",
        "description": "分析行业或概念板块近一周、两周或一个月资金流向，并返回AI/规则分析。",
        "inputSchema": {"type": "object", "properties": {"board_type": {"type": "string", "enum": ["industry", "concept"]}, "window": {"type": "string", "enum": ["week", "two_weeks", "month"]}}, "additionalProperties": False},
    },
    {
        "name": "analyze_dragon_board",
        "description": "读取缓存龙虎榜历史并按时间窗口分析净买卖、重复上榜和机构席位。",
        "inputSchema": {"type": "object", "properties": {"window": {"type": "string", "enum": ["week", "two_weeks", "month"]}}, "additionalProperties": False},
    },
    {
        "name": "get_overnight_dashboard",
        "description": "读取一夜持股新旧策略、尾盘候选、竞价盯盘、模拟持仓和真实前向盈亏。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "run_overnight_strategy",
        "description": "提交一夜持股的预扫、尾盘复核、竞价盯盘或退出任务；只做模拟交易，不连接券商。",
        "inputSchema": {"type": "object", "required": ["stage"], "properties": {"stage": {"type": "string", "enum": ["preliminary", "entry", "auction", "exit", "force_exit"]}, "strategy_id": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "name": "get_personal_pool",
        "description": "读取个人股票池、实时盈亏、风险检查和告警。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "add_to_personal_pool",
        "description": "将经OpenClaw分析的A股加入个人观察池；操作幂等，不执行真实交易。",
        "inputSchema": {"type": "object", "required": ["stock_code"], "properties": {"stock_code": {"type": "string"}, "name": {"type": "string"}, "pool": {"type": "string", "enum": ["core", "watchlist", "leaders", "etf", "blacklist"]}, "industry": {"type": "string"}, "thesis": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "name": "check_data_source",
        "description": "检查国内行情代理和上游行情源是否可用，并返回延迟。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_midday_research",
        "description": "读取最新或指定午间AI研究，包含上午尸检、主要矛盾、板块结构、Alpha/Beta异常、下午情景和14:55预演。",
        "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "name": "run_midday_research",
        "description": "发起午间AI战术研究任务；仅做研究和模拟筛选，不执行真实交易。",
        "inputSchema": {"type": "object", "properties": {"force": {"type": "boolean", "default": False}}, "additionalProperties": False},
    },
    {
        "name": "query_system_database",
        "description": "只读查询系统生产数据库中的白名单数据集，支持历史行情、资金流、选股运行、量化策略和一夜持仓；不接受任意SQL。",
        "inputSchema": {
            "type": "object",
            "required": ["dataset"],
            "properties": {
                "dataset": {
                    "type": "string",
                    "enum": [
                        "stock_daily_bars", "overnight_runs", "overnight_positions",
                        "stock_selection_runs", "quant_strategies", "market_flow",
                        "market_sentiment_daily", "security_master", "security_status_events",
                        "stock_valuation_history",
                    ],
                },
                "fields": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
                "offset": {"type": "integer", "minimum": 0, "maximum": 10000, "default": 0},
                "sort_by": {"type": "string"},
                "sort_order": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
                "stock_code": {"type": "string", "description": "股票六位代码；仅适用于日线和一夜持仓"},
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "market": {"type": "string"},
                "source": {"type": "string"},
                "status": {"type": "string"},
                "stage": {"type": "string"},
                "mode": {"type": "string"},
                "risk_profile": {"type": "string"},
                "is_realtime": {"type": "boolean"},
                "is_builtin": {"type": "boolean"},
                "strategy_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
]


HANDLERS: dict[str, ToolHandler] = {
    "get_market_snapshot": _market_snapshot,
    "get_stock_quote": _stock_quote,
    "get_stock_history": _stock_history,
    "run_smart_stock_selection": _smart_selection,
    "run_technical_screen": _technical_screen,
    "get_macro_dashboard": _macro_dashboard,
    "analyze_mao_strategy": _mao_strategy,
    "analyze_fund_flow": _flow_analysis,
    "analyze_dragon_board": _dragon_analysis,
    "get_overnight_dashboard": _overnight_dashboard,
    "run_overnight_strategy": _overnight_run,
    "get_personal_pool": _personal_pool,
    "add_to_personal_pool": _add_personal_stock,
    "check_data_source": _data_source_health,
    "get_midday_research": _midday_research,
    "run_midday_research": _run_midday_research,
    "query_system_database": query_system_database,
}


class OpenClawGateway:
    protocol_version = "2024-11-05"

    @staticmethod
    def manifest() -> dict[str, Any]:
        return {
            "name": "ai-buffett-openclaw",
            "version": "1.3.0",
            "protocol": "MCP JSON-RPC 2.0 over stateless HTTP",
            "endpoint": "/api/v1/openclaw/mcp",
            "enabled": bool(settings.openclaw_enabled),
            "authentication": "Authorization: Bearer <OPENCLAW_API_KEY> or X-OpenClaw-Key",
            "tools": TOOL_DEFINITIONS,
            "safety": [
                "只暴露白名单系统工具",
                "股票代码经过六位代码和交易所前缀校验",
                "不提供任意SQL、shell、Python或券商下单能力",
                "数据库仅通过白名单数据集、字段、过滤条件和分页只读查询开放",
                "实时不可用时工具返回cache_used/is_realtime，不把缓存冒充实时",
            ],
        }

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        handler = HANDLERS.get(str(name or "").strip())
        if handler is None:
            raise ValueError(f"未知工具: {name}")
        timeout = min(max(float(settings.openclaw_tool_timeout), 5.0), 120.0)
        result = await asyncio.wait_for(handler(arguments or {}), timeout=timeout)
        return _json_safe(result)

    @staticmethod
    def _error(code: int, message: str, request_id: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    async def handle_rpc(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return self._error(-32600, "请求必须是JSON-RPC 2.0对象", request.get("id") if isinstance(request, dict) else None)
        request_id = request.get("id")
        method = str(request.get("method") or "")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "ai-buffett-openclaw", "version": "1.3.0"},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOL_DEFINITIONS}}
        if method == "tools/call":
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            try:
                result = await self.call_tool(str(params.get("name") or ""), params.get("arguments") or {})
            except asyncio.TimeoutError:
                return {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"isError": True, "content": [{"type": "text", "text": "工具执行超时，系统未返回不完整结果"}]},
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"isError": True, "content": [{"type": "text", "text": f"工具执行失败: {type(exc).__name__}: {exc}"}]},
                }
            import json
            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "isError": False,
                    "structuredContent": result,
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                },
            }
        return self._error(-32601, f"不支持的MCP方法: {method}", request_id)


openclaw_gateway = OpenClawGateway()
