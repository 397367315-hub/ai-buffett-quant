from datetime import date, timedelta

from wildman.mainline import candidate_name_allowed, derive_mainline_steps, select_trigger_date


def _bar(day, close, *, low=None, high=None, volume=100):
    return {
        "trade_date": day.isoformat(), "open": close - 0.2, "high": high or close + 0.4,
        "low": low if low is not None else close - 0.4, "close": close,
        "volume": volume, "change_pct": 2.0,
    }


def _fixture():
    target = date(2026, 9, 18)
    history_days = [date(2026, 8, 17) + timedelta(days=index) for index in range(33) if (date(2026, 8, 17) + timedelta(days=index)).weekday() < 5]
    trigger = date(2026, 9, 15)
    burst_follow = date(2026, 9, 16)
    members = ["600001", "600002", "600003", "600004", "600005"]
    limit_history = [{"trade_date": date(2026, 9, 1).isoformat(), "theme_name": "机器人", "code": "600001", "name": "老龙股份", "continuous_days": 4}]
    for day in (trigger, burst_follow):
        limit_history.extend({"trade_date": day.isoformat(), "theme_name": "机器人", "code": code, "name": f"机器人{code}", "continuous_days": 1, "first_limit_time": "09:35"} for code in members)
    board_bars = [{**_bar(day, 100, high=100, volume=100), "code": "INDEX", "is_index": True, "source": "theme_daily"} for day in history_days]
    for day in (trigger, burst_follow):
        board_bars[history_days.index(day)] = {"trade_date": day.isoformat(), "code": "INDEX", "is_index": True, "open": 109, "high": 112, "low": 108, "close": 111, "volume": 150, "source": "theme_daily"}
    stock_bars = {}
    for code in members:
        rows = [_bar(day, 20 + index * 0.02, low=19.5, high=20.5, volume=100) for index, day in enumerate(history_days)]
        rows[-1] = _bar(history_days[-1], 21, low=20, high=21.5, volume=100)
        rows.append(_bar(trigger, 22, low=20.5, high=22.5, volume=150))
        follow = _bar(burst_follow, 22.4, low=21.5, high=23, volume=150)
        follow["open"] = 23.0
        rows.append(follow)
        rows.append(_bar(target, 22.5, low=21.8, high=23, volume=150))
        stock_bars[code] = rows
    stock_bars["600002"][len(history_days)] = {**stock_bars["600002"][len(history_days)], "open": 21.5, "close": 22.5, "change_pct": 5.5}
    quotes = {code: {"trade_date": trigger.isoformat(), "market_cap": 12_000_000_000 if code == "600002" else 2_000_000_000, "name": f"机器人{code}"} for code in members}
    auctions = {code: {"trade_date": burst_follow.isoformat(), "auction_price": 23, "previous_close": 22} for code in members}
    member_history = [{"trade_date": day.isoformat(), "theme_name": "机器人", "symbols": members} for day in history_days]
    member_history.append({"trade_date": target.isoformat(), "theme_name": "机器人", "symbols": [*members, "999999"]})
    theme = {"theme_id": "robot", "theme_name": "机器人", "member_codes": members}
    news = [{"published_at": "2026-09-14T10:00:00", "title": "机器人纳入国家战略重点方向", "source": "新华社", "url": "https://example.test/news/1"}]
    return target, theme, limit_history, board_bars, stock_bars, quotes, news, member_history, auctions


def test_mainline_five_steps_are_point_in_time_and_show_real_facts():
    args = _fixture()
    result = derive_mainline_steps(args[0], args[1], limit_history=args[2], board_bars=args[3], stock_bars=args[4], stock_quotes=args[5], news=args[6], member_history=args[7], auctions=args[8])

    assert set(result["mainline_five_steps"]) == {"step1", "step2", "step3", "step4", "step5"}
    assert result["trigger_date"] == "2026-09-15"
    assert result["validation_date"] == "2026-09-16"
    assert all(result["mainline_five_steps"][key]["passed"] is True for key in ("step1", "step2", "step3", "step4", "step5"))
    assert result["core_stocks"][0]["代码"] == "600002"
    assert result["leader_stocks"][0]["验证日"] == "2026-09-16"
    assert result["mainline_five_steps"]["step5"]["actual"]["首板存活率"] == 1.0
    assert result["mainline_five_steps"]["step1"]["sources"][0]["url"].startswith("https://")
    assert select_trigger_date(args[0], args[1], args[2], args[7]) == date(2026, 9, 15)
    assert result["mainline_five_steps"]["step2"]["actual"]["板指代码"] == "INDEX"


def test_future_members_and_unverifiable_news_do_not_prove_a_step():
    args = _fixture()
    target, theme, limit_history, board_bars, stock_bars, quotes, _, member_history, auctions = args
    future_code = "999999"
    limit_history.append({"trade_date": "2026-09-15", "theme_name": "机器人", "code": future_code, "name": "未来成员", "continuous_days": 9, "first_limit_time": "09:31"})
    result = derive_mainline_steps(target, theme, limit_history=limit_history, board_bars=board_bars, stock_bars=stock_bars, stock_quotes=quotes, news=[{"published_at": "2026-09-15", "title": "传闻机器人将纳入国家战略", "source": "论坛", "url": "https://example.test/hearsay"}], member_history=member_history, auctions=auctions)

    assert result["mainline_five_steps"]["step1"]["passed"] is False
    assert all(item["代码"] != future_code for item in result["leader_stocks"])
    assert all(item["代码"] != future_code for item in result["core_stocks"])


def test_news_offset_is_normalized_to_shanghai_before_cutoff():
    args = _fixture()
    result = derive_mainline_steps(args[0], args[1], limit_history=args[2], board_bars=args[3], stock_bars=args[4], stock_quotes=args[5], news=[{"published_at": "2026-09-15T09:00:00Z", "title": "机器人纳入国家战略重点方向", "source": "新华社", "url": "https://example.test/late"}], member_history=args[7], auctions=args[8])

    assert result["mainline_five_steps"]["step1"]["passed"] is False


def test_no_old_leader_is_explicit_failure_and_no_next_session_is_unknown():
    args = _fixture()
    target, theme, limit_history, board_bars, stock_bars, quotes, news, member_history, auctions = args
    limit_history[:] = [row for row in limit_history if int(row.get("continuous_days", 0)) < 4]
    result = derive_mainline_steps(target, theme, limit_history=limit_history, board_bars=board_bars, stock_bars=stock_bars, stock_quotes=quotes, news=news, member_history=member_history, auctions=auctions)
    assert result["mainline_five_steps"]["step4"]["passed"] is False
    assert "未找到" in result["mainline_five_steps"]["step4"]["reason"]
    assert result["mainline_five_steps"]["step4"]["reason_code"] == "STEP4_OLD_LEADER_NOT_FOUND"

    no_next = derive_mainline_steps(date(2026, 9, 15), theme, limit_history=limit_history, board_bars=board_bars, stock_bars=stock_bars, stock_quotes=quotes, news=news, member_history=member_history, auctions=auctions)
    assert no_next["validation_date"] is None
    assert no_next["mainline_five_steps"]["step5"]["passed"] is None


def test_candidate_name_filter_is_explicit_but_allows_topic_examples():
    assert candidate_name_allowed("机器人股份")
    assert candidate_name_allowed("商业航天科技")
    assert candidate_name_allowed("低空经济设备")
    assert not candidate_name_allowed("ST低价股")
    assert not candidate_name_allowed("地方国企并购重组")
    assert not candidate_name_allowed("机器人行业指数")
    assert not candidate_name_allowed("业绩增长")
    assert not candidate_name_allowed("广东省")
    assert not candidate_name_allowed("600001")
    assert candidate_name_allowed("机器人板块")


def _derive(args):
    return derive_mainline_steps(args[0], args[1], limit_history=args[2], board_bars=args[3], stock_bars=args[4], stock_quotes=args[5], news=args[6], member_history=args[7], auctions=args[8])


def test_later_board_breakout_cannot_replace_failed_first_day():
    args = _fixture()
    for row in args[3]:
        if row["trade_date"] == "2026-09-15":
            row.update(close=99, volume=80)
        elif row["trade_date"] == "2026-09-16":
            row.update(close=150, volume=300)
    result = _derive(args)
    assert result["trigger_date"] == "2026-09-15"
    assert result["mainline_five_steps"]["step2"]["passed"] is False


def test_missing_index_preserves_event_date_without_confirming_breakout():
    args = _fixture()
    args[3].clear()
    result = _derive(args)
    assert result["trigger_date"] == "2026-09-15"
    assert result["mainline_five_steps"]["step2"]["passed"] is None


def test_zero_volume_index_cannot_confirm_a_volume_breakout():
    args = _fixture()
    for row in args[3]:
        row["volume"] = 0
    assert _derive(args)["mainline_five_steps"]["step2"]["passed"] is False


def test_missing_core_bars_are_unknown_but_complete_no_cap_core_fails():
    args = _fixture()
    args[1].update(core_sample_codes=["600002"], core_capitalization_complete=True)
    args[4].pop("600002")
    assert _derive(args)["mainline_five_steps"]["step3"]["passed"] is None
    args[1]["core_sample_codes"] = []
    assert _derive(args)["mainline_five_steps"]["step3"]["passed"] is False


def test_negative_core_opening_fails_even_when_limit_leaders_gap_up():
    from copy import deepcopy
    args = _fixture()
    code = "600999"
    args[4][code] = deepcopy(args[4]["600002"])
    args[5][code] = {**args[5]["600002"], "name": "容量中军"}
    args[5]["600002"]["market_cap"] = 2_000_000_000
    args[1]["member_codes"] = [*args[1]["member_codes"], code]
    for member in args[7]:
        member["symbols"] = [*member["symbols"], code]
    for row in args[4][code]:
        if row["trade_date"] == "2026-09-16":
            row["open"] = 18
    result = _derive(args)
    assert result["mainline_five_steps"]["step3"]["passed"] is True
    assert all(item["开盘溢价"] >= 2 for item in result["leader_stocks"])
    assert result["mainline_five_steps"]["step5"]["passed"] is False


def test_routine_company_contract_is_not_a_major_industry_catalyst():
    args = _fixture()
    args[6][0]["title"] = "机器人公司与供应商签署协议"
    assert _derive(args)["mainline_five_steps"]["step1"]["passed"] is False


def test_continuous_burst_uses_original_date_outside_recent_five_sessions():
    args = _fixture()
    early = date(2026, 9, 11)
    burst_days = [date(2026, 9, day) for day in (11, 14, 17, 18)]
    for day in burst_days:
        args[2].extend({"trade_date": day.isoformat(), "theme_name": "机器人", "code": code,
                        "name": "测试股份", "continuous_days": 1, "first_limit_time": "09:40"}
                       for code in args[1]["member_codes"])
    # A longer real board history permits verification at the original event.
    args[3][:0] = [{**_bar(date(2026, 8, day), 98), "code": "INDEX", "is_index": True}
                  for day in (10, 11, 12, 13, 14)]
    for row in args[3]:
        if row["trade_date"] == early.isoformat():
            row.update(close=110, volume=160)
    result = _derive(args)
    assert select_trigger_date(args[0], args[1], args[2], args[7]) == early
    assert result["trigger_date"] == early.isoformat()
    assert result["mainline_five_steps"]["step2"]["passed"] is True
    assert result["validation_date"] == "2026-09-14"
    assert not candidate_name_allowed("中报增长")
    assert not candidate_name_allowed("含B股")
    assert candidate_name_allowed("华为机器人")
