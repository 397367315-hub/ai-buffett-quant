from datetime import date

from wildman.risks import announcement_reasons, classify_announcement, financial_indicator_reasons, financial_indicator_warnings, pit_financial_reasons, pit_financial_warnings, review_window


def test_negative_announcement_is_explicit_and_date_window_is_bounded():
    assert "立案调查" in announcement_reasons("关于立案调查的风险提示公告")
    assert review_window(date(2026, 9, 18))[1] == date(2026, 9, 18)


def test_warning_and_negated_disclosures_are_not_major():
    assert classify_announcement("减持计划完成公告")[0] != "major"
    assert classify_announcement("关于不存在重大诉讼的说明")[0] == "observed"
    assert classify_announcement("关于审计意见的说明")[0] == "warning"
    level, reasons = classify_announcement("减持完成，但收到立案调查通知")
    assert level == "major"
    assert "立案调查" in reasons


def test_negation_is_scoped_to_its_risk_not_the_whole_disclosure():
    level, reasons = classify_announcement("减持完成，但收到立案调查通知")
    assert level == "major"
    assert reasons == ["立案调查"]

    level, reasons = classify_announcement("不存在重大诉讼，但收到行政处罚")
    assert level == "major"
    assert reasons == ["行政处罚"]

    assert classify_announcement("未受到行政处罚，减持计划实施完毕")[0] == "observed"


def test_positive_financial_indicators_do_not_certify_safety():
    assert financial_indicator_reasons({"eps": 1.2, "roe": 8, "debt_to_assets": 40}) == []
    assert financial_indicator_reasons({"eps": 1.2, "roe": 8, "debt_to_assets": 90}) == []
    assert financial_indicator_warnings({"eps": 1.2, "roe": 8, "debt_to_assets": 90})
    assert pit_financial_reasons({"net_profit": 10, "operating_cf": 5}) == []
    assert "PIT已披露净利润<0" in pit_financial_reasons({"net_profit": -1, "operating_cf": 5})
    assert pit_financial_reasons({"net_profit": 10, "operating_cf": -5}) == []
    assert pit_financial_warnings({"net_profit": 10, "operating_cf": -5})
    assert "金融行业现金流口径特殊" in pit_financial_warnings({
        "net_profit": 10, "operating_cf": -5, "industry": "银行",
    })[0]
    assert "不作单项硬风险" in pit_financial_warnings({
        "net_profit": 10, "operating_cf": -5, "industry": "制造业",
    })[0]
