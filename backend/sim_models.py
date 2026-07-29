from datetime import date, datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Float, Date, DateTime,
    Boolean, JSON, ForeignKey, UniqueConstraint, Index
)
from database import Base


class SimAccount(Base):
    __tablename__ = "sim_account"

    id = Column(Integer, primary_key=True, autoincrement="auto")
    name = Column(String(100), default="AI量化交易账户")
    initial_capital = Column(Float, default=1_000_000.0)
    cash = Column(Float, default=1_000_000.0)
    total_value = Column(Float, default=1_000_000.0)
    daily_pnl = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    total_pnl_pct = Column(Float, default=0.0)
    trade_count = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SimPosition(Base):
    __tablename__ = "sim_position"
    __table_args__ = (UniqueConstraint("account_id", "stock_code"),)

    id = Column(Integer, primary_key=True, autoincrement="auto")
    account_id = Column(Integer, ForeignKey("sim_account.id"), nullable=False)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(50))
    shares = Column(Integer, default=0)
    avg_cost = Column(Float, default=0.0)
    current_price = Column(Float, default=0.0)
    market_value = Column(Float, default=0.0)
    pnl = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)
    hold_days = Column(Integer, default=0)
    buy_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SimTradeRecord(Base):
    __tablename__ = "sim_trade_record"
    __table_args__ = (Index("idx_sim_trade_date", "traded_at"),)

    id = Column(Integer, primary_key=True, autoincrement="auto")
    account_id = Column(Integer, ForeignKey("sim_account.id"), nullable=False)
    trade_type = Column(String(10), nullable=False)  # buy / sell
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(50))
    shares = Column(Integer, default=0)
    price = Column(Float, default=0.0)
    amount = Column(Float, default=0.0)
    pnl = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)
    ai_reason = Column(Text)
    ai_score = Column(Float, default=0.0)
    traded_at = Column(DateTime, default=datetime.utcnow)
    trade_date = Column(Date)


class SimDailySummary(Base):
    __tablename__ = "sim_daily_summary"
    __table_args__ = (UniqueConstraint("account_id", "summary_date"),)

    id = Column(Integer, primary_key=True, autoincrement="auto")
    account_id = Column(Integer, ForeignKey("sim_account.id"), nullable=False)
    summary_date = Column(Date, nullable=False)
    daily_pnl = Column(Float, default=0.0)
    daily_pnl_pct = Column(Float, default=0.0)
    total_value = Column(Float, default=0.0)
    cash = Column(Float, default=0.0)
    positions_value = Column(Float, default=0.0)
    trade_count = Column(Integer, default=0)
    positions_count = Column(Integer, default=0)
    top_gainer = Column(String(200))
    top_loser = Column(String(200))
    ai_summary = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
