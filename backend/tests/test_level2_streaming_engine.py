from datetime import datetime, timedelta

from engines.microstructure import build_feature_series
from engines.microstructure.engine import _amount, _percentile, _price
from market_data.level2.models import BookLevel, OrderBookSnapshot, OrderTick, TradeTick


def _trade(timestamp: datetime, price: float, amount: float) -> TradeTick:
    return TradeTick("600519", timestamp.date(), timestamp, price=price, volume=amount / price, amount=amount)


def _quote(timestamp: datetime, bid: float, ask: float) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        "600519",
        timestamp.date(),
        timestamp,
        last_price=(bid + ask) / 2,
        bids=[BookLevel(bid, 1000, 1), BookLevel(bid - 0.1, 800, 2)],
        asks=[BookLevel(ask, 700, 1), BookLevel(ask + 0.1, 500, 2)],
    )


def test_minute_stream_matches_full_day_with_global_context_and_carried_quote():
    start = datetime(2026, 8, 29, 9, 30)
    trades = [
        _trade(start + timedelta(seconds=5), 10.1, 100),
        _trade(start + timedelta(seconds=20), 10.1, 110),
        _trade(start + timedelta(seconds=50), 10.0, 120),
        _trade(start + timedelta(minutes=1, seconds=5), 10.1, 1000),
        _trade(start + timedelta(minutes=1, seconds=20), 10.1, 2000),
        _trade(start + timedelta(minutes=1, seconds=50), 10.2, 3000),
        _trade(start + timedelta(minutes=2, seconds=5), 10.15, 4000),
        _trade(start + timedelta(minutes=2, seconds=40), 10.3, 5000),
        _trade(start + timedelta(minutes=2, seconds=50), 10.3, 6000),
    ]
    quotes = [
        _quote(start, 10.0, 10.2),
        _quote(start + timedelta(minutes=2, seconds=30), 10.2, 10.4),
    ]
    orders = [
        OrderTick("600519", start.date(), start + timedelta(minutes=index, seconds=10), side="buy", order_type="new")
        for index in range(3)
    ]
    amounts = [_amount(trade) for trade in trades]
    threshold = _percentile(amounts, 85)
    assert threshold == 4800

    full_day = build_feature_series(trades, orders, quotes)
    full_day_with_context = build_feature_series(trades, orders, quotes, large_trade_threshold=threshold)
    assert full_day_with_context == full_day

    streamed = []
    previous_price = None
    previous_quote = None
    for minute_index in range(3):
        minute = start + timedelta(minutes=minute_index)
        minute_trades = [trade for trade in trades if trade.timestamp.replace(second=0, microsecond=0) == minute]
        minute_orders = [order for order in orders if order.timestamp.replace(second=0, microsecond=0) == minute]
        minute_quotes = [quote for quote in quotes if quote.timestamp.replace(second=0, microsecond=0) == minute]
        streamed.extend(
            build_feature_series(
                minute_trades,
                minute_orders,
                minute_quotes,
                previous_price=previous_price,
                previous_quote=previous_quote,
                large_trade_threshold=threshold,
            )
        )
        for trade in sorted(minute_trades, key=lambda item: item.timestamp):
            if _price(trade) is not None:
                previous_price = _price(trade)
        if minute_quotes:
            previous_quote = max(minute_quotes, key=lambda item: item.timestamp)

    assert streamed == full_day

    no_quote_row = streamed[1]
    assert no_quote_row["quote_count"] == 0
    assert no_quote_row["order_imbalance"] is None
    assert no_quote_row["large_buy_amount"] is None
    assert no_quote_row["components"]["active_flow"]["direction_method"] == {
        "tick_rule": 1,
        "unclassified": 1,
        "quote_ask_rule": 1,
    }
    assert streamed[0]["components"]["active_flow"]["direction_method"]["unclassified"] == 2
    assert streamed[2]["large_sell_amount"] is None
    assert streamed[2]["large_buy_amount"] == 5000


def test_future_previous_quote_is_ignored_for_trade_classification():
    timestamp = datetime(2026, 8, 29, 9, 31, 5)
    trade = _trade(timestamp, 10.0, 100)
    future_quote = _quote(timestamp + timedelta(seconds=1), 10.0, 10.2)

    result = build_feature_series([trade], previous_price=10.1, previous_quote=future_quote)

    assert result[0]["quote_count"] == 0
    assert result[0]["components"]["active_flow"]["direction_method"] == {"tick_rule": 1}
