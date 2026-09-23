from copy import deepcopy
from datetime import date

from services.wildman_service import WildmanService
from services.data_collector import shanghai_now
from wildman.rules import WildmanRuleCore
from wildman.candidate_discovery import DISCOVERY_VERSION, discover_candidate


TARGET = date(2026, 9, 22)
TRIGGER = "2026-09-18"


def _facts(*, step2=False, step2_count=14, step3=True, step4=None, step5=None):
    def fact(passed, actual=None, sources=None):
        return {"passed": passed, "actual": actual or {}, "sources": sources or [], "reason": "test"}

    return {
        "trigger_date": TRIGGER,
        "mainline_five_steps": {
            "step1": fact(False),
            "step2": fact(step2, {"首板数": step2_count, "触发日": TRIGGER}, [{"source": "limit_history", "published_at": TRIGGER}]),
            "step3": fact(step3, sources=[{"source": "stock_quotes", "published_at": TRIGGER}, {"source": "stock_bars", "published_at": TRIGGER}]),
            "step4": fact(step4),
            "step5": fact(step5),
        },
    }


def _theme(**extra):
    return {"theme_id": "robot", "theme_name": "机器人", "candidate_eligible": True, **extra}


def test_market_path_uses_real_breadth_even_when_original_step2_failed():
    result = discover_candidate(TARGET, _theme(), _facts(step2=False, step2_count=14, step3=True))

    assert result["status"] == "candidate"
    assert result["path"] == "market"
    assert result["signals"] == {"breadth": True, "capacity": True}
    assert result["reference_confirmed"] is False
    assert result["evidence_date"] == TRIGGER
    assert any("原步骤2" in reason for reason in result["reasons"])


def test_industry_catalyst_with_one_market_response_is_candidate():
    news = [{
        "title": "机器人公司中标产业订单",
        "source": "公司公告",
        "source_kind": "company_announcement",
        "url": "https://example.test/announcement",
        "published_at": "2026-09-17T16:00:00+08:00",
    }]
    result = discover_candidate(TARGET, _theme(), _facts(step2=False, step2_count=0, step3=True), news=news)

    assert result["status"] == "candidate"
    assert result["path"] == "catalyst"
    assert result["catalyst"]["level"] == "industry"
    assert result["catalyst"]["sources"][0]["url"].startswith("https://")


def test_positive_and_negative_industry_events_are_not_treated_the_same():
    positive = [{
        "title": "机器人公司公告披露订单增长",
        "source": "公司公告",
        "source_kind": "company_announcement",
        "url": "https://example.test/order-growth",
        "published_at": "2026-09-17T16:00:00+08:00",
    }]
    negative = [{
        "title": "机器人公司公告披露重大订单取消且业绩下滑",
        "source": "公司公告",
        "source_kind": "company_announcement",
        "url": "https://example.test/order-cancelled",
        "published_at": "2026-09-17T16:00:00+08:00",
    }]

    positive_result = discover_candidate(TARGET, _theme(), _facts(step2=False, step2_count=0, step3=True), news=positive)
    negative_result = discover_candidate(TARGET, _theme(), _facts(step2=False, step2_count=0, step3=True), news=negative)

    assert positive_result["status"] == "candidate"
    assert positive_result["path"] == "catalyst"
    assert positive_result["catalyst"]["level"] == "industry"
    assert negative_result["status"] == "watch"
    assert negative_result["path"] is None
    assert negative_result["catalyst"]["level"] == "unverified"


def test_news_without_market_response_remains_watch_and_unverified_sources_do_not_promote():
    news = [{
        "title": "机器人产业重大订单消息",
        "source": "论坛传闻",
        "url": "https://example.test/hearsay",
        "published_at": "2026-09-17T10:00:00+08:00",
    }]
    result = discover_candidate(TARGET, _theme(), _facts(step2=False, step2_count=0, step3=False), news=news)

    assert result["status"] == "watch"
    assert result["path"] is None
    assert result["catalyst"]["level"] == "unverified"
    assert result["catalyst"]["sources"] == []


def test_future_and_date_only_trigger_news_do_not_count_as_known_catalyst():
    future = [{
        "title": "机器人纳入国家战略",
        "source": "新华社",
        "url": "https://example.test/future",
        "published_at": "2026-09-23T09:00:00+08:00",
    }]
    same_day_without_time = [{
        "title": "机器人纳入国家战略",
        "source": "新华社",
        "url": "https://example.test/same-day",
        "published_at": TRIGGER,
    }]
    for news in (future, same_day_without_time):
        result = discover_candidate(TARGET, _theme(), _facts(step2=False, step2_count=0, step3=False), news=news)
        assert result["catalyst"]["level"] == "unverified"
        assert result["status"] == "watch"


def test_same_day_after_cutoff_and_late_available_time_do_not_count():
    too_late = [{
        "title": "机器人纳入国家战略",
        "source": "新华社",
        "url": "https://example.test/late",
        "published_at": "2026-09-18T10:00:00+08:00",
    }]
    late_available = [{
        "title": "机器人公司公告披露订单",
        "source": "公司公告",
        "source_kind": "company_announcement",
        "url": "https://example.test/available-late",
        "published_at": "2026-09-18T09:40:00+08:00",
        "available_time": "2026-09-18T10:31:00+08:00",
    }]

    for news in (too_late, late_available):
        facts = _facts(step2=False, step2_count=0, step3=False)
        facts["mainline_five_steps"]["step2"]["actual"]["最早首板时间"] = "09:35"
        result = discover_candidate(TARGET, _theme(), facts, news=news)
        assert result["catalyst"]["level"] == "unverified"
        assert result["catalyst"]["cutoff_time"] == "2026-09-18T09:35:00"
        assert result["status"] == "watch"


def test_same_day_news_uses_conservative_0930_cutoff_when_firstboard_time_missing():
    news = [{
        "title": "机器人纳入国家战略",
        "source": "新华社",
        "url": "https://example.test/late-without-board-time",
        "published_at": "2026-09-18T10:00:00+08:00",
    }]

    result = discover_candidate(TARGET, _theme(), _facts(step2=False, step2_count=0, step3=False), news=news)

    assert result["catalyst"]["level"] == "unverified"
    assert result["catalyst"]["cutoff_time"] == "2026-09-18T09:30:00"
    assert result["catalyst"]["cutoff_basis"] == "conservative_window_start"


def test_current_day_theme_firstboard_time_cannot_rewrite_historical_trigger_cutoff():
    news = [{
        "title": "机器人纳入国家战略",
        "source": "新华社",
        "url": "https://example.test/historical-late",
        "published_at": "2026-09-18T09:45:00+08:00",
    }]

    result = discover_candidate(
        TARGET,
        _theme(earliest_first_limit_time="10:00", trade_date=TARGET.isoformat()),
        _facts(step2=False, step2_count=0, step3=False),
        news=news,
    )

    assert result["catalyst"]["level"] == "unverified"
    assert result["catalyst"]["cutoff_time"] == "2026-09-18T09:30:00"
    assert result["catalyst"]["cutoff_basis"] == "conservative_window_start"
    assert result["status"] == "watch"


def test_source_reliability_requires_structured_kind_or_exact_known_source():
    news = [{
        "title": "机器人公司公告披露订单",
        "source": "伪research_media账号",
        "url": "https://example.test/fake-source",
        "published_at": "2026-09-17T10:00:00+08:00",
    }]

    result = discover_candidate(TARGET, _theme(), _facts(step2=False, step2_count=0, step3=True), news=news)

    assert result["catalyst"]["level"] == "unverified"
    assert result["status"] == "watch"


def test_step1_passed_does_not_bypass_new_source_and_time_validation():
    facts = _facts(step2=False, step2_count=0, step3=True)
    facts["mainline_five_steps"]["step1"] = {
        "passed": True,
        "actual": {
            "消息标题": "机器人纳入国家战略",
            "来源": "新华社",
            "URL": "https://example.test/future-step1",
            "发布时间原文": "2026-09-23T09:00:00+08:00",
        },
        "sources": [],
    }

    result = discover_candidate(TARGET, _theme(), facts)

    assert result["catalyst"]["level"] == "unverified"
    assert result["status"] == "watch"


def test_future_evidence_date_cannot_be_candidate():
    facts = _facts(step2=True, step2_count=14, step3=True)
    facts["trigger_date"] = "2026-09-23"
    facts["mainline_five_steps"]["step2"]["actual"]["触发日"] = "2026-09-23"

    result = discover_candidate(TARGET, _theme(), facts)

    assert result["status"] != "candidate"
    assert result["evidence_date"] is None
    assert result["signals"] == {"breadth": None, "capacity": None}


def test_market_signals_require_source_date_and_valid_count():
    missing_sources = _facts(step2=True, step2_count=14, step3=True)
    missing_sources["mainline_five_steps"]["step2"]["sources"] = []
    missing_sources["mainline_five_steps"]["step3"]["sources"] = []
    assert discover_candidate(TARGET, _theme(), missing_sources)["signals"] == {"breadth": None, "capacity": None}

    wrong_date = _facts(step2=True, step2_count=14, step3=True)
    wrong_date["mainline_five_steps"]["step2"]["sources"] = [{"source": "limit_history", "published_at": "2026-09-17"}]
    wrong_date["mainline_five_steps"]["step3"]["sources"] = [{"source": "stock_quotes", "published_at": "2026-09-17"}]
    assert discover_candidate(TARGET, _theme(), wrong_date)["signals"] == {"breadth": None, "capacity": None}

    for bad_count in (float("nan"), float("inf"), True):
        facts = _facts(step2=True, step2_count=bad_count, step3=False)
        assert discover_candidate(TARGET, _theme(), facts)["signals"]["breadth"] is None


def test_discovery_does_not_mutate_inputs():
    theme = _theme(theme_aliases=["机器人概念"])
    facts = _facts(step2=False, step2_count=14, step3=True)
    original_theme = deepcopy(theme)
    original_facts = deepcopy(facts)

    discover_candidate(TARGET, theme, facts, news=[])

    assert theme == original_theme
    assert facts == original_facts


def test_candidate_discovery_field_does_not_change_trade_rule_output():
    core = WildmanRuleCore()
    market = {
        "max_limit_height": 5,
        "limit_up_count": 46,
        "limit_down_count": 2,
        "new_theme_first_boards": 5,
        "ladder_complete": True,
        "core_midcap_support": True,
        "first_divergence": True,
        "leader_nuked": False,
        "mid_level_limit_down_spread": False,
        "one_word_limit_count": 0,
        "leader_volume_acceleration": False,
        "rear_all_red": False,
    }
    theme = {
        "theme_name": "机器人",
        "candidate_eligible": True,
        "mainline_five_steps": {
            "step1": {"passed": False},
            "step2": {"passed": False, "actual": {"首板数": 14, "触发日": TRIGGER}},
            "step3": {"passed": True},
            "step4": {"passed": None},
            "step5": {"passed": None},
        },
    }
    stock = {
        "symbol": "000001", "name": "测试龙头", "close_price": 10,
        "low_price": 9.5, "market_cap": 8_000_000_000,
        "consecutive_limit_days": 5, "theme_linkage": True,
        "emotion_benchmark": True, "first_volume_divergence": True,
        "independent_theme_leadership": True, "early_theme_start": True,
        "divergence_low": 9.5, "pressure_price": 12.5,
    }
    base = core.evaluate(market, theme, stock, {"two_pullbacks_hold": True, "lows_rising": True})
    with_discovery = core.evaluate(
        market,
        {**theme, "candidate_discovery": discover_candidate(TARGET, _theme(), {"trigger_date": TRIGGER, **theme})},
        stock,
        {"two_pullbacks_hold": True, "lows_rising": True},
    )

    assert with_discovery == base


def test_unknown_or_excluded_eligibility_cannot_be_promoted():
    facts = _facts(step2=True, step2_count=14, step3=True)
    assert discover_candidate(TARGET, _theme(candidate_eligible=False), facts)["status"] == "excluded"
    assert discover_candidate(TARGET, _theme(candidate_eligible=None), facts)["status"] == "unknown"


def test_old_dashboard_cache_with_mainlines_requires_discovery_version():
    target = date(2026, 9, 22)
    fresh = {
        "rule_version": "WM_RULE_CORE_V1_3",
        "trade_date": target.isoformat(),
        "updated_at": shanghai_now().isoformat(),
        "mainlines": [{"candidate_discovery": {"version": DISCOVERY_VERSION}}],
        "candidate_discovery_version": DISCOVERY_VERSION,
    }
    assert WildmanService._cache_fresh(fresh, target) is True
    assert WildmanService._cache_fresh({**fresh, "candidate_discovery_version": "old"}, target) is False
    assert WildmanService._cache_fresh({key: value for key, value in fresh.items() if key != "candidate_discovery_version"}, target) is False
    assert WildmanService._cache_fresh({**fresh, "mainlines": [{"theme_name": "旧缓存"}]}, target) is False
