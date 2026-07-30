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
