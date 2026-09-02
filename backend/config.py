import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "AI巴菲特量化分析系统API"
    app_version: str = "1.0.0"
    debug: bool = False

    database_url: str = "sqlite+aiosqlite:///./stockflow.db"
    redis_url: str = "redis://localhost:6379/0"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    data_refresh_interval: int = 60
    data_proxy_base_url: str = ""
    data_proxy_token: str = ""
    data_proxy_timeout: float = 20.0
    market_aggregate_timeout: float = 12.0
    stock_selection_sector_refresh_timeout: float = 1.5
    macro_news_timeout: float = 8.0
    macro_news_cache_seconds: int = 900
    macro_news_announcement_limit: int = 32
    ftshare_mcp_enabled: bool = False
    ftshare_mcp_url: str = "https://market.ft.tech/gateway/mcp"
    ftshare_mcp_timeout: float = 10.0

    # Optional Level-2 provider. The key stays server-side; the frontend only
    # receives capability and data-quality metadata.
    level2_enabled: bool = True
    # NumCat/MeoZ V2.0 API-first gateway. MEOZ_API_KEY is the preferred
    # production variable; NUMCAT_API_KEY remains a backwards-compatible
    # alias for the existing Level-2 integration.
    meoz_enabled: bool = True
    meoz_api_key: str = ""
    meoz_api_route: str = ""
    numcat_api_key: str = ""
    numcat_api_base: str = "https://numcat.net/api"
    numcat_route: str = "dedicated"
    numcat_sz_base_url: str = "http://sz.numcat.net:8866/api"
    numcat_sh_base_url: str = "http://sh.numcat.net:8866/api"
    numcat_public_base_url: str = "https://numcat.net/api"
    numcat_allow_public_fallback: bool = False
    numcat_global_qps: int = 50
    numcat_global_rpm: int = 500
    numcat_heavy_qps: int = 5
    numcat_cache_max_entries: int = 512
    # NumCat responses stay in bounded process memory and are never written
    # to PostgreSQL by the gateway. Oversized payloads are not cached.
    numcat_cache_max_bytes: int = 16 * 1024 * 1024
    numcat_cache_max_payload_bytes: int = 2 * 1024 * 1024
    numcat_schema_url: str = ""
    numcat_schema_cache_file: str = ""
    numcat_timeout: float = 20.0
    numcat_retry_count: int = 3
    numcat_min_request_interval: float = 0.25
    level2_page_size: int = 5000
    level2_max_pages: int = 200
    level2_max_rows: int = 500000
    level2_cache_seconds: int = 300

    # Experimental Level-2 weights. They are configuration, rather than
    # constants buried in the decision engine, so later validation can tune
    # them without changing the API contract.
    level2_hfi_active_flow_weight: float = 0.25
    level2_hfi_absorption_weight: float = 0.20
    level2_hfi_split_weight: float = 0.15
    level2_hfi_imbalance_weight: float = 0.15
    level2_hfi_replenishment_weight: float = 0.10
    level2_hfi_vwap_weight: float = 0.10
    level2_hfi_impact_weight: float = 0.05

    # OpenClaw/MCP read and controlled-action gateway.
    openclaw_enabled: bool = False
    openclaw_api_key: str = ""
    openclaw_tool_timeout: float = 45.0

    # Independent, read-only Shadow module based on the three strong-stock
    # trading books. It never changes existing strategy scores or actions.
    feature_strong_stock_decision: bool = True
    # Independent switch for the additive V2 research layer. V1 clients keep
    # working when operators need to disable the extra Shadow workload.
    feature_strong_stock_decision_v2: bool = True

    # 可视化量化策略模块。生产环境可将目录指向 Render 持久化磁盘；
    # 未配置时使用仓库内的 JSON 数据目录，适合本地开发和单进程部署。
    quant_data_dir: str = ""
    quant_scan_cache_seconds: int = 300
    quant_scan_max_technical_stocks: int = 1500
    quant_backtest_max_stocks: int = 120
    personal_positions_json: str = ""

    # 登录账号配置
    admin_username: str = "admin"
    admin_password: str = "buffett2026"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
