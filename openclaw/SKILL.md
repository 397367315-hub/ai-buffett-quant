---
name: ai-buffett-system
description: Call the AI Buffett A-share quant system through its authenticated stateless MCP gateway.
---

# AI Buffett System for OpenClaw

Use the deployed backend as an MCP JSON-RPC server:

`https://ai-buffett-backend.onrender.com/api/v1/openclaw/mcp`

Authentication is required on every request. Prefer:

`Authorization: Bearer <OPENCLAW_API_KEY>`

The same key may also be sent as `X-OpenClaw-Key`. The key is configured as
the Render `OPENCLAW_API_KEY` secret.

## Protocol

Send JSON-RPC 2.0 POST requests. First call `initialize`, then `tools/list`,
then `tools/call` with `params.name` and `params.arguments`.

Example tool call:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "get_stock_quote",
    "arguments": {"stock_code": "600519"}
  }
}
```

## Available capabilities

- `get_market_snapshot`: complete market snapshot with cache provenance.
- `get_stock_quote`: one or more validated A-share quotes.
- `get_stock_history`: up to 365 days of daily history.
- `run_smart_stock_selection`: horizon, sector, risk, and factor-aware selection.
- `run_technical_screen`: basic, short, long, or custom screener.
- `get_macro_dashboard`: international economy, domestic policy, and A-share direction.
- `analyze_fund_flow`: industry/concept flow for week, two weeks, or month.
- `analyze_dragon_board`: cached Dragon Tiger Board analysis.
- `get_overnight_dashboard`: old and auction-confirmed overnight strategy state.
- `run_overnight_strategy`: submit an auditable simulation stage.
- `get_personal_pool`: personal pool and risk alerts.
- `add_to_personal_pool`: idempotently add a validated stock to a personal pool.
- `check_data_source`: proxy/upstream health and latency.
- `get_decision_workbench_2026`: unified market permission, opportunity density,
  sector lifecycle, Alpha attribution, decision windows, conditional orders,
  dynamic exits, and the daily six-question conclusion.
- `get_decision_snapshots`: read immutable 10:40, midday, 14:40, 14:55, close,
  and manual decision snapshots with validation status.
- `query_system_database`: read-only access to the same production database used by
  the web app. Allowed datasets are `stock_daily_bars`, `market_flow`,
  `stock_selection_runs`, `quant_strategies`, `overnight_runs`, and
  `overnight_positions`. Use `stock_code`, `start_date`, `end_date`, status
  filters, field selection, and pagination as needed.

The database tool is ORM-backed and strictly read-only: it does not accept SQL,
shell commands, Python code, table names outside the allowlist, or writes.
Treat `read_only`, `is_realtime`, `cache_used`, `source`, and `data_date` as
mandatory evidence when explaining market data. A simulation stage never places
a real order.
