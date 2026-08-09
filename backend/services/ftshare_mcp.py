"""Minimal Streamable HTTP client for the public FTShare MCP gateway.

The application uses this only as a fallback source. Keeping the protocol
client here avoids adding an MCP runtime dependency to the production API.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from config import settings


class FTShareMCPError(RuntimeError):
    """Raised when the optional FTShare data source cannot return valid data."""


class FTShareMCPClient:
    _DEFAULT_URL = "https://market.ft.tech/gateway/mcp"
    _DEFAULT_PROTOCOL_VERSION = "2025-03-26"
    _DOCUMENT_BASE_URL = "https://market.ft.tech/api/v1/market/data/announcements/stock-announcements"
    _STOCK_CODE_RE = re.compile(r"^\d{6}$")
    _URL_HASH_RE = re.compile(r"^[0-9a-f]{32,128}$")

    @staticmethod
    def _enabled() -> bool:
        return bool(settings.ftshare_mcp_enabled)

    @staticmethod
    def _timeout() -> float:
        try:
            configured = float(settings.ftshare_mcp_timeout)
        except (TypeError, ValueError):
            configured = 10.0
        return min(max(configured, 2.0), 20.0)

    @classmethod
    def _url(cls) -> str:
        value = str(settings.ftshare_mcp_url or cls._DEFAULT_URL).strip()
        if not value.startswith("https://"):
            raise FTShareMCPError("FTShare MCP URL must use HTTPS")
        return value.rstrip("/")

    @staticmethod
    def _proxy_url() -> str | None:
        """Use the regional data proxy when one is configured for market data."""
        base_url = str(settings.data_proxy_base_url or "").strip().rstrip("/")
        if not base_url:
            return None
        if not base_url.startswith("https://"):
            raise FTShareMCPError("FTShare MCP data proxy URL must use HTTPS")
        return f"{base_url}/ftshare-mcp"

    @staticmethod
    def _parse_rpc_response(payload: str) -> dict[str, Any]:
        """Extract one JSON-RPC message from JSON or a Streamable HTTP SSE body."""
        body = payload.strip()
        if body.startswith("{"):
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                return parsed

        for line in body.splitlines():
            if not line.startswith("data:"):
                continue
            candidate = line[5:].strip()
            if candidate.startswith("{"):
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
        raise FTShareMCPError("FTShare MCP returned an unreadable protocol response")

    @staticmethod
    def _rpc_error(message: dict[str, Any]) -> str | None:
        error = message.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "unknown MCP error")
        return None

    async def _send(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        proxy_url = self._proxy_url()
        if proxy_url:
            proxy_headers: dict[str, str] = {}
            if settings.data_proxy_token:
                proxy_headers["X-Data-Proxy-Token"] = settings.data_proxy_token
            response = await client.post(
                proxy_url,
                json={"payload": payload, "headers": headers},
                headers=proxy_headers,
            )
        else:
            response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response

    async def _post(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[dict[str, Any], httpx.Headers]:
        response = await self._send(client, url, payload, headers)
        message = self._parse_rpc_response(response.text)
        error = self._rpc_error(message)
        if error:
            raise FTShareMCPError(f"FTShare MCP error: {error}")
        return message, response.headers

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one documented, read-only FTShare MCP tool and return its data envelope."""
        if not self._enabled():
            raise FTShareMCPError("FTShare MCP fallback is disabled")

        url = self._url()
        common_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        initialize_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": self._DEFAULT_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ai-buffett-backend", "version": "1.0"},
            },
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout()), follow_redirects=True) as client:
            initialized, response_headers = await self._post(client, url, initialize_payload, common_headers)
            session_id = response_headers.get("Mcp-Session-Id")
            if not session_id:
                raise FTShareMCPError("FTShare MCP did not return a session ID")

            initialize_result = initialized.get("result")
            protocol_version = (
                str(initialize_result.get("protocolVersion") or self._DEFAULT_PROTOCOL_VERSION)
                if isinstance(initialize_result, dict)
                else self._DEFAULT_PROTOCOL_VERSION
            )
            session_headers = {
                **common_headers,
                "Mcp-Session-Id": session_id,
                "MCP-Protocol-Version": protocol_version,
            }
            notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            await self._send(client, url, notification, session_headers)
            called, _ = await self._post(
                client,
                url,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                session_headers,
            )

        return self._extract_tool_result(called, name)

    @staticmethod
    def _extract_tool_result(called: dict[str, Any], name: str) -> dict[str, Any]:
        result = called.get("result")
        if not isinstance(result, dict):
            raise FTShareMCPError("FTShare MCP tool response is missing a result")
        if result.get("isError"):
            raise FTShareMCPError(f"FTShare MCP tool {name} reported an upstream error")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured

        content = result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            raw_text = content[0].get("text")
            if isinstance(raw_text, str):
                parsed = json.loads(raw_text)
                if isinstance(parsed, dict):
                    return parsed
        raise FTShareMCPError(f"FTShare MCP tool {name} returned no structured data")

    async def call_paginated_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        page_size: int = 90,
        concurrency: int = 4,
        max_pages: int = 2_000,
    ) -> dict[str, Any]:
        """Read every page in one bounded MCP session.

        FTShare truncates large tool responses by serialized size. A page size
        below that limit keeps every row while a small amount of concurrency
        makes historical backfills practical on Render.
        """
        if not self._enabled():
            raise FTShareMCPError("FTShare MCP fallback is disabled")

        normalized_page_size = min(max(int(page_size), 1), 90)
        normalized_concurrency = min(max(int(concurrency), 1), 6)
        url = self._url()
        common_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        initialize_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": self._DEFAULT_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ai-buffett-backend", "version": "1.0"},
            },
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout()), follow_redirects=True) as client:
            initialized, response_headers = await self._post(client, url, initialize_payload, common_headers)
            session_id = response_headers.get("Mcp-Session-Id")
            if not session_id:
                raise FTShareMCPError("FTShare MCP did not return a session ID")

            initialize_result = initialized.get("result")
            protocol_version = (
                str(initialize_result.get("protocolVersion") or self._DEFAULT_PROTOCOL_VERSION)
                if isinstance(initialize_result, dict)
                else self._DEFAULT_PROTOCOL_VERSION
            )
            session_headers = {
                **common_headers,
                "Mcp-Session-Id": session_id,
                "MCP-Protocol-Version": protocol_version,
            }
            notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            await self._send(client, url, notification, session_headers)

            async def fetch_page(page: int) -> dict[str, Any]:
                payload = {
                    "jsonrpc": "2.0",
                    "id": page + 1,
                    "method": "tools/call",
                    "params": {
                        "name": name,
                        "arguments": {
                            **arguments,
                            "page": page,
                            "page_size": normalized_page_size,
                        },
                    },
                }
                last_error: Exception | None = None
                for attempt in range(3):
                    try:
                        called, _ = await self._post(client, url, payload, session_headers)
                        result = self._extract_tool_result(called, name)
                        metadata = result.get("metadata") if isinstance(result, dict) else None
                        if isinstance(metadata, dict) and metadata.get("truncated"):
                            raise FTShareMCPError(f"FTShare MCP tool {name} truncated page {page}")
                        return result
                    except (httpx.HTTPError, FTShareMCPError) as exc:
                        last_error = exc
                        if attempt < 2:
                            await asyncio.sleep(0.35 * (attempt + 1))
                raise FTShareMCPError(f"FTShare MCP tool {name} failed on page {page}") from last_error

            first = await fetch_page(1)
            first_metadata = first.get("metadata") if isinstance(first, dict) else {}
            pagination = first_metadata.get("pagination") if isinstance(first_metadata, dict) else {}
            try:
                pages = int(pagination.get("pages") or 0)
            except (TypeError, ValueError):
                pages = 0
            if pages > max_pages:
                raise FTShareMCPError(f"FTShare MCP tool {name} exceeded the page safety limit")

            rows = list(first.get("data") or [])
            for start in range(2, pages + 1, normalized_concurrency):
                batch = await asyncio.gather(
                    *(fetch_page(page) for page in range(start, min(start + normalized_concurrency, pages + 1)))
                )
                for result in batch:
                    rows.extend(result.get("data") or [])

        return {
            "data": [item for item in rows if isinstance(item, dict)],
            "metadata": {
                **(first_metadata if isinstance(first_metadata, dict) else {}),
                "returned": len(rows),
                "truncated": False,
            },
        }

    @classmethod
    def stock_symbol(cls, stock_code: str) -> str:
        code = str(stock_code or "").strip()
        if not cls._STOCK_CODE_RE.fullmatch(code):
            raise FTShareMCPError("FTShare MCP requires a six-digit A-share code")
        if code.startswith(("4", "8", "92")):
            return f"{code}.BJ"
        if code.startswith(("6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"

    async def get_daily_ohlc(self, stock_code: str, limit: int) -> list[dict[str, Any]]:
        result = await self.call_tool(
            "daily_ohlc",
            {
                "type": "stock",
                "symbol": self.stock_symbol(stock_code),
                "limit": min(max(int(limit), 1), 500),
            },
        )
        data = result.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def get_stock_announcements(self, stock_code: str, page_size: int = 6) -> list[dict[str, Any]]:
        result = await self.call_tool(
            "ft_stock_announcements",
            {
                "type": "stock",
                "stock_code": self.stock_symbol(stock_code),
                "page": 1,
                "page_size": min(max(int(page_size), 1), 20),
            },
        )
        data = result.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def get_stock_filter(self, page_size: int = 300) -> list[dict[str, Any]]:
        result = await self.call_tool(
            "ft_stock_filter",
            {"page": 1, "page_size": min(max(int(page_size), 50), 500)},
        )
        data = result.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def get_full_stock_filter(self) -> list[dict[str, Any]]:
        """Read the complete current A-share directory with listing dates."""
        result = await self.call_paginated_tool(
            "ft_stock_filter",
            {},
            # The stock-filter rows are wide. FTShare currently truncates a
            # 40-row page by response size, while 30 rows remain complete.
            page_size=30,
            concurrency=4,
            max_pages=250,
        )
        data = result.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def get_stock_status_changes(self, stock_codes: list[str]) -> dict[str, Any]:
        symbols = [self.stock_symbol(code) for code in stock_codes]
        if not symbols:
            return {"data": [], "metadata": {"truncated": False}}
        return await self.call_tool(
            "ft_get_stk_status_change",
            {"trade_code": ",".join(symbols)},
        )

    async def get_stock_valuation_history(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        result = await self.call_paginated_tool(
            "ft_get_eastmoney_stock_valuation",
            {
                "symbol": self.stock_symbol(stock_code),
                "start_date": str(start_date).replace("-", ""),
                "end_date": str(end_date).replace("-", ""),
            },
            page_size=60,
            concurrency=4,
            max_pages=30,
        )
        data = result.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def get_sector_flow_history(
        self,
        sector_type: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        normalized_type = str(sector_type or "").strip().lower()
        if normalized_type not in {"industry", "concept"}:
            raise FTShareMCPError("FTShare sector flow requires industry or concept")
        result = await self.call_paginated_tool(
            "ft_get_eastmoney_sector_flow",
            {
                "sector_type": normalized_type,
                # The live service accepts ISO dates even though an older
                # public document still describes compact YYYYMMDD values.
                "start_date": str(start_date),
                "end_date": str(end_date),
            },
        )
        data = result.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    @classmethod
    def announcement_document_url(cls, url_hash: object) -> str | None:
        value = str(url_hash or "").strip().lower()
        if not cls._URL_HASH_RE.fullmatch(value):
            return None
        return f"{cls._DOCUMENT_BASE_URL}/{value}"


ftshare_mcp_client = FTShareMCPClient()
