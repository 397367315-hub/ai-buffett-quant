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

    # OpenClaw/MCP read and controlled-action gateway.
    openclaw_enabled: bool = False
    openclaw_api_key: str = ""
    openclaw_tool_timeout: float = 45.0

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
