"""Validated request models for the quantitative strategy API."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class StrategyRule(BaseModel):
    type: str = Field(min_length=1, max_length=40)
    operator: str = Field(min_length=1, max_length=20)
    value: Any


class RuleGroup(BaseModel):
    logic: Literal["AND", "OR"] = "AND"
    rules: list[StrategyRule] = Field(default_factory=list, max_length=20)


class ExitConfig(BaseModel):
    stop_loss_pct: float = Field(default=5.0, gt=0, le=30)
    take_profit_pct: float = Field(default=15.0, gt=0, le=100)
    max_holding_days: int = Field(default=20, ge=1, le=250)
    rules: list[StrategyRule] = Field(default_factory=list, max_length=20)


class PositionConfig(BaseModel):
    method: Literal["equal_weight", "kelly", "fixed_amount"] = "equal_weight"
    max_holdings: int = Field(default=5, ge=1, le=50)
    max_position_pct: float = Field(default=20.0, gt=0, le=100)
    fixed_amount: float | None = Field(default=None, ge=1000, le=100000000)


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    active: bool = True
    scan_schedule: Literal["daily", "manual"] = "daily"
    filter: RuleGroup = Field(default_factory=RuleGroup)
    entry: RuleGroup
    exit: ExitConfig = Field(default_factory=ExitConfig)
    position: PositionConfig = Field(default_factory=PositionConfig)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("策略名称不能为空")
        return cleaned

    @model_validator(mode="after")
    def require_entry_rule(self):
        if not self.entry.rules:
            raise ValueError("至少需要一条买入规则")
        return self


class StrategyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    active: bool | None = None
    scan_schedule: Literal["daily", "manual"] | None = None
    filter: RuleGroup | None = None
    entry: RuleGroup | None = None
    exit: ExitConfig | None = None
    position: PositionConfig | None = None

    @field_validator("name")
    @classmethod
    def clean_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("策略名称不能为空")
        return cleaned


class PreviewRequest(BaseModel):
    strategy: StrategyCreate
    limit: int = Field(default=20, ge=1, le=100)


class ScanRequest(BaseModel):
    strategy_id: str | None = None
    force: bool = False


class ZhabanScanRequest(BaseModel):
    """Parameters for the research-only limit-up-breakout scanner."""

    target_date: date | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    force: bool = False


class ZhabanBacktestRequest(BaseModel):
    """Bounded daily event-study request for the zhaban research module."""

    start_date: date = Field(default_factory=lambda: date.today() - timedelta(days=365))
    end_date: date = Field(default_factory=date.today)
    initial_capital: float = Field(default=100000.0, ge=10000, le=100000000)
    config: dict[str, Any] = Field(default_factory=dict)
    force: bool = False

    @model_validator(mode="after")
    def validate_period(self):
        if self.end_date <= self.start_date:
            raise ValueError("炸板研究结束日期必须晚于开始日期")
        if (self.end_date - self.start_date).days > 730:
            raise ValueError("炸板研究区间最长支持两年")
        return self


class FQERequest(BaseModel):
    top_n: int = Field(default=10, ge=5, le=15)
    candidate_pool: int = Field(default=60, ge=20, le=120)
    mode: Literal["strict", "pragmatic"] = "pragmatic"
    force: bool = False


class FQEDataSyncRequest(BaseModel):
    full: bool = True
    years: int = Field(default=3, ge=1, le=5)
    force: bool = False


class ResearchRunRequest(BaseModel):
    """Bounded parameters for a reproducible research experiment."""

    experiment_id: str = Field(default="weekly_momentum_baseline_v1", min_length=1, max_length=80)
    days: int = Field(default=365, ge=30, le=730)
    top_n: int = Field(default=10, ge=1, le=50)
    lookback_days: int = Field(default=20, ge=10, le=120)
    holding_days: int = Field(default=5, ge=1, le=20)
    capital: float = Field(default=400000.0, ge=10000, le=100000000)

    @field_validator("experiment_id")
    @classmethod
    def clean_experiment_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("实验编号不能为空")
        return cleaned


class ResearchDslValidateRequest(BaseModel):
    definition: dict[str, Any]


class TradingSkillScanRequest(BaseModel):
    skill_ids: list[str] = Field(default_factory=list, max_length=9)
    force: bool = False
    # Personal trading permissions: enabled by default because some users
    # cannot trade STAR or ChiNext securities.
    exclude_star_market: bool = True
    exclude_gem: bool = True


class TradingSkillValidationRequest(BaseModel):
    skill_id: str = Field(min_length=1, max_length=80)
    start_date: date = Field(default_factory=lambda: date.today() - timedelta(days=365))
    end_date: date = Field(default_factory=date.today)
    max_stocks: int = Field(default=150, ge=20, le=500)

    @model_validator(mode="after")
    def validate_period(self):
        if self.end_date <= self.start_date:
            raise ValueError("Skill验证结束日期必须晚于开始日期")
        if (self.end_date - self.start_date).days > 1825:
            raise ValueError("Skill验证区间最长支持五年")
        return self


class BacktestRequest(BaseModel):
    start_date: date = Field(default_factory=lambda: date.today() - timedelta(days=365))
    end_date: date = Field(default_factory=date.today)
    initial_capital: float = Field(default=100000.0, ge=10000, le=100000000)

    @model_validator(mode="after")
    def validate_period(self):
        if self.end_date <= self.start_date:
            raise ValueError("回测结束日期必须晚于开始日期")
        if (self.end_date - self.start_date).days > 730:
            raise ValueError("单次回测最长支持两年")
        return self


class CompareRequest(BaseModel):
    strategy_ids: list[str] = Field(min_length=2, max_length=5)


class PaperBuyRequest(BaseModel):
    stock_code: str
    stock_name: str = Field(default="", max_length=80)
    price: float = Field(gt=0, le=100000)
    shares: int = Field(gt=0, le=100000000)
    strategy_id: str | None = None
    signal_id: str | None = None


class PaperSellRequest(BaseModel):
    stock_code: str
    price: float = Field(gt=0, le=100000)
    shares: int = Field(gt=0, le=100000000)
    reason: str = Field(default="手动卖出", max_length=120)


class PaperResetRequest(BaseModel):
    initial_capital: float = Field(default=100000.0, ge=10000, le=100000000)
