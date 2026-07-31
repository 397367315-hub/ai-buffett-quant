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
    macro_news_timeout: float = 8.0
    macro_news_cache_seconds: int = 900
    macro_news_announcement_limit: int = 32
    ftshare_mcp_enabled: bool = False
    ftshare_mcp_url: str = "https://market.ft.tech/gateway/mcp"
    ftshare_mcp_timeout: float = 10.0

    # 登录账号配置
    admin_username: str = "admin"
    admin_password: str = "buffett2026"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
