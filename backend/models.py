from datetime import date, datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Float, Date, DateTime,
    Boolean, JSON, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from database import Base


class ConceptBoard(Base):
    __tablename__ = "concept_boards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(20), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    category = Column(String(50))
    one_liner = Column(String(200))
    simple_explanation = Column(Text)
    industry_chain = Column(JSON)
    key_companies = Column(JSON)
    leading_stocks = Column(JSON)
    stock_count = Column(Integer)
    triggers = Column(JSON)
    beginner_tip = Column(Text)
    related_reading = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ConceptFundFlowDaily(Base):
    __tablename__ = "concept_fund_flow_daily"
    __table_args__ = (
        UniqueConstraint("board_code", "trade_date"),
        Index("idx_cffd_date", "trade_date"),
        Index("idx_cffd_board", "board_code"),
        Index("idx_cffd_main_inflow", "trade_date", "main_net_inflow"),
    )

    id = Column(Integer, primary_key=True, autoincrement="auto")
    board_code = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    close_price = Column(Float)
    change_pct = Column(Float)
    main_net_inflow = Column(BigInteger)
    main_net_inflow_pct = Column(Float)
    super_large_net_inflow = Column(BigInteger)
    large_net_inflow = Column(BigInteger)
    medium_net_inflow = Column(BigInteger)
    small_net_inflow = Column(BigInteger)
    up_count = Column(Integer)
    down_count = Column(Integer)
    leading_stock = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)


class IndustryFundFlowDaily(Base):
    __tablename__ = "industry_fund_flow_daily"
    __table_args__ = (UniqueConstraint("board_code", "trade_date"),)

    id = Column(Integer, primary_key=True, autoincrement="auto")
    board_code = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    close_price = Column(Float)
    change_pct = Column(Float)
    main_net_inflow = Column(BigInteger)
    main_net_inflow_pct = Column(Float)
    super_large_net_inflow = Column(BigInteger)
    large_net_inflow = Column(BigInteger)
    medium_net_inflow = Column(BigInteger)
    small_net_inflow = Column(BigInteger)
    up_count = Column(Integer)
    down_count = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)


class StockFundFlowDaily(Base):
    __tablename__ = "stock_fund_flow_daily"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date"),
        Index("idx_sffd_stock", "stock_code", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement="auto")
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(50))
    trade_date = Column(Date, nullable=False)
    close_price = Column(Float)
    change_pct = Column(Float)
    main_net_inflow = Column(BigInteger)
    super_large_net_inflow = Column(BigInteger)
    large_net_inflow = Column(BigInteger)
    medium_net_inflow = Column(BigInteger)
    small_net_inflow = Column(BigInteger)
    created_at = Column(DateTime, default=datetime.utcnow)


class DragonBoardDaily(Base):
    """Daily Dragon-Tiger List snapshot, deduplicated by stock and trade date."""

    __tablename__ = "dragon_board_daily"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_dragon_board_code_date"),
        Index("idx_dragon_board_date_net", "trade_date", "net_amount"),
        Index("idx_dragon_board_code_date", "stock_code", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100), nullable=False)
    close_price = Column(Float)
    change_pct = Column(Float)
    turnover = Column(Float)
    deal_amount = Column(BigInteger)
    buy_amount = Column(BigInteger)
    sell_amount = Column(BigInteger)
    net_amount = Column(BigInteger)
    market_cap = Column(BigInteger)
    institution_count = Column(Integer, nullable=False, default=0)
    institution_buy_amount = Column(BigInteger)
    institution_sell_amount = Column(BigInteger)
    institution_net_amount = Column(BigInteger)
    reason = Column(Text)
    source = Column(String(30), nullable=False, default="eastmoney")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MarketFundFlowDaily(Base):
    __tablename__ = "market_fund_flow_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, unique=True, nullable=False)
    market = Column(String(20))
    main_net_inflow = Column(BigInteger)
    super_large_net_inflow = Column(BigInteger)
    large_net_inflow = Column(BigInteger)
    medium_net_inflow = Column(BigInteger)
    small_net_inflow = Column(BigInteger)
    north_net_inflow = Column(BigInteger)
    created_at = Column(DateTime, default=datetime.utcnow)


class MarketSentimentDaily(Base):
    """Auditable daily breadth, turnover and limit-board emotion snapshot."""

    __tablename__ = "market_sentiment_daily"
    __table_args__ = (Index("idx_market_sentiment_date", "trade_date"),)

    trade_date = Column(Date, primary_key=True)
    up_count = Column(Integer)
    down_count = Column(Integer)
    flat_count = Column(Integer)
    stock_count = Column(Integer)
    market_amount = Column(BigInteger)
    amount_count = Column(Integer)
    average_turnover = Column(Float)
    turnover_count = Column(Integer)
    limit_up_count = Column(Integer)
    limit_down_count = Column(Integer)
    failed_limit_count = Column(Integer)
    failed_limit_rate = Column(Float)
    max_streak_height = Column(Integer)
    source = Column(String(50), nullable=False, default="eastmoney+daily_bars")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class NorthFundFlowDaily(Base):
    __tablename__ = "north_fund_flow_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, unique=True)
    net_inflow = Column(BigInteger)
    sh_net_inflow = Column(BigInteger)
    sz_net_inflow = Column(BigInteger)
    balance = Column(BigInteger)
    created_at = Column(DateTime, default=datetime.utcnow)


class MarketBoard(Base):
    """实时行情发现的板块目录，不与教学板块内容混用。"""

    __tablename__ = "market_boards"
    __table_args__ = (
        UniqueConstraint("board_type", "code"),
        Index("idx_market_boards_type_code", "board_type", "code"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    board_type = Column(String(20), nullable=False)
    code = Column(String(20), nullable=False)
    name = Column(String(100), nullable=False)
    source = Column(String(30), nullable=False, default="eastmoney")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MarketDataCache(Base):
    """Small persistent JSON snapshots that make cold-start directories usable."""

    __tablename__ = "market_data_cache"

    key = Column(String(100), primary_key=True)
    payload = Column(JSON, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class FactorRegistryV5(Base):
    """Versioned factor definitions used by the V5 forward-forecast layer."""

    __tablename__ = "factor_registry"

    factor_id = Column(String(100), primary_key=True)
    name = Column(String(160), nullable=False)
    layer = Column(String(30), nullable=False)
    source = Column(String(200), nullable=False)
    source_level = Column(String(1), nullable=False, default="B")
    ttl_minutes = Column(Integer, nullable=False, default=1440)
    lead_score = Column(Float, nullable=False, default=0.5)
    causal_chain_ids = Column(JSON, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    definition = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class TradingSkillRegistry(Base):
    """Versioned, auditable microstructure skills used by the V5 selection funnel."""

    __tablename__ = "trading_skill_registry"
    __table_args__ = (
        Index("idx_trading_skill_state", "lifecycle_state", "enabled"),
        Index("idx_trading_skill_category", "category", "updated_at"),
    )

    skill_id = Column(String(80), primary_key=True)
    skill_name = Column(String(120), nullable=False)
    skill_version = Column(String(30), nullable=False)
    category = Column(String(50), nullable=False)
    description = Column(Text, nullable=False)
    applicable_market_regimes = Column(JSON, nullable=False, default=list)
    applicable_sector_states = Column(JSON, nullable=False, default=list)
    applicable_horizons = Column(JSON, nullable=False, default=list)
    required_factors = Column(JSON, nullable=False, default=list)
    optional_factors = Column(JSON, nullable=False, default=list)
    required_data_level = Column(String(20), nullable=False, default="DAILY")
    entry_gate = Column(JSON, nullable=False, default=dict)
    confirm_gate = Column(JSON, nullable=False, default=dict)
    reject_gate = Column(JSON, nullable=False, default=dict)
    exit_logic = Column(JSON, nullable=False, default=dict)
    lifecycle_state = Column(String(20), nullable=False, default="EXPERIMENTAL")
    validation_status = Column(String(30), nullable=False, default="NOT_TESTED")
    sample_size = Column(Integer, nullable=False, default=0)
    precision = Column(Float)
    recall = Column(Float)
    hit_rate = Column(Float)
    avg_excess_return = Column(Float)
    profit_loss_ratio = Column(Float)
    max_drawdown = Column(Float)
    brier_score = Column(Float)
    last_backtest_at = Column(DateTime)
    last_recalibrated_at = Column(DateTime)
    enabled = Column(Boolean, nullable=False, default=True)
    definition = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class TradingSkillValidationRun(Base):
    """One immutable PIT/walk-forward validation report for a registered skill."""

    __tablename__ = "trading_skill_validation_runs"
    __table_args__ = (
        Index("idx_skill_validation_skill_time", "skill_id", "completed_at"),
        Index("idx_skill_validation_status", "status", "completed_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    experiment_id = Column(String(80), nullable=False, unique=True)
    skill_id = Column(String(80), ForeignKey("trading_skill_registry.skill_id"), nullable=False)
    skill_version = Column(String(30), nullable=False)
    status = Column(String(30), nullable=False)
    lifecycle_before = Column(String(20), nullable=False)
    lifecycle_after = Column(String(20), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    data_cutoff_time = Column(DateTime, nullable=False)
    sample_size = Column(Integer, nullable=False, default=0)
    parameters = Column(JSON, nullable=False, default=dict)
    metrics = Column(JSON, nullable=False, default=dict)
    partitions = Column(JSON, nullable=False, default=dict)
    walk_forward = Column(JSON, nullable=False, default=list)
    decay_monitor = Column(JSON, nullable=False, default=dict)
    audit = Column(JSON, nullable=False, default=dict)
    report_hash = Column(String(64), nullable=False)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TradingSkillScanSnapshot(Base):
    """Bounded candidate output after market and sector permission checks."""

    __tablename__ = "trading_skill_scan_snapshots"
    __table_args__ = (
        Index("idx_skill_scan_date", "trade_date", "generated_at"),
        Index("idx_skill_scan_permission", "market_permission", "generated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    phase = Column(String(30), nullable=False)
    market_permission = Column(String(20), nullable=False)
    data_cutoff_time = Column(DateTime, nullable=False)
    source = Column(String(120), nullable=False)
    scanned_count = Column(Integer, nullable=False, default=0)
    candidate_count = Column(Integer, nullable=False, default=0)
    payload = Column(JSON, nullable=False, default=dict)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BehaviorReflexivitySnapshot(Base):
    """Auditable per-stock Skill 10 diagnosis at a point-in-time cutoff.

    The denormalised headline fields support history/ranking queries while the
    full payload preserves the six-dimensional evidence and missing-data
    policy used to produce the snapshot.
    """

    __tablename__ = "behavior_reflexivity_snapshots"
    __table_args__ = (
        Index("idx_reflexivity_stock_time", "stock_code", "snapshot_time"),
        Index("idx_reflexivity_candidate_time", "candidate_type", "snapshot_time"),
        Index("idx_reflexivity_trade_date", "trade_date", "selection_score"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100))
    trade_date = Column(Date, nullable=False)
    snapshot_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    forced_buy_pressure = Column(Float)
    forced_sell_pressure = Column(Float)
    nearest_up_liquidity = Column(JSON)
    nearest_down_liquidity = Column(JSON)
    liquidity_asymmetry = Column(Float)
    capital_price_efficiency = Column(Float)
    capital_price_efficiency_delta = Column(Float)
    absorption_score = Column(Float)
    pressure_score = Column(Float)
    psychology_state = Column(String(30))
    psychology_transition = Column(JSON)
    reflexivity_state = Column(String(50))
    reflexivity_score = Column(Float)
    crowding_score = Column(Float)
    selection_score = Column(Float)
    diagnosis_level = Column(String(10))
    candidate_type = Column(String(80))
    data_cutoff_time = Column(DateTime)
    model_version = Column(String(80), nullable=False)
    skill_version = Column(String(30), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RejectedTradingKnowledge(Base):
    """External trading claims that are explicitly barred from model priors."""

    __tablename__ = "rejected_trading_knowledge"

    knowledge_id = Column(String(80), primary_key=True)
    claim = Column(Text, nullable=False)
    rejection_reason = Column(Text, nullable=False)
    category = Column(String(50), nullable=False)
    source = Column(String(200))
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ForecastSnapshotV5(Base):
    """Replayable V5 forecast snapshot with model and data cut-off metadata."""

    __tablename__ = "market_forecasts"
    __table_args__ = (
        UniqueConstraint(
            "forecast_date", "phase", "forecast_version",
            name="uq_market_forecasts_date_phase_version",
        ),
        Index("idx_market_forecasts_date", "forecast_date", "generated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    forecast_date = Column(Date, nullable=False)
    phase = Column(String(30), nullable=False)
    forecast_version = Column(String(50), nullable=False)
    model_version = Column(String(80), nullable=False)
    data_cutoff_time = Column(DateTime, nullable=False)
    data_completeness_pct = Column(Float, nullable=False, default=0.0)
    confidence_ceiling_pct = Column(Float, nullable=False, default=0.0)
    payload = Column(JSON, nullable=False)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class CausalChainActivationV5(Base):
    """Persisted chain activations used to replay and validate resonance changes."""

    __tablename__ = "chain_activations"
    __table_args__ = (
        UniqueConstraint(
            "forecast_date", "phase", "chain_id", "forecast_version",
            name="uq_chain_activations_date_phase_chain_version",
        ),
        Index("idx_chain_activations_date", "forecast_date", "generated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    forecast_date = Column(Date, nullable=False)
    phase = Column(String(30), nullable=False)
    chain_id = Column(String(100), nullable=False)
    forecast_version = Column(String(50), nullable=False)
    activation_pct = Column(Float)
    direction = Column(String(30), nullable=False)
    payload = Column(JSON, nullable=False)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BehaviorSnapshotV5(Base):
    """Point-in-time market psychology and observable behavior evidence."""

    __tablename__ = "behavior_history"
    __table_args__ = (
        UniqueConstraint("behavior_date", "phase", name="uq_behavior_history_date_phase"),
        Index("idx_behavior_history_date", "behavior_date", "generated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    behavior_date = Column(Date, nullable=False)
    phase = Column(String(30), nullable=False)
    market_psychology_state = Column(String(30), nullable=False)
    psychology_transition = Column(String(60))
    behavior_imbalance = Column(Float)
    crowding_state = Column(String(30))
    panic_state = Column(String(30))
    fomo_state = Column(String(30))
    false_breakout_risk = Column(String(30))
    payload = Column(JSON, nullable=False)
    data_cutoff_time = Column(DateTime, nullable=False)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class QuantStrategy(Base):
    """User-authored quantitative strategies stored outside the ephemeral app filesystem."""

    __tablename__ = "quant_strategies"
    __table_args__ = (Index("idx_quant_strategies_updated", "updated_at"),)

    id = Column(String(40), primary_key=True)
    name = Column(String(80), nullable=False, unique=True)
    is_builtin = Column(Boolean, nullable=False, default=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(String(50), nullable=False)
    updated_at = Column(String(50), nullable=False)


class NorthboundDealDaily(Base):
    """北向汇总成交额。净买入字段停止公开时保持为空，绝不填造数据。"""

    __tablename__ = "northbound_deal_daily"
    __table_args__ = (Index("idx_northbound_deal_date", "trade_date"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, unique=True)
    deal_amount = Column(BigInteger, nullable=False)
    net_inflow = Column(BigInteger)
    buy_amount = Column(BigInteger)
    sell_amount = Column(BigInteger)
    balance = Column(BigInteger)
    source = Column(String(30), nullable=False, default="eastmoney")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MarginMarketDaily(Base):
    """End-of-day A-share margin-financing market aggregate."""

    __tablename__ = "margin_market_daily"
    __table_args__ = (Index("idx_margin_market_date", "trade_date"),)

    trade_date = Column(Date, primary_key=True)
    margin_balance = Column(BigInteger)
    financing_balance = Column(BigInteger)
    securities_balance = Column(BigInteger)
    financing_buy = Column(BigInteger)
    financing_repay = Column(BigInteger)
    financing_net_buy = Column(BigInteger)
    float_market_cap = Column(BigInteger)
    market_index_close = Column(Float)
    market_index_change_pct = Column(Float)
    market_turnover_amount = Column(BigInteger)
    financing_ratio = Column(Float)
    lmi_score = Column(Float)
    lmi_level = Column(String(40))
    components = Column(JSON, nullable=False, default=dict)
    source = Column(String(80), nullable=False, default="eastmoney")
    source_updated_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MarginSectorDaily(Base):
    """Industry/concept margin snapshot and multi-window aggregation."""

    __tablename__ = "margin_sector_daily"
    __table_args__ = (
        UniqueConstraint("trade_date", "sector_type", "sector_code", name="uq_margin_sector_date_type_code"),
        Index("idx_margin_sector_date_balance", "trade_date", "financing_balance"),
        Index("idx_margin_sector_date_lri", "trade_date", "crowding_score"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    sector_type = Column(String(30), nullable=False, default="industry")
    sector_code = Column(String(30), nullable=False)
    sector_name = Column(String(120), nullable=False)
    financing_balance = Column(BigInteger)
    securities_balance = Column(BigInteger)
    margin_balance = Column(BigInteger)
    financing_buy = Column(BigInteger)
    financing_repay = Column(BigInteger)
    financing_net_buy = Column(BigInteger)
    financing_net_buy_5d = Column(BigInteger)
    financing_net_buy_20d = Column(BigInteger)
    window_end_date_5d = Column(Date)
    window_end_date_20d = Column(Date)
    financing_change_5d = Column(Float)
    financing_change_20d = Column(Float)
    financing_buy_ratio = Column(Float)
    float_market_cap = Column(BigInteger)
    financing_ratio = Column(Float)
    price_change_pct = Column(Float)
    crowding_score = Column(Float)
    divergence_type = Column(String(60))
    source = Column(String(80), nullable=False, default="eastmoney")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MarginStockDaily(Base):
    """Persistent stock-level T-close margin-financing disclosure."""

    __tablename__ = "margin_stock_daily"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_margin_stock_code_date"),
        Index("idx_margin_stock_date_balance", "trade_date", "financing_balance"),
        Index("idx_margin_stock_code_date", "stock_code", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100), nullable=False)
    exchange = Column(String(10))
    trade_market = Column(String(80))
    sector_name = Column(String(120))
    financing_balance = Column(BigInteger)
    financing_buy = Column(BigInteger)
    financing_repay = Column(BigInteger)
    financing_net_buy = Column(BigInteger)
    financing_net_buy_3d = Column(BigInteger)
    financing_net_buy_5d = Column(BigInteger)
    financing_net_buy_10d = Column(BigInteger)
    securities_balance = Column(BigInteger)
    securities_sell = Column(BigInteger)
    securities_repay = Column(BigInteger)
    margin_balance = Column(BigInteger)
    close_price = Column(Float)
    pct_change = Column(Float)
    price_change_3d = Column(Float)
    price_change_5d = Column(Float)
    price_change_10d = Column(Float)
    turnover_amount = Column(BigInteger)
    turnover_rate = Column(Float)
    float_market_cap = Column(BigInteger)
    financing_ratio = Column(Float)
    financing_buy_ratio = Column(Float)
    source = Column(String(80), nullable=False, default="eastmoney")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockLeverageMetric(Base):
    """Auditable LRI, historical percentile and price-financing relation."""

    __tablename__ = "stock_leverage_metrics"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_stock_leverage_code_date"),
        Index("idx_stock_leverage_date_lri", "trade_date", "lri_score"),
        Index("idx_stock_leverage_code_date", "stock_code", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    stock_code = Column(String(10), nullable=False)
    financing_ratio = Column(Float)
    financing_buy_ratio = Column(Float)
    financing_change_1d = Column(Float)
    financing_change_3d = Column(Float)
    financing_change_5d = Column(Float)
    financing_change_10d = Column(Float)
    financing_change_20d = Column(Float)
    percentile_60 = Column(Float)
    percentile_120 = Column(Float)
    percentile_250 = Column(Float)
    price_change_5d = Column(Float)
    price_change_20d = Column(Float)
    volatility_20d = Column(Float)
    turnover_anomaly_score = Column(Float)
    divergence_type = Column(String(60))
    divergence_score = Column(Float)
    lri_score = Column(Float)
    lri_level = Column(String(40))
    coverage_pct = Column(Float)
    components = Column(JSON, nullable=False, default=dict)
    risk_reasons = Column(JSON, nullable=False, default=list)
    validation_conditions = Column(JSON, nullable=False, default=list)
    invalidation_conditions = Column(JSON, nullable=False, default=list)
    source = Column(String(80), nullable=False, default="calculated")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockDailyBar(Base):
    """A 股日线缓存，按代码和交易日唯一。"""

    __tablename__ = "stock_daily_bars"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date"),
        Index("idx_stock_daily_bars_code_date", "stock_code", "trade_date"),
        Index("idx_stock_daily_bars_date", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100))
    market = Column(String(10))
    trade_date = Column(Date, nullable=False)
    open_price = Column(Float)
    close_price = Column(Float)
    high_price = Column(Float)
    low_price = Column(Float)
    volume = Column(BigInteger)
    amount = Column(BigInteger)
    amplitude = Column(Float)
    change_pct = Column(Float)
    change_amount = Column(Float)
    turnover = Column(Float)
    source = Column(String(30), nullable=False, default="eastmoney")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SecurityMaster(Base):
    """Point-in-time security directory including inactive A-share symbols."""

    __tablename__ = "security_master"
    __table_args__ = (
        Index("idx_security_master_status", "is_currently_listed", "status"),
        Index("idx_security_master_dates", "list_date", "delist_date"),
    )

    stock_code = Column(String(10), primary_key=True)
    stock_name = Column(String(100), nullable=False)
    exchange = Column(String(10), nullable=False)
    list_date = Column(Date)
    delist_date = Column(Date)
    status = Column(String(30), nullable=False, default="unknown")
    is_currently_listed = Column(Boolean, nullable=False, default=True)
    date_quality = Column(String(30), nullable=False, default="missing")
    source = Column(String(80), nullable=False, default="eastmoney")
    source_updated_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SecurityStatusEvent(Base):
    """Dated listing, suspension, resumption and delisting events."""

    __tablename__ = "security_status_events"
    __table_args__ = (
        UniqueConstraint(
            "stock_code", "change_date", "change_type",
            name="uq_security_status_event_code_date_type",
        ),
        Index("idx_security_status_events_date", "change_date", "change_type"),
        Index("idx_security_status_events_code", "stock_code", "change_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100))
    change_date = Column(Date, nullable=False)
    change_type = Column(String(30), nullable=False)
    details = Column(Text)
    source = Column(String(30), nullable=False, default="ftshare")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockValuationHistory(Base):
    """Compact per-stock three-year PE history plus current audit summary."""

    __tablename__ = "stock_valuation_histories"
    __table_args__ = (
        Index("idx_stock_valuation_history_end", "history_end"),
        Index("idx_stock_valuation_sync_status", "sync_status", "history_end"),
    )

    stock_code = Column(String(10), primary_key=True)
    stock_name = Column(String(100))
    history = Column(JSON, nullable=False, default=list)
    requested_start = Column(Date, nullable=False)
    history_start = Column(Date)
    history_end = Column(Date)
    sample_count = Column(Integer, nullable=False, default=0)
    positive_sample_count = Column(Integer, nullable=False, default=0)
    latest_pe_ttm = Column(Float)
    pe_percentile_3y = Column(Float)
    sync_status = Column(String(20), nullable=False, default="available")
    source = Column(String(30), nullable=False, default="eastmoney_proxy")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockMinuteBar(Base):
    """Minute bars captured for intraday strategy evidence and forward audit."""

    __tablename__ = "stock_minute_bars"
    __table_args__ = (
        UniqueConstraint("stock_code", "bar_time", "interval_minutes", name="uq_stock_minute_bar"),
        Index("idx_stock_minute_bars_code_time", "stock_code", "bar_time"),
        Index("idx_stock_minute_bars_time", "bar_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100))
    bar_time = Column(DateTime, nullable=False)
    interval_minutes = Column(Integer, nullable=False, default=1)
    open_price = Column(Float)
    close_price = Column(Float)
    high_price = Column(Float)
    low_price = Column(Float)
    volume = Column(BigInteger)
    amount = Column(BigInteger)
    average_price = Column(Float)
    source = Column(String(30), nullable=False, default="eastmoney")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockIntradayEvidence(Base):
    """Auditable VWAP and active-side trade evidence captured for one session."""

    __tablename__ = "stock_intraday_evidence"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_stock_intraday_evidence_code_date"),
        Index("idx_stock_intraday_evidence_date", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100))
    trade_date = Column(Date, nullable=False)
    latest_bar_at = Column(DateTime)
    last_price = Column(Float)
    vwap = Column(Float)
    vwap_distance_pct = Column(Float)
    above_vwap = Column(Boolean)
    minute_bar_count = Column(Integer, nullable=False, default=0)
    active_buy_amount = Column(BigInteger)
    active_sell_amount = Column(BigInteger)
    neutral_amount = Column(BigInteger)
    active_net_amount = Column(BigInteger)
    active_buy_ratio = Column(Float)
    active_direction = Column(String(20))
    trade_detail_count = Column(Integer, nullable=False, default=0)
    trade_detail_complete = Column(Boolean, nullable=False, default=False)
    source = Column(String(80), nullable=False, default="eastmoney")
    is_realtime = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Level2TradeHistory(Base):
    """Normalized NumCat Level-2 trade records with the original payload kept."""

    __tablename__ = "level2_trade_history"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "source_trade_id", name="uq_l2_trade_source_id"),
        Index("idx_l2_trade_symbol_time", "symbol", "trade_date", "timestamp"),
        Index("idx_l2_trade_buy_order", "symbol", "trade_date", "buy_order_id"),
        Index("idx_l2_trade_sell_order", "symbol", "trade_date", "sell_order_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    source_trade_id = Column(String(160), nullable=False)
    trade_id = Column(String(100))
    price = Column(Float)
    volume = Column(Float)
    amount = Column(Float)
    side = Column(String(16))
    direction_method = Column(String(32))
    direction_confidence = Column(Float)
    trade_code = Column(String(40))
    buy_order_id = Column(String(100))
    sell_order_id = Column(String(100))
    source = Column(String(40), nullable=False, default="numcat")
    raw_payload = Column(JSON, nullable=False, default=dict)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class Level2OrderHistory(Base):
    """Normalized Level-2 order records used for cancellation and cadence analysis."""

    __tablename__ = "level2_order_history"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "source_order_id", name="uq_l2_order_source_id"),
        Index("idx_l2_order_symbol_time", "symbol", "trade_date", "timestamp"),
        Index("idx_l2_order_order_id", "symbol", "trade_date", "order_id"),
        Index("idx_l2_order_order_no", "symbol", "trade_date", "order_no"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    source_order_id = Column(String(160), nullable=False)
    order_id = Column(String(100))
    price = Column(Float)
    volume = Column(Float)
    amount = Column(Float)
    side = Column(String(16))
    order_type = Column(String(40))
    order_no = Column(String(100))
    source = Column(String(40), nullable=False, default="numcat")
    raw_payload = Column(JSON, nullable=False, default=dict)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class Level2QuoteHistory(Base):
    """Historical ten-level order-book snapshots."""

    __tablename__ = "level2_quote_history"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "source_snapshot_id", name="uq_l2_quote_source_id"),
        Index("idx_l2_quote_symbol_time", "symbol", "trade_date", "timestamp"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    source_snapshot_id = Column(String(160), nullable=False)
    last_price = Column(Float)
    open_price = Column(Float)
    high_price = Column(Float)
    low_price = Column(Float)
    pre_close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)
    bid_json = Column(JSON, nullable=False, default=list)
    ask_json = Column(JSON, nullable=False, default=list)
    source = Column(String(40), nullable=False, default="numcat")
    raw_payload = Column(JSON, nullable=False, default=dict)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class Level2Feature1m(Base):
    """One-minute microstructure features; raw ticks are never rescanned by the UI."""

    __tablename__ = "level2_feature_1m"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "minute", name="uq_l2_feature_symbol_date_minute"),
        Index("idx_l2_feature_symbol_time", "symbol", "trade_date", "minute"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    minute = Column(DateTime, nullable=False)
    trade_count = Column(Integer, nullable=False, default=0)
    order_count = Column(Integer, nullable=False, default=0)
    quote_count = Column(Integer, nullable=False, default=0)
    buy_amount = Column(Float)
    sell_amount = Column(Float)
    neutral_amount = Column(Float)
    net_active_amount = Column(Float)
    large_buy_amount = Column(Float)
    large_sell_amount = Column(Float)
    split_buy_score = Column(Float)
    split_sell_score = Column(Float)
    absorption_buy_score = Column(Float)
    absorption_sell_score = Column(Float)
    replenishment_bid = Column(Float)
    replenishment_ask = Column(Float)
    cancel_buy_ratio = Column(Float)
    cancel_sell_ratio = Column(Float)
    order_imbalance = Column(Float)
    order_imbalance_1 = Column(Float)
    order_imbalance_3 = Column(Float)
    order_imbalance_5 = Column(Float)
    order_imbalance_10 = Column(Float)
    vwap = Column(Float)
    last_price = Column(Float)
    price_vs_vwap = Column(Float)
    qas = Column(Float)
    qas_type = Column(String(40))
    hfi = Column(Float)
    hfi_components = Column(JSON, nullable=False, default=dict)
    micro_score = Column(Float)
    distribution_score = Column(Float)
    spoof_risk = Column(Float)
    confidence = Column(Float)
    data_quality = Column(String(24), nullable=False, default="degraded")
    components = Column(JSON, nullable=False, default=dict)
    explanation = Column(JSON, nullable=False, default=list)
    source = Column(String(80), nullable=False, default="numcat")
    created_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Level2FetchJob(Base):
    """Persistent cursor checkpoint for one symbol/date/data-type fetch."""

    __tablename__ = "level2_fetch_jobs"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "data_type", name="uq_l2_fetch_job_key"),
        Index("idx_l2_fetch_job_status", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    data_type = Column(String(16), nullable=False)
    provider = Column(String(40), nullable=False, default="numcat")
    cursor = Column(Text)
    status = Column(String(24), nullable=False, default="queued")
    pages = Column(Integer, nullable=False, default=0)
    rows = Column(Integer, nullable=False, default=0)
    error = Column(Text)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Level2QualitySnapshot(Base):
    """Data completeness audit for a Level-2 symbol/session."""

    __tablename__ = "level2_quality_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", name="uq_l2_quality_symbol_date"),
        Index("idx_l2_quality_date_status", "trade_date", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    status = Column(String(24), nullable=False, default="not_available")
    first_timestamp = Column(DateTime)
    last_timestamp = Column(DateTime)
    trade_count = Column(Integer, nullable=False, default=0)
    order_count = Column(Integer, nullable=False, default=0)
    quote_count = Column(Integer, nullable=False, default=0)
    pagination_complete = Column(Boolean, nullable=False, default=False)
    quote_depth_coverage = Column(Float)
    confidence = Column(Float)
    warnings = Column(JSON, nullable=False, default=list)
    checks = Column(JSON, nullable=False, default=dict)
    source = Column(String(80), nullable=False, default="numcat")
    generated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockUniverseSnapshot(Base):
    """Daily point-in-time universe, industry and market-cap observation."""

    __tablename__ = "stock_universe_snapshots"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_stock_universe_code_date"),
        Index("idx_stock_universe_date", "trade_date"),
        Index("idx_stock_universe_industry_date", "industry", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100))
    exchange = Column(String(10), nullable=False)
    trade_date = Column(Date, nullable=False)
    industry = Column(String(100))
    market_cap = Column(BigInteger)
    close_price = Column(Float)
    is_suspended = Column(Boolean)
    status_quality = Column(String(30), nullable=False, default="observed_quote")
    source = Column(String(50), nullable=False, default="eastmoney")
    observed_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockAuctionSnapshot(Base):
    """09:24-09:27 full-universe call-auction observation captured forward in time."""

    __tablename__ = "stock_auction_snapshots"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_stock_auction_code_date"),
        Index("idx_stock_auction_date", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100))
    trade_date = Column(Date, nullable=False)
    quote_at = Column(DateTime, nullable=False)
    auction_price = Column(Float)
    previous_close = Column(Float)
    high_open_pct = Column(Float)
    auction_volume = Column(BigInteger)
    auction_amount = Column(BigInteger)
    auction_volume_ratio = Column(Float)
    industry = Column(String(100))
    market_cap = Column(BigInteger)
    source = Column(String(50), nullable=False, default="tencent")
    is_realtime = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinancialPITSnapshot(Base):
    """Financial values keyed by their actual public disclosure timestamp."""

    __tablename__ = "financial_pit_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "stock_code", "report_date", "disclosed_at",
            name="uq_financial_pit_code_report_disclosed",
        ),
        Index("idx_financial_pit_disclosed", "disclosed_at"),
        Index("idx_financial_pit_code_disclosed", "stock_code", "disclosed_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100))
    report_date = Column(Date, nullable=False)
    disclosed_at = Column(Date, nullable=False)
    roe = Column(Float)
    gross_margin = Column(Float)
    revenue_growth = Column(Float)
    deducted_profit_growth = Column(Float)
    ocf_to_profit = Column(Float)
    debt_ratio = Column(Float)
    receivable_to_revenue = Column(Float)
    revenue = Column(Float)
    deducted_profit = Column(Float)
    net_profit = Column(Float)
    operating_cf = Column(Float)
    source = Column(String(50), nullable=False, default="eastmoney")
    captured_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OvernightStrategyRun(Base):
    """One auditable preliminary, entry, or exit pass of the overnight strategy."""

    __tablename__ = "overnight_strategy_runs"
    __table_args__ = (
        Index("idx_overnight_runs_stage_created", "stage", "created_at"),
        Index("idx_overnight_runs_status", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stage = Column(String(20), nullable=False)
    trigger = Column(String(20), nullable=False, default="manual")
    status = Column(String(20), nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    message = Column(String(300))
    data_date = Column(Date)
    is_realtime = Column(Boolean, nullable=False, default=False)
    scanned_count = Column(Integer, nullable=False, default=0)
    prefiltered_count = Column(Integer, nullable=False, default=0)
    qualified_count = Column(Integer, nullable=False, default=0)
    candidates = Column(JSON)
    data_quality = Column(JSON)
    error = Column(Text)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class OvernightPosition(Base):
    """A 100-share paper position created only from a fully verified entry pass."""

    __tablename__ = "overnight_positions"
    __table_args__ = (
        UniqueConstraint("entry_run_id", "stock_code", name="uq_overnight_position_run_code"),
        Index("idx_overnight_positions_status_entry", "status", "entry_at"),
        Index("idx_overnight_positions_code", "stock_code", "entry_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_run_id = Column(Integer, ForeignKey("overnight_strategy_runs.id"), nullable=False)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100), nullable=False)
    sector = Column(String(100))
    status = Column(String(20), nullable=False, default="open")
    shares = Column(Integer, nullable=False, default=100)
    signal_at = Column(DateTime, nullable=False)
    entry_at = Column(DateTime, nullable=False)
    entry_price = Column(Float, nullable=False)
    previous_close = Column(Float)
    reference_capital = Column(Float, nullable=False, default=1_000_000.0)
    allocated_pct = Column(Float)
    exit_at = Column(DateTime)
    exit_price = Column(Float)
    exit_reason = Column(String(300))
    pnl = Column(Float)
    pnl_pct = Column(Float)
    audit = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CacheBackfillRun(Base):
    """持久化记录回补进度，前端和运维接口可查询。"""

    __tablename__ = "cache_backfill_runs"
    __table_args__ = (Index("idx_cache_backfill_runs_status", "status", "started_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="queued")
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    requested_days = Column(Integer, nullable=False)
    total_tasks = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
    records_written = Column(Integer, default=0)
    error = Column(Text)


class FQEDataSyncRun(Base):
    """Recoverable security-master and valuation-history synchronization."""

    __tablename__ = "fqe_data_sync_runs"
    __table_args__ = (Index("idx_fqe_data_sync_status", "status", "started_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    sync_mode = Column(String(20), nullable=False, default="full")
    requested_years = Column(Integer, nullable=False, default=3)
    force = Column(Boolean, nullable=False, default=False)
    status = Column(String(20), nullable=False, default="queued")
    stage = Column(String(30), nullable=False, default="queued")
    message = Column(String(300))
    total_securities = Column(Integer, nullable=False, default=0)
    completed_securities = Column(Integer, nullable=False, default=0)
    master_count = Column(Integer, nullable=False, default=0)
    inactive_count = Column(Integer, nullable=False, default=0)
    valuation_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    failed_codes = Column(JSON, nullable=False, default=list)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    error = Column(Text)


class StockSelectionRun(Base):
    """An auditable snapshot of one intelligent stock-selection pipeline run."""

    __tablename__ = "stock_selection_runs"
    __table_args__ = (Index("idx_stock_selection_runs_created", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    mode = Column(String(20), nullable=False)
    risk_profile = Column(String(20), nullable=False)
    candidate_count = Column(Integer, nullable=False, default=0)
    selected_count = Column(Integer, nullable=False, default=0)
    source = Column(String(30), nullable=False, default="eastmoney")
    data_date = Column(Date)
    is_realtime = Column(Boolean, nullable=False, default=False)
    result = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class StockDecisionProfile(Base):
    """Versioned, evidence-bound individual-stock decision snapshot."""

    __tablename__ = "stock_decision_profiles"
    __table_args__ = (
        UniqueConstraint(
            "stock_code", "decision_date", "contract_version",
            name="uq_stock_decision_profile_code_date_version",
        ),
        Index("idx_stock_decision_profiles_code_date", "stock_code", "decision_date"),
        Index("idx_stock_decision_profiles_state_date", "decision_state", "decision_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100))
    decision_date = Column(Date, nullable=False)
    data_date = Column(Date)
    contract_version = Column(String(40), nullable=False)
    decision_state = Column(String(20), nullable=False)
    source = Column(String(300), nullable=False, default="unavailable")
    is_realtime = Column(Boolean, nullable=False, default=False)
    payload = Column(JSON, nullable=False)
    evidence = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DecisionWorkbenchSnapshot(Base):
    """Immutable evidence snapshot for one decision window on one trade date."""

    __tablename__ = "decision_workbench_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "decision_date", "phase", "contract_version",
            name="uq_decision_workbench_snapshot_date_phase_version",
        ),
        Index("idx_decision_workbench_snapshot_date", "decision_date", "captured_at"),
        Index("idx_decision_workbench_snapshot_phase", "phase", "captured_at"),
        Index("idx_decision_workbench_snapshot_validation", "validation_status", "decision_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_date = Column(Date, nullable=False)
    source_data_date = Column(Date, nullable=False)
    phase = Column(String(30), nullable=False)
    phase_label = Column(String(80), nullable=False)
    contract_version = Column(String(50), nullable=False)
    snapshot_hash = Column(String(64), nullable=False)
    is_realtime = Column(Boolean, nullable=False, default=False)
    payload = Column(JSON, nullable=False)
    evidence = Column(JSON, nullable=False, default=list)
    user_judgment = Column(Text)
    validation_status = Column(String(20), nullable=False, default="PENDING")
    validation_result = Column(JSON)
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    validated_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class DataSourceRegistry(Base):
    """Auditable source identity and S/A/B/C trust grade for V4 evidence."""

    __tablename__ = "data_sources"

    source_key = Column(String(80), primary_key=True)
    name = Column(String(120), nullable=False)
    grade = Column(String(1), nullable=False)
    source_type = Column(String(30), nullable=False)
    official_url = Column(String(500))
    active = Column(Boolean, nullable=False, default=True)
    metadata_payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TruthDataEvent(Base):
    """One point-in-time fact with the four timestamps required by V4."""

    __tablename__ = "truth_data_events"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_truth_data_event_fingerprint"),
        Index("idx_truth_events_trade_date", "research_trade_date", "snapshot_time"),
        Index("idx_truth_events_source", "source_key", "available_time"),
        Index("idx_truth_events_kind", "event_kind", "research_trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    fingerprint = Column(String(64), nullable=False)
    event_kind = Column(String(40), nullable=False)
    fact_key = Column(String(120), nullable=False)
    label = Column(String(300), nullable=False)
    source_key = Column(String(80), ForeignKey("data_sources.source_key"), nullable=False)
    source_grade = Column(String(1), nullable=False)
    evidence_tag = Column(String(20), nullable=False, default="FACT")
    event_time = Column(DateTime, nullable=False)
    publish_time = Column(DateTime, nullable=False)
    available_time = Column(DateTime, nullable=False)
    snapshot_time = Column(DateTime, nullable=False)
    research_trade_date = Column(Date, nullable=False)
    data_cutoff_time = Column(DateTime, nullable=False)
    status = Column(String(30), nullable=False, default="ACCEPTED")
    value_payload = Column(JSON, nullable=False, default=dict)
    quality_flags = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class TruthDataConflict(Base):
    """A source, value, statistical-basis, or trading-date conflict."""

    __tablename__ = "data_conflicts"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_truth_data_conflict_fingerprint"),
        Index("idx_truth_conflicts_status", "status", "detected_at"),
        Index("idx_truth_conflicts_trade_date", "research_trade_date", "detected_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    fingerprint = Column(String(64), nullable=False)
    conflict_type = Column(String(40), nullable=False, default="DATA_CONFLICT")
    fact_key = Column(String(120), nullable=False)
    research_trade_date = Column(Date, nullable=False)
    source_keys = Column(JSON, nullable=False, default=list)
    conflicting_values = Column(JSON, nullable=False, default=list)
    resolution = Column(Text)
    confidence_penalty = Column(Float, nullable=False, default=0.0)
    status = Column(String(20), nullable=False, default="OPEN")
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime)


class DataQualityEvent(Base):
    """Recoverable acquisition/quality issue instead of a silent data skip."""

    __tablename__ = "data_quality_events"
    __table_args__ = (
        Index("idx_data_quality_events_status", "status", "detected_at"),
        Index("idx_data_quality_events_component", "component", "research_trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    component = Column(String(80), nullable=False)
    event_type = Column(String(40), nullable=False)
    severity = Column(String(20), nullable=False, default="WARNING")
    research_trade_date = Column(Date)
    source_key = Column(String(80))
    message = Column(Text, nullable=False)
    acquisition_action = Column(Text)
    details = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="OPEN")
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime)


class IndustryValidationSnapshot(Base):
    """Daily PIT aggregate of issuer financial facts for a national direction."""

    __tablename__ = "industry_validation"
    __table_args__ = (
        UniqueConstraint(
            "direction_key", "trade_date", "source_data_date",
            name="uq_industry_validation_direction_date",
        ),
        Index("idx_industry_validation_trade_date", "trade_date", "direction_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    direction_key = Column(String(80), nullable=False)
    direction_name = Column(String(100), nullable=False)
    industries = Column(JSON, nullable=False, default=list)
    trade_date = Column(Date, nullable=False)
    source_data_date = Column(Date, nullable=False)
    latest_disclosure_date = Column(Date)
    universe_count = Column(Integer, nullable=False, default=0)
    financial_sample_count = Column(Integer, nullable=False, default=0)
    coverage_pct = Column(Float, nullable=False, default=0.0)
    validation_status = Column(String(30), nullable=False)
    metrics = Column(JSON, nullable=False, default=dict)
    source = Column(String(100), nullable=False, default="financial_pit_snapshots")
    source_grade = Column(String(1), nullable=False, default="A")
    available_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PolicyTransmissionRecord(Base):
    """Observed policy evidence and its explicitly bounded L1-L6 chain."""

    __tablename__ = "policy_transmission"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_policy_transmission_fingerprint"),
        Index("idx_policy_transmission_direction", "direction_key", "published_at"),
        Index("idx_policy_transmission_trade_date", "research_trade_date", "published_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    fingerprint = Column(String(64), nullable=False)
    direction_key = Column(String(80), nullable=False)
    direction_name = Column(String(100), nullable=False)
    policy_title = Column(String(500), nullable=False)
    policy_url = Column(String(800))
    source_key = Column(String(80), nullable=False)
    source_grade = Column(String(1), nullable=False, default="S")
    published_at = Column(DateTime, nullable=False)
    available_time = Column(DateTime, nullable=False)
    research_trade_date = Column(Date, nullable=False)
    policy_level = Column(String(10), nullable=False)
    marginal_state = Column(String(20), nullable=False, default="STABLE")
    max_verified_level = Column(String(10), nullable=False, default="L2")
    transmission_state = Column(String(30), nullable=False, default="UNVERIFIED")
    stages = Column(JSON, nullable=False, default=list)
    evidence = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MarketWayState(Base):
    """Latest deterministic V4 state for one trade date and decision phase."""

    __tablename__ = "market_way_states"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "phase", "contract_version",
            name="uq_market_way_state_date_phase_version",
        ),
        Index("idx_market_way_states_date", "trade_date", "generated_at"),
        Index("idx_market_way_states_order", "order_state", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    phase = Column(String(30), nullable=False, default="current")
    contract_version = Column(String(50), nullable=False)
    snapshot_hash = Column(String(64), nullable=False)
    truth_status = Column(String(20), nullable=False)
    way_state = Column(String(30), nullable=False)
    order_state = Column(String(30), nullable=False)
    momentum_state = Column(String(30), nullable=False)
    risk_appetite = Column(String(20), nullable=False)
    pricing_force = Column(String(30), nullable=False)
    ai_conclusion = Column(String(30), nullable=False)
    confidence_pct = Column(Float, nullable=False, default=0.0)
    payload = Column(JSON, nullable=False)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MarketWayJudgment(Base):
    """AI/user dual-track judgment with later outcome validation."""

    __tablename__ = "market_way_judgments"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "phase", "user_key",
            name="uq_market_way_judgment_date_phase_user",
        ),
        Index("idx_market_way_judgments_date", "trade_date", "created_at"),
        Index("idx_market_way_judgments_validation", "validation_status", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    phase = Column(String(30), nullable=False, default="current")
    user_key = Column(String(80), nullable=False, default="default")
    ai_judgment = Column(JSON, nullable=False, default=dict)
    user_action = Column(String(30), nullable=False)
    user_judgment = Column(Text)
    user_evidence = Column(JSON, nullable=False, default=list)
    actual_result = Column(JSON)
    validation_status = Column(String(20), nullable=False, default="PENDING")
    correct_party = Column(String(20))
    error_type = Column(String(50))
    validated_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ResearchSession(Base):
    """One versioned strategic or tactical research run and its report snapshot."""

    __tablename__ = "research_sessions"
    __table_args__ = (
        Index("idx_research_sessions_created", "created_at"),
        Index("idx_research_sessions_status", "status", "updated_at"),
        Index("idx_research_sessions_data_date", "source_data_date"),
    )

    id = Column(String(40), primary_key=True)
    mode = Column(String(20), nullable=False, default="quick")
    topic = Column(String(300))
    status = Column(String(20), nullable=False, default="DRAFT")
    stage = Column(String(50), nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    as_of_date = Column(Date)
    source_data_date = Column(Date)
    market_data_version = Column(String(50), nullable=False)
    fundamental_data_version = Column(String(50), nullable=False)
    strategy_version = Column(String(50), nullable=False)
    model_version = Column(String(80), nullable=False)
    prompt_version = Column(String(50), nullable=False)
    research_version = Column(String(50), nullable=False)
    report = Column(JSON, nullable=False, default=dict)
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime)


class ResearchJudgment(Base):
    """User review kept separately from the original AI conclusion."""

    __tablename__ = "research_judgments"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "target_type", "target_key",
            name="uq_research_judgment_session_target",
        ),
        Index("idx_research_judgments_session", "session_id", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(40), ForeignKey("research_sessions.id", ondelete="CASCADE"), nullable=False)
    target_type = Column(String(30), nullable=False)
    target_key = Column(String(100), nullable=False)
    ai_judgment = Column(JSON, nullable=False, default=dict)
    action = Column(String(20), nullable=False)
    user_judgment = Column(Text)
    reason = Column(Text)
    validation_status = Column(String(20), nullable=False, default="PENDING")
    validation_result = Column(Text)
    correct_party = Column(String(20))
    validated_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ResearchHypothesis(Base):
    """A falsifiable market, sector or stock claim awaiting real outcomes."""

    __tablename__ = "research_hypotheses"
    __table_args__ = (
        UniqueConstraint("session_id", "hypothesis_key", name="uq_research_hypothesis_key"),
        Index("idx_research_hypotheses_status_due", "status", "due_date"),
        Index("idx_research_hypotheses_session", "session_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(40), ForeignKey("research_sessions.id", ondelete="CASCADE"), nullable=False)
    hypothesis_key = Column(String(100), nullable=False)
    scope = Column(String(20), nullable=False)
    target = Column(String(100))
    title = Column(String(200), nullable=False)
    statement = Column(Text, nullable=False)
    nature = Column(String(20), nullable=False, default="FORECAST")
    horizon = Column(String(20), nullable=False, default="T+5")
    evidence = Column(JSON, nullable=False, default=list)
    falsification = Column(JSON, nullable=False, default=list)
    due_date = Column(Date)
    status = Column(String(20), nullable=False, default="PENDING")
    actual_result = Column(Text)
    validation_result = Column(Text)
    error_type = Column(String(50))
    validated_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ResearchMarketCase(Base):
    """Validated research outcome used by the case and cognition libraries."""

    __tablename__ = "research_market_cases"
    __table_args__ = (
        Index("idx_research_cases_date", "case_date"),
        Index("idx_research_cases_type", "case_type", "outcome"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(40), ForeignKey("research_sessions.id", ondelete="SET NULL"))
    hypothesis_id = Column(Integer, ForeignKey("research_hypotheses.id", ondelete="SET NULL"))
    case_type = Column(String(30), nullable=False)
    title = Column(String(200), nullable=False)
    summary = Column(Text, nullable=False)
    market_context = Column(JSON, nullable=False, default=dict)
    outcome = Column(String(20), nullable=False)
    error_attribution = Column(String(50))
    lesson = Column(Text)
    tags = Column(JSON, nullable=False, default=list)
    case_date = Column(Date, nullable=False, default=date.today)
    created_at = Column(DateTime, default=datetime.utcnow)


class KnowledgeTerm(Base):
    __tablename__ = "knowledge_terms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    term = Column(String(100), unique=True, nullable=False)
    category = Column(String(50))
    simple_explanation = Column(Text)
    professional_explanation = Column(Text)
    usage_guide = Column(Text)
    related_terms = Column(JSON)
    examples = Column(JSON)
    difficulty_level = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


class LearningCase(Base):
    __tablename__ = "learning_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    summary = Column(Text)
    event_date = Column(Date)
    category = Column(String(50))
    difficulty_level = Column(Integer, default=1)
    steps = Column(JSON, nullable=False)
    quiz = Column(JSON)
    related_terms = Column(JSON)
    key_learnings = Column(JSON)
    view_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserLearningProgress(Base):
    __tablename__ = "user_learning_progress"
    __table_args__ = (UniqueConstraint("user_id", "term_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False)
    term_id = Column(Integer, ForeignKey("knowledge_terms.id"))
    learned = Column(Boolean, default=False)
    reviewed_count = Column(Integer, default=0)
    last_reviewed_at = Column(DateTime)


class UserCaseProgress(Base):
    __tablename__ = "user_case_progress"
    __table_args__ = (UniqueConstraint("user_id", "case_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False)
    case_id = Column(Integer, ForeignKey("learning_cases.id"))
    completed = Column(Boolean, default=False)
    quiz_passed = Column(Boolean, default=False)
    completed_at = Column(DateTime)


class AIChatHistory(Base):
    __tablename__ = "ai_chat_history"
    __table_args__ = (Index("idx_ai_chat_user", "user_id", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement="auto")
    user_id = Column(String(100), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    context_type = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)


class PersonalPoolItem(Base):
    """A user-managed item in one of the five personal investment pools."""

    __tablename__ = "personal_pool_items"
    __table_args__ = (
        UniqueConstraint("pool_key", "code", name="uq_personal_pool_item_pool_code"),
        Index("idx_personal_pool_items_pool", "pool_key", "updated_at"),
        Index("idx_personal_pool_items_code", "code"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    pool_key = Column(String(30), nullable=False)
    code = Column(String(10), nullable=False)
    name = Column(String(100), nullable=False)
    asset_type = Column(String(20), nullable=False, default="stock")
    industry = Column(String(100))
    status = Column(String(30), nullable=False, default="watching")
    cost = Column(Float)
    entry_date = Column(Date)
    position_pct = Column(Float)
    stop_loss = Column(Float)
    targets = Column(JSON)
    max_position = Column(Float)
    thesis = Column(Text)
    risk_note = Column(Text)
    warning = Column(Text)
    etf_type = Column(String(30))
    tags = Column(JSON)
    source = Column(String(50), nullable=False, default="user")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PersonalSystemConfig(Base):
    """Versioned JSON configuration for the personal decision workspace."""

    __tablename__ = "personal_system_config"

    key = Column(String(80), primary_key=True)
    payload = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PersonalInvestmentLog(Base):
    """A manually recorded investment decision or review."""

    __tablename__ = "personal_investment_logs"
    __table_args__ = (Index("idx_personal_logs_created", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(20), nullable=False)
    code = Column(String(10))
    name = Column(String(100))
    price = Column(Float)
    shares = Column(Integer)
    reason = Column(Text, nullable=False)
    pre_check = Column(JSON)
    violations = Column(JSON)
    reflection = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class AIRobotRun(Base):
    """One auditable refresh of the independent AI-managed stock pool."""

    __tablename__ = "ai_robot_runs"
    __table_args__ = (
        Index("idx_ai_robot_runs_pool_created", "pool_type", "created_at"),
        Index("idx_ai_robot_runs_status", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    pool_type = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="queued")
    trigger = Column(String(20), nullable=False, default="manual")
    progress = Column(Integer, nullable=False, default=0)
    message = Column(String(300))
    config_snapshot = Column(JSON)
    summary = Column(JSON)
    error = Column(Text)
    source_data_date = Column(Date)
    is_realtime = Column(Boolean, nullable=False, default=False)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    picks = relationship("AIRobotPick", back_populates="run", cascade="all, delete-orphan")


class AIRobotPick(Base):
    """A source-backed recommendation stored as part of a robot run snapshot."""

    __tablename__ = "ai_robot_picks"
    __table_args__ = (
        UniqueConstraint("run_id", "code", name="uq_ai_robot_pick_run_code"),
        Index("idx_ai_robot_picks_pool_code", "pool_type", "code"),
        Index("idx_ai_robot_picks_sector", "run_id", "sector_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("ai_robot_runs.id", ondelete="CASCADE"), nullable=False)
    pool_type = Column(String(20), nullable=False)
    sector_key = Column(String(40), nullable=False)
    sector_label = Column(String(100), nullable=False)
    board_code = Column(String(20))
    code = Column(String(10), nullable=False)
    name = Column(String(100), nullable=False)
    selected_price = Column(Float)
    selected_on = Column(Date)
    simulated_shares = Column(Integer, nullable=False, default=100)
    score = Column(Float)
    confidence = Column(Float)
    verdict = Column(String(60))
    state = Column(String(20), nullable=False, default="new")
    criteria = Column(JSON)
    evidence = Column(JSON)
    recommendation = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    run = relationship("AIRobotRun", back_populates="picks")


class AIRobotJournal(Base):
    """One daily, auditable decision and performance note per robot pool."""

    __tablename__ = "ai_robot_journals"
    __table_args__ = (
        UniqueConstraint("pool_type", "journal_date", name="uq_ai_robot_journal_pool_date"),
        Index("idx_ai_robot_journals_date", "journal_date"),
        Index("idx_ai_robot_journals_pool_date", "pool_type", "journal_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("ai_robot_runs.id", ondelete="SET NULL"))
    pool_type = Column(String(20), nullable=False)
    journal_date = Column(Date, nullable=False)
    source_data_date = Column(Date)
    is_realtime = Column(Boolean, nullable=False, default=False)
    action_summary = Column(Text, nullable=False)
    decision_reason = Column(Text, nullable=False)
    pnl_reflection = Column(Text)
    lessons = Column(Text)
    metrics = Column(JSON)
    picks_snapshot = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PersonalResearchNote(Base):
    """The latest structured research memo for one personal-pool stock."""

    __tablename__ = "personal_research_notes"
    __table_args__ = (Index("idx_personal_research_notes_updated", "updated_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    first_researched_at = Column(Date)
    why_follow = Column(Text)
    competitive_advantage = Column(Text)
    risks = Column(Text)
    key_metrics = Column(JSON)
    latest_view = Column(Text)
    tags = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PersonalErrorPattern(Base):
    """One recorded investing mistake used to detect repeated behaviour."""

    __tablename__ = "personal_error_patterns"
    __table_args__ = (
        Index("idx_personal_error_patterns_type", "error_type", "occurred_on"),
        Index("idx_personal_error_patterns_code", "code", "occurred_on"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    occurred_on = Column(Date, nullable=False, default=date.today)
    error_type = Column(String(100), nullable=False)
    code = Column(String(10))
    name = Column(String(100))
    lesson = Column(Text, nullable=False)
    prevention = Column(Text, nullable=False)
    context = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


# V5.1 incremental evidence tables.  These tables intentionally keep the
# engine payload alongside a few indexed headline fields: the calculation
# contract can evolve without losing the exact point-in-time evidence used by
# an earlier result.
class AuctionSnapshotV51(Base):
    """Normalized call-auction observations when a time series is available."""

    __tablename__ = "auction_snapshots"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", "snapshot_time", name="uq_v51_auction_snapshot"),
        Index("idx_v51_auction_code_time", "stock_code", "trade_date", "snapshot_time"),
        Index("idx_v51_auction_trade_date", "trade_date", "snapshot_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100))
    trade_date = Column(Date, nullable=False)
    snapshot_time = Column(DateTime, nullable=False)
    indicative_price = Column(Float)
    indicative_return = Column(Float)
    matched_volume = Column(BigInteger)
    matched_amount = Column(BigInteger)
    unmatched_buy_volume = Column(BigInteger)
    unmatched_buy_amount = Column(BigInteger)
    unmatched_sell_volume = Column(BigInteger)
    unmatched_sell_amount = Column(BigInteger)
    activity_count = Column(Integer)
    source = Column(String(80), nullable=False, default="unavailable")
    data_cutoff_time = Column(DateTime, nullable=False)
    quality_score = Column(Float, nullable=False, default=0.0)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class V51EngineSnapshot(Base):
    """Replayable output for one V5.1 engine and one point-in-time cutoff."""

    __tablename__ = "v51_engine_snapshots"
    __table_args__ = (
        Index("idx_v51_engine_symbol_time", "stock_code", "engine_id", "data_cutoff_time"),
        Index("idx_v51_engine_date", "trade_date", "engine_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10))
    trade_date = Column(Date, nullable=False)
    engine_id = Column(String(50), nullable=False)
    status = Column(String(40), nullable=False)
    model_version = Column(String(80), nullable=False)
    data_cutoff_time = Column(DateTime, nullable=False)
    coverage_pct = Column(Float, nullable=False, default=0.0)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class RadarRawSource(Base):
    """Immutable-ish normalized input from a free/public event provider."""

    __tablename__ = "radar_raw_sources"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_radar_raw_content_hash"),
        Index("idx_radar_raw_published", "published_at"),
        Index("idx_radar_raw_provider", "provider", "fetched_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(80), nullable=False)
    provider_event_id = Column(String(160))
    title = Column(String(600), nullable=False)
    content = Column(Text)
    url = Column(String(1000))
    published_at = Column(DateTime)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    content_hash = Column(String(64), nullable=False)
    raw_json = Column(JSON, nullable=False, default=dict)


class RadarEvent(Base):
    """Canonical deduplicated event and its deterministic score."""

    __tablename__ = "radar_events"
    __table_args__ = (
        Index("idx_radar_events_score", "alert_level", "event_score", "last_updated_at"),
        Index("idx_radar_events_status", "status", "last_updated_at"),
        Index("idx_radar_events_type", "event_type", "last_updated_at"),
    )

    event_id = Column(String(80), primary_key=True)
    canonical_title = Column(String(600), nullable=False)
    summary = Column(Text)
    event_type = Column(String(50), nullable=False)
    source_score = Column(Float, nullable=False, default=0.0)
    certainty_score = Column(Float, nullable=False, default=0.0)
    novelty_score = Column(Float, nullable=False, default=0.0)
    impact_score = Column(Float, nullable=False, default=0.0)
    topic_relevance_score = Column(Float, nullable=False, default=0.0)
    market_confirmation_score = Column(Float, nullable=False, default=0.0)
    urgency_score = Column(Float, nullable=False, default=0.0)
    event_score = Column(Float, nullable=False, default=0.0)
    alert_level = Column(String(1), nullable=False, default="C")
    direction = Column(String(20), nullable=False, default="mixed")
    first_seen_at = Column(DateTime, nullable=False)
    last_updated_at = Column(DateTime, nullable=False)
    status = Column(String(30), nullable=False, default="DISCOVERED")
    source_level = Column(String(30), nullable=False, default="unknown")
    data_cutoff_time = Column(DateTime, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)


class RadarEventTopic(Base):
    __tablename__ = "radar_event_topics"
    __table_args__ = (Index("idx_radar_event_topics_topic", "topic_name", "relevance_score"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(80), ForeignKey("radar_events.event_id", ondelete="CASCADE"), nullable=False)
    topic_name = Column(String(120), nullable=False)
    relevance_score = Column(Float, nullable=False, default=0.0)
    direction = Column(String(20), nullable=False, default="mixed")
    reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class RadarEventStock(Base):
    __tablename__ = "radar_event_stocks"
    __table_args__ = (
        UniqueConstraint("event_id", "stock_code", name="uq_radar_event_stock"),
        Index("idx_radar_event_stocks_code", "stock_code", "total_score"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(80), ForeignKey("radar_events.event_id", ondelete="CASCADE"), nullable=False)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(100))
    relation_type = Column(String(30), nullable=False, default="concept")
    relation_score = Column(Float, nullable=False, default=0.0)
    benefit_score = Column(Float, nullable=False, default=0.0)
    business_evidence = Column(Text)
    market_score = Column(Float, nullable=False, default=0.0)
    total_score = Column(Float, nullable=False, default=0.0)
    evidence_tag = Column(String(20), nullable=False, default="INFERRED")
    created_at = Column(DateTime, default=datetime.utcnow)


class RadarAlert(Base):
    __tablename__ = "radar_alerts"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_radar_alert_dedupe"),
        Index("idx_radar_alerts_level_time", "level", "created_at"),
    )

    alert_id = Column(String(80), primary_key=True)
    event_id = Column(String(80), ForeignKey("radar_events.event_id", ondelete="CASCADE"), nullable=False)
    level = Column(String(1), nullable=False)
    title = Column(String(600), nullable=False)
    message = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    sent_at = Column(DateTime)
    channel = Column(String(30), nullable=False, default="in_app")
    status = Column(String(20), nullable=False, default="NEW")
    dedupe_key = Column(String(120), nullable=False)


class RadarProviderHealth(Base):
    __tablename__ = "radar_provider_health"

    provider = Column(String(80), primary_key=True)
    last_success_at = Column(DateTime)
    last_failure_at = Column(DateTime)
    latency_ms = Column(Float)
    error_count = Column(Integer, nullable=False, default=0)
    empty_count = Column(Integer, nullable=False, default=0)
    last_record_time = Column(DateTime)
    status = Column(String(20), nullable=False, default="UNKNOWN")
    details = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RadarEventEffect(Base):
    __tablename__ = "radar_event_effects"
    __table_args__ = (UniqueConstraint("event_id", "window", name="uq_radar_event_effect"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(80), ForeignKey("radar_events.event_id", ondelete="CASCADE"), nullable=False)
    window = Column(String(20), nullable=False)
    market_return = Column(Float)
    topic_return = Column(Float)
    core_stock_return = Column(Float)
    recorded_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    data_quality = Column(JSON, nullable=False, default=dict)


# ROCI is an additive sidecar.  These tables deliberately do not reference or
# overwrite the legacy scoring tables; the snapshot key is the boundary
# between the read-only adapters and the ROCI evidence graph.
class RociSkill(Base):
    __tablename__ = "roci_skills"
    __table_args__ = (
        Index("idx_roci_skills_status", "status", "enabled"),
        Index("idx_roci_skills_category", "category", "status"),
    )

    skill_id = Column(String(32), primary_key=True)
    name = Column(String(128), nullable=False)
    category = Column(String(64), nullable=False)
    source_name = Column(String(255))
    source_section = Column(String(255))
    source_pages = Column(String(64))
    source_claim = Column(Text)
    engineered_definition = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="DETECT_ONLY")
    version = Column(String(32), nullable=False, default="roci-v1.1.2")
    data_requirements = Column(JSON, nullable=False, default=list)
    applicable_regimes = Column(JSON, nullable=False, default=list)
    forbidden_regimes = Column(JSON, nullable=False, default=list)
    default_weight = Column(Float)
    enabled = Column(Boolean, nullable=False, default=True)
    validation_status = Column(String(40), nullable=False, default="NOT_TESTED")
    sample_size = Column(Integer, nullable=False, default=0)
    hit_rate = Column(Float)
    profit_factor = Column(Float)
    expectancy_r = Column(Float)
    max_drawdown = Column(Float)
    last_validated_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RociSkillRun(Base):
    __tablename__ = "roci_skill_runs"
    __table_args__ = (
        Index("idx_roci_skill_runs_skill_time", "skill_id", "snapshot_time"),
        Index("idx_roci_skill_runs_snapshot", "snapshot_key"),
        Index("idx_roci_skill_runs_triggered", "triggered", "snapshot_time"),
        UniqueConstraint("snapshot_key", "skill_id", name="uq_roci_skill_run_snapshot_skill"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False)
    skill_id = Column(String(32), ForeignKey("roci_skills.skill_id"), nullable=False)
    symbol = Column(String(20))
    trade_date = Column(Date)
    snapshot_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    triggered = Column(Boolean, nullable=False, default=False)
    score = Column(Float)
    confidence = Column(Float)
    contribution = Column(Float)
    evidence = Column(JSON, nullable=False, default=list)
    state = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociSourceRegistry(Base):
    __tablename__ = "roci_source_registry"

    source_key = Column(String(80), primary_key=True)
    name = Column(String(255), nullable=False)
    source_type = Column(String(40), nullable=False, default="knowledge")
    locator = Column(String(500))
    trust_note = Column(Text)
    active = Column(Boolean, nullable=False, default=True)
    metadata_payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RociSourceSkillLink(Base):
    __tablename__ = "roci_source_skill_links"
    __table_args__ = (
        UniqueConstraint("source_key", "skill_id", name="uq_roci_source_skill"),
        Index("idx_roci_source_skill_skill", "skill_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_key = Column(String(80), ForeignKey("roci_source_registry.source_key"), nullable=False)
    skill_id = Column(String(32), ForeignKey("roci_skills.skill_id"), nullable=False)
    section = Column(String(255))
    relation = Column(String(40), nullable=False, default="derived_from")
    created_at = Column(DateTime, default=datetime.utcnow)


class RociBattlefieldSnapshot(Base):
    __tablename__ = "roci_battlefield_snapshots"
    __table_args__ = (
        Index("idx_roci_battlefield_date", "trade_date", "data_cutoff_time"),
        Index("idx_roci_battlefield_symbol", "symbol", "data_cutoff_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False, unique=True)
    symbol = Column(String(20))
    trade_date = Column(Date, nullable=False)
    data_cutoff_time = Column(DateTime, nullable=False)
    data_completeness_pct = Column(Float)
    is_realtime = Column(Boolean, nullable=False, default=False)
    cache_used = Column(Boolean, nullable=False, default=False)
    regime = Column(String(32), nullable=False, default="UNKNOWN")
    market_reward = Column(Text)
    market_penalty = Column(Text)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociForce(Base):
    __tablename__ = "roci_forces"
    __table_args__ = (
        Index("idx_roci_forces_snapshot_side", "snapshot_key", "side"),
        UniqueConstraint("snapshot_key", "force_id", name="uq_roci_force_snapshot_force"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False)
    force_id = Column(String(80), nullable=False)
    scope = Column(String(20), nullable=False)
    name = Column(String(160), nullable=False)
    side = Column(String(20), nullable=False)
    strength = Column(Float)
    direction = Column(String(20), nullable=False, default="UNKNOWN")
    confidence = Column(Float)
    persistence = Column(Float)
    relevance = Column(Float)
    evidence = Column(JSON, nullable=False, default=list)
    skills = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociForceHistory(Base):
    __tablename__ = "roci_force_history"
    __table_args__ = (
        Index("idx_roci_force_history_id_time", "force_id", "observed_at"),
        UniqueConstraint("snapshot_key", "force_id", name="uq_roci_force_history_snapshot_force"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    force_id = Column(String(80), nullable=False)
    snapshot_key = Column(String(80), nullable=False)
    observed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    side = Column(String(20), nullable=False)
    strength = Column(Float)
    direction = Column(String(20), nullable=False, default="UNKNOWN")
    evidence = Column(JSON, nullable=False, default=list)


class RociPrimaryContradiction(Base):
    __tablename__ = "roci_primary_contradictions"
    __table_args__ = (
        Index("idx_roci_contradiction_snapshot", "snapshot_key"),
        UniqueConstraint("snapshot_key", name="uq_roci_contradiction_snapshot"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False)
    statement = Column(Text, nullable=False)
    candidate_key = Column(String(80), nullable=False)
    confidence = Column(Float)
    secondary_risks = Column(JSON, nullable=False, default=list)
    supporting_evidence = Column(JSON, nullable=False, default=list)
    opposing_evidence = Column(JSON, nullable=False, default=list)
    what_would_resolve = Column(JSON, nullable=False, default=list)
    what_would_worsen = Column(JSON, nullable=False, default=list)
    status = Column(String(30), nullable=False, default="OBSERVING")
    created_at = Column(DateTime, default=datetime.utcnow)


class RociRiskPricing(Base):
    __tablename__ = "roci_risk_pricing"
    __table_args__ = (
        Index("idx_roci_risk_pricing_snapshot", "snapshot_key"),
        UniqueConstraint("snapshot_key", "risk_key", name="uq_roci_risk_pricing_snapshot_risk"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False)
    risk_key = Column(String(80), nullable=False)
    risk_name = Column(String(160), nullable=False)
    event_strength = Column(Float)
    price_response = Column(Float)
    relative_response = Column(Float)
    recovery_speed = Column(Float)
    pricing_state = Column(String(32), nullable=False, default="UNKNOWN")
    evidence = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociStressEvent(Base):
    __tablename__ = "roci_stress_events"
    __table_args__ = (
        Index("idx_roci_stress_events_snapshot", "snapshot_key"),
        UniqueConstraint("snapshot_key", "event_key", name="uq_roci_stress_snapshot_event"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False)
    event_key = Column(String(80), nullable=False)
    event_name = Column(String(160), nullable=False)
    event_date = Column(Date)
    severity = Column(Float)
    source = Column(String(120))
    expected_response = Column(Text)
    evidence = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociStressResponse(Base):
    __tablename__ = "roci_stress_responses"
    __table_args__ = (Index("idx_roci_stress_response_event", "stress_event_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    stress_event_id = Column(Integer, ForeignKey("roci_stress_events.id"), nullable=False)
    actual_response = Column(Text)
    relative_response = Column(Float)
    recovery_speed = Column(Float)
    post_stress_followthrough = Column(Float)
    resilience_state = Column(String(30), nullable=False, default="UNKNOWN")
    evidence = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociRiskOpportunityConversion(Base):
    __tablename__ = "roci_risk_opportunity_conversions"
    __table_args__ = (
        Index("idx_roci_conversion_snapshot", "snapshot_key"),
        UniqueConstraint("snapshot_key", name="uq_roci_conversion_snapshot"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False)
    risk_event = Column(Text, nullable=False)
    price_response = Column(Text)
    supply_demand_response = Column(Text)
    relative_strength = Column(Float)
    follow_through = Column(Float)
    conversion_state = Column(String(32), nullable=False, default="RISK_UNRESOLVED")
    evidence = Column(JSON, nullable=False, default=list)
    invalidations = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociOpportunityPattern(Base):
    __tablename__ = "roci_opportunity_patterns"
    __table_args__ = (
        Index("idx_roci_patterns_category_status", "category", "status"),
        Index("idx_roci_patterns_updated", "updated_at"),
    )

    pattern_id = Column(String(80), primary_key=True)
    name = Column(String(160), nullable=False)
    category = Column(String(40), nullable=False)
    source_name = Column(String(255))
    definition = Column(Text, nullable=False)
    detection_rule = Column(JSON, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="SHADOW")
    applicable_regimes = Column(JSON, nullable=False, default=list)
    candidate_count = Column(Integer, nullable=False, default=0)
    last_triggered_at = Column(DateTime)
    validation_summary = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RociPatternHit(Base):
    __tablename__ = "roci_pattern_hits"
    __table_args__ = (Index("idx_roci_pattern_hits_pattern_time", "pattern_id", "observed_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    pattern_id = Column(String(80), ForeignKey("roci_opportunity_patterns.pattern_id"), nullable=False)
    snapshot_key = Column(String(80), nullable=False)
    symbol = Column(String(20))
    observed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    triggered = Column(Boolean, nullable=False, default=False)
    score = Column(Float)
    confidence = Column(Float)
    evidence = Column(JSON, nullable=False, default=list)
    outcome = Column(JSON)


class RociAsymmetryScore(Base):
    __tablename__ = "roci_asymmetry_scores"
    __table_args__ = (
        Index("idx_roci_asymmetry_snapshot", "snapshot_key"),
        UniqueConstraint("snapshot_key", name="uq_roci_asymmetry_snapshot"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False)
    symbol = Column(String(20))
    invalidation_distance = Column(Float)
    expected_upside = Column(Float)
    expected_downside = Column(Float)
    estimated_win_probability = Column(Float)
    reward_risk_ratio = Column(Float)
    liquidity_risk = Column(Float)
    gap_risk = Column(Float)
    tail_risk = Column(Float)
    time_cost = Column(Float)
    score = Column(Float)
    status = Column(String(30), nullable=False, default="UNKNOWN")
    evidence = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociAction(Base):
    __tablename__ = "roci_actions"
    __table_args__ = (
        Index("idx_roci_actions_snapshot_time", "snapshot_key", "created_at"),
        UniqueConstraint("snapshot_key", name="uq_roci_action_snapshot"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False)
    symbol = Column(String(20))
    action = Column(String(20), nullable=False)
    reason = Column(Text, nullable=False)
    confidence = Column(Float)
    risk_budget = Column(Float)
    invalidations = Column(JSON, nullable=False, default=list)
    next_checks = Column(JSON, nullable=False, default=list)
    shadow_excluded = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociActionEvidence(Base):
    __tablename__ = "roci_action_evidence"
    __table_args__ = (Index("idx_roci_action_evidence_action", "action_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    action_id = Column(Integer, ForeignKey("roci_actions.id"), nullable=False)
    evidence_type = Column(String(20), nullable=False)
    label = Column(String(300), nullable=False)
    value = Column(JSON)
    source = Column(String(200))
    as_of = Column(DateTime)
    supports = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociReplay(Base):
    __tablename__ = "roci_replays"
    __table_args__ = (Index("idx_roci_replays_symbol_date", "symbol", "trade_date"),)

    replay_id = Column(String(80), primary_key=True)
    symbol = Column(String(20))
    trade_date = Column(Date, nullable=False)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    data_cutoff_time = Column(DateTime, nullable=False)
    status = Column(String(20), nullable=False, default="COMPLETED")
    snapshot_payload = Column(JSON, nullable=False, default=dict)
    outcome_payload = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociUserFeedback(Base):
    __tablename__ = "roci_user_feedback"
    __table_args__ = (Index("idx_roci_feedback_snapshot_time", "snapshot_key", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False)
    user_key = Column(String(80), nullable=False, default="default")
    target = Column(String(80))
    rating = Column(Integer)
    action = Column(String(20))
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociModelRiskEvent(Base):
    __tablename__ = "roci_model_risk_events"
    __table_args__ = (Index("idx_roci_model_risk_time", "severity", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False)
    risk_type = Column(String(60), nullable=False)
    severity = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="OPEN")
    message = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociExplanation(Base):
    """Structured, point-in-time explanation attached to a ROCI result."""

    __tablename__ = "roci_explanations"
    __table_args__ = (
        UniqueConstraint("snapshot_key", "entity_type", "entity_id", name="uq_roci_explanation_entity"),
        Index("idx_roci_explanations_entity", "entity_type", "entity_id", "as_of"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False)
    entity_type = Column(String(64), nullable=False)
    entity_id = Column(String(128), nullable=False)
    as_of = Column(DateTime, nullable=False)
    conclusion_code = Column(String(64))
    conclusion_label = Column(String(255))
    summary = Column(Text)
    confidence = Column(Float)
    explanation_version = Column(String(32), nullable=False, default="roci-explanation-v1.1.2")
    data_quality = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociExplanationDriver(Base):
    __tablename__ = "roci_explanation_drivers"
    __table_args__ = (Index("idx_roci_explanation_drivers_explanation", "explanation_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    explanation_id = Column(Integer, ForeignKey("roci_explanations.id", ondelete="CASCADE"), nullable=False)
    driver_name = Column(String(255), nullable=False)
    direction = Column(String(32))
    importance = Column(Float)
    evidence_strength = Column(Float)
    description = Column(Text)
    metrics = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociExplanationEvidence(Base):
    __tablename__ = "roci_explanation_evidence"
    __table_args__ = (Index("idx_roci_explanation_evidence_explanation", "explanation_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    explanation_id = Column(Integer, ForeignKey("roci_explanations.id", ondelete="CASCADE"), nullable=False)
    evidence_type = Column(String(32), nullable=False)
    claim = Column(Text, nullable=False)
    evidence_strength = Column(Float)
    evidence_grade = Column(String(8))
    source_table = Column(String(128))
    source_field = Column(String(128))
    source_timestamp = Column(DateTime)
    raw_data = Column(JSON, nullable=False, default=dict)
    supports = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociExplanationAlternative(Base):
    __tablename__ = "roci_explanation_alternatives"
    __table_args__ = (Index("idx_roci_explanation_alternatives_explanation", "explanation_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    explanation_id = Column(Integer, ForeignKey("roci_explanations.id", ondelete="CASCADE"), nullable=False)
    hypothesis = Column(String(255), nullable=False)
    support_score = Column(Float)
    supporting_evidence = Column(JSON, nullable=False, default=list)
    contradictions = Column(JSON, nullable=False, default=list)
    required_confirmation = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociExplanationChain(Base):
    __tablename__ = "roci_explanation_chains"
    __table_args__ = (Index("idx_roci_explanation_chains_explanation", "explanation_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    explanation_id = Column(Integer, ForeignKey("roci_explanations.id", ondelete="CASCADE"), nullable=False)
    step_order = Column(Integer, nullable=False)
    from_node = Column(String(255), nullable=False)
    to_node = Column(String(255), nullable=False)
    status = Column(String(32), nullable=False, default="INFERRED")
    confidence = Column(Float)
    evidence = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class RociExplanationValidation(Base):
    __tablename__ = "roci_explanation_validations"
    __table_args__ = (Index("idx_roci_explanation_validations_explanation", "explanation_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    explanation_id = Column(Integer, ForeignKey("roci_explanations.id", ondelete="CASCADE"), nullable=False)
    validation_type = Column(String(20), nullable=False)
    condition_text = Column(Text, nullable=False)
    horizon = Column(String(32))
    source_metric = Column(String(128))
    created_at = Column(DateTime, default=datetime.utcnow)


class RociIntradaySnapshot(Base):
    """Read-only intraday market state snapshot with provider timing metadata."""

    __tablename__ = "roci_intraday_snapshots"
    __table_args__ = (
        Index("idx_roci_intraday_trade_time", "trade_date", "snapshot_time"),
        Index("idx_roci_intraday_status", "data_status", "snapshot_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    snapshot_time = Column(DateTime, nullable=False)
    resolution = Column(String(16), nullable=False, default="1m")
    market_state = Column(String(64), nullable=False, default="UNKNOWN")
    breadth_state = Column(String(64), nullable=False, default="UNKNOWN")
    volume_state = Column(String(64), nullable=False, default="UNKNOWN")
    leadership_state = Column(String(64), nullable=False, default="UNKNOWN")
    migration_state = Column(String(64), nullable=False, default="UNKNOWN")
    risk_score = Column(Float)
    opportunity_score = Column(Float)
    confidence = Column(Float)
    provider_timestamp = Column(DateTime)
    ingest_timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    latency_ms = Column(Float)
    data_status = Column(String(32), nullable=False, default="INSUFFICIENT_DATA")
    is_realtime = Column(Boolean, nullable=False, default=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class BookSkillRegistry(Base):
    """Locked book terminology and auditable engineering metadata."""

    __tablename__ = "book_skill_registry"
    __table_args__ = (
        Index("idx_book_skill_registry_book", "book", "enabled"),
        Index("idx_book_skill_registry_section", "section"),
    )

    skill_id = Column(String(80), primary_key=True)
    book = Column(String(120), nullable=False)
    chapter = Column(String(120), nullable=False)
    section = Column(String(160), nullable=False)
    original_name = Column(String(160), nullable=False)
    description = Column(Text, nullable=False)
    required_features = Column(JSON, nullable=False, default=list)
    prerequisite = Column(JSON, nullable=False, default=list)
    positive_evidence = Column(JSON, nullable=False, default=list)
    negative_evidence = Column(JSON, nullable=False, default=list)
    invalidation = Column(JSON, nullable=False, default=list)
    chart_annotations = Column(JSON, nullable=False, default=list)
    book_rule_version = Column(String(32), nullable=False, default="three-books-v1.0")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockSkillSignal(Base):
    """Point-in-time signal emitted by the independent strong-stock engine."""

    __tablename__ = "stock_skill_signal"
    __table_args__ = (
        Index("idx_stock_skill_signal_code_date", "symbol", "trade_date", "trade_time"),
        Index("idx_stock_skill_signal_skill_status", "skill_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    trade_time = Column(DateTime)
    skill_id = Column(String(80), nullable=False)
    status = Column(String(24), nullable=False, default="NOT_FOUND")
    confidence = Column(Float)
    evidence_json = Column(JSON, nullable=False, default=list)
    invalidation_json = Column(JSON, nullable=False, default=list)
    next_confirmation_json = Column(JSON, nullable=False, default=list)
    source_interval = Column(String(32), nullable=False, default="DAILY")
    engine_version = Column(String(32), nullable=False, default="STRONG_STOCK_DECISION_V1")
    created_at = Column(DateTime, default=datetime.utcnow)


class StrongDecisionState(Base):
    """State-machine output; stored separately from existing ACTION systems."""

    __tablename__ = "decision_state"
    __table_args__ = (
        Index("idx_strong_decision_state_code_date", "symbol", "trade_date", "created_at"),
        Index("idx_strong_decision_state_action", "action", "trade_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    state_code = Column(String(16), nullable=False)
    state_name = Column(String(120), nullable=False)
    primary_skill = Column(String(160))
    secondary_skills_json = Column(JSON, nullable=False, default=list)
    risk_skills_json = Column(JSON, nullable=False, default=list)
    action = Column(String(24), nullable=False, default="NO_TRADE")
    reason_json = Column(JSON, nullable=False, default=dict)
    next_confirmation_json = Column(JSON, nullable=False, default=list)
    invalidation_json = Column(JSON, nullable=False, default=list)
    mode = Column(String(16), nullable=False, default="SHADOW")
    engine_version = Column(String(32), nullable=False, default="STRONG_STOCK_DECISION_V1")
    created_at = Column(DateTime, default=datetime.utcnow)


class MainForceEvidence(Base):
    """Observable volume/price evidence; wording intentionally remains 主力."""

    __tablename__ = "main_force_evidence"
    __table_args__ = (
        Index("idx_main_force_evidence_code_date", "symbol", "trade_date", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    main_force_state = Column(String(32), nullable=False, default="不明显")
    main_force_direction = Column(String(32), nullable=False, default="暂不明确")
    main_force_persistence = Column(String(32), nullable=False, default="减弱")
    volume_pattern = Column(String(160))
    price_pattern = Column(String(160))
    turnover_pattern = Column(String(160))
    evidence_json = Column(JSON, nullable=False, default=list)
    confidence = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


class PatternAnnotation(Base):
    """K-line annotation metadata for the independent chart layer."""

    __tablename__ = "pattern_annotation"
    __table_args__ = (
        Index("idx_pattern_annotation_code_time", "symbol", "start_time", "end_time"),
        Index("idx_pattern_annotation_type", "pattern_type", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    pattern_type = Column(String(160), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    upper_boundary = Column(Float)
    lower_boundary = Column(Float)
    key_price = Column(Float)
    annotation_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class StrongCaseLibrary(Base):
    """Positive, negative and look-alike cases for 望星空 comparison."""

    __tablename__ = "case_library"
    __table_args__ = (
        Index("idx_strong_case_library_book_skill", "book", "skill_id"),
        Index("idx_strong_case_library_symbol_dates", "symbol", "start_date", "end_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    book = Column(String(120), nullable=False)
    skill_id = Column(String(80), nullable=False)
    symbol = Column(String(20), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    case_type = Column(String(24), nullable=False, default="LOOK_ALIKE")
    feature_snapshot_json = Column(JSON, nullable=False, default=dict)
    outcome_json = Column(JSON, nullable=False, default=dict)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class SkillRuleDefinition(Base):
    """Versioned, auditable rule metadata for the V2 skill registry."""

    __tablename__ = "skill_rule_definition"
    __table_args__ = (
        UniqueConstraint("skill_id", "version"),
        Index("idx_skill_rule_definition_skill", "skill_id", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(String(80), nullable=False)
    prerequisite_json = Column(JSON, nullable=False, default=list)
    positive_rule_json = Column(JSON, nullable=False, default=list)
    negative_rule_json = Column(JSON, nullable=False, default=list)
    confirmation_json = Column(JSON, nullable=False, default=list)
    invalidation_json = Column(JSON, nullable=False, default=list)
    required_timeframes_json = Column(JSON, nullable=False, default=list)
    engine_feature_json = Column(JSON, nullable=False, default=list)
    version = Column(String(32), nullable=False, default="three-books-v2.0")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MainForceState(Base):
    """Point-in-time observable main-force state, without unverifiable claims."""

    __tablename__ = "main_force_state"
    __table_args__ = (Index("idx_main_force_state_symbol_time", "symbol", "trade_time", "timeframe"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_time = Column(DateTime, nullable=False)
    timeframe = Column(String(16), nullable=False, default="1d")
    main_force_presence = Column(String(32), nullable=False, default="不明显")
    main_force_direction = Column(String(32), nullable=False, default="暂不明确")
    main_force_stage = Column(String(64), nullable=False, default="样本不足")
    main_force_intent = Column(String(64), nullable=False, default="暂不判断")
    main_force_continuity = Column(String(32), nullable=False, default="未知")
    evidence_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class TradingZoneGeometry(Base):
    """A/B/C geometry and state-machine values used to draw the chart layer."""

    __tablename__ = "trading_zone_geometry"
    __table_args__ = (Index("idx_trading_zone_geometry_symbol_time", "symbol", "trade_time"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_time = Column(DateTime, nullable=False)
    zone = Column(String(32), nullable=False)
    zone_stage = Column(String(40), nullable=False, default="UNKNOWN")
    zone_start = Column(DateTime)
    zone_upper = Column(Float)
    zone_lower = Column(Float)
    short_attack_line = Column(Float)
    mid_long_cost_line = Column(Float)
    small_a_point = Column(Float)
    invalidation_price = Column(Float)
    geometry_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class BigPatternInstance(Base):
    """Lifecycle instance for a documented big-pattern skill."""

    __tablename__ = "big_pattern_instance"
    __table_args__ = (
        Index("idx_big_pattern_instance_symbol_time", "symbol", "start_time", "end_time"),
        Index("idx_big_pattern_instance_skill", "pattern_skill_id", "stage"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    pattern_skill_id = Column(String(80), nullable=False)
    subtype = Column(String(160))
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    stage = Column(String(32), nullable=False, default="NOT_FOUND")
    upper_boundary = Column(Float)
    lower_boundary = Column(Float)
    key_price = Column(Float)
    breakout_price = Column(Float)
    retest_price = Column(Float)
    evidence_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class StarInstance(Base):
    """Context-gated rising-star observation."""

    __tablename__ = "star_instance"
    __table_args__ = (Index("idx_star_instance_symbol_time", "symbol", "trade_time", "star_skill_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    star_skill_id = Column(String(80), nullable=False)
    trade_time = Column(DateTime, nullable=False)
    status = Column(String(24), nullable=False, default="NOT_FOUND")
    pre_context_json = Column(JSON, nullable=False, default=dict)
    star_body_json = Column(JSON, nullable=False, default=dict)
    volume_json = Column(JSON, nullable=False, default=dict)
    ma_json = Column(JSON, nullable=False, default=dict)
    main_force_json = Column(JSON, nullable=False, default=dict)
    confirmation_json = Column(JSON, nullable=False, default=list)
    invalidation_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class ThreeDegreeState(Base):
    """Independent thickness, strength and speed observations."""

    __tablename__ = "three_degree_state"
    __table_args__ = (Index("idx_three_degree_state_symbol_time", "symbol", "trade_time"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_time = Column(DateTime, nullable=False)
    thickness_state = Column(String(32), nullable=False, default="未知")
    strength_state = Column(String(32), nullable=False, default="未知")
    speed_state = Column(String(32), nullable=False, default="未知")
    thickness_evidence_json = Column(JSON, nullable=False, default=list)
    strength_evidence_json = Column(JSON, nullable=False, default=list)
    speed_evidence_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class StockCharacterState(Base):
    """Engineering-only historical behaviour profile for one stock."""

    __tablename__ = "stock_character_state"
    __table_args__ = (Index("idx_stock_character_state_symbol_time", "symbol", "trade_time"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_time = Column(DateTime, nullable=False)
    character_summary = Column(Text, nullable=False, default="有效历史样本不足")
    feature_json = Column(JSON, nullable=False, default=dict)
    historical_samples = Column(Integer, nullable=False, default=0)
    confidence = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


class ThemeState(Base):
    """Theme type, hotspot level and evidence snapshot."""

    __tablename__ = "theme_state"
    __table_args__ = (Index("idx_theme_state_symbol_time", "symbol", "trade_time"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_time = Column(DateTime, nullable=False)
    theme_name = Column(String(160))
    theme_type = Column(String(64), nullable=False, default="未知")
    hotspot_level = Column(String(64), nullable=False, default="未知")
    theme_stage = Column(String(64), nullable=False, default="未接入")
    evidence_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class BuyPointState(Base):
    """Research classification of a buy-point level, never an order."""

    __tablename__ = "buy_point_state"
    __table_args__ = (Index("idx_buy_point_state_symbol_time", "symbol", "trade_time"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_time = Column(DateTime, nullable=False)
    buy_level = Column(String(32), nullable=False, default="臆想买点")
    matched_skills_json = Column(JSON, nullable=False, default=list)
    missing_evidence_json = Column(JSON, nullable=False, default=list)
    counter_evidence_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class SellRiskState(Base):
    """Exit-risk radar snapshot with the three documented sell strategies."""

    __tablename__ = "sell_risk_state"
    __table_args__ = (Index("idx_sell_risk_state_symbol_time", "symbol", "trade_time"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_time = Column(DateTime, nullable=False)
    obvious_top_state = Column(String(32), nullable=False, default="NOT_FOUND")
    meet_top_state = Column(String(32), nullable=False, default="NOT_FOUND")
    c_zone_state = Column(String(32), nullable=False, default="NOT_FOUND")
    classic_top_state = Column(String(32), nullable=False, default="NOT_FOUND")
    risk_evidence_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class ThreeBooksConsensus(Base):
    """Explicit consensus/conflict matrix across the three books."""

    __tablename__ = "three_books_consensus"
    __table_args__ = (Index("idx_three_books_consensus_symbol_time", "symbol", "trade_time"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_time = Column(DateTime, nullable=False)
    hunter_state_json = Column(JSON, nullable=False, default=dict)
    big_pattern_state_json = Column(JSON, nullable=False, default=dict)
    star_state_json = Column(JSON, nullable=False, default=dict)
    consensus_level = Column(String(32), nullable=False, default="冲突")
    conflicts_json = Column(JSON, nullable=False, default=list)
    dominant_signal = Column(String(32), nullable=False, default="NEUTRAL")
    created_at = Column(DateTime, default=datetime.utcnow)


class CaseSimilarity(Base):
    """Similarity links are kept separate from the manually labelled case library."""

    __tablename__ = "case_similarity"
    __table_args__ = (Index("idx_case_similarity_symbol_time", "symbol", "trade_time"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    trade_time = Column(DateTime, nullable=False)
    current_skill = Column(String(80))
    case_id = Column(Integer)
    similarity = Column(Float)
    similar_dimensions_json = Column(JSON, nullable=False, default=list)
    different_dimensions_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class StrongStockRuleConfig(Base):
    """User-adjustable feature parameters, isolated from the locked book rules."""

    __tablename__ = "strong_stock_rule_config"
    __table_args__ = (UniqueConstraint("config_key", "version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(80), nullable=False)
    version = Column(String(32), nullable=False, default="v2.0")
    parameters_json = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
