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


class ResearchSession(Base):
    """One versioned AI weekend-research run and its compact report snapshot."""

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
