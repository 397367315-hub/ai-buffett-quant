"""学习卡组（CARD-001 / CARD-002）：由已有研究产物派生，卡牌只是方法入口与复习索引。

- 来源只能是当前命名空间可见的 Case（终态）/ 计划某个版本 / 复盘结果；卡面文字全部来自结构化结果，
  不新增结论、不改变证据与行动资格，也不生成仓位；
- deck_id 由（来源类型, 来源 id, 来源版本）派生；同一来源再次派生，内容与标题都不变时返回原版本，
  否则追加新版本（旧版本保留，触发器禁止改写）；
- 不存在稀有度、抽卡、连胜、按收益或交易次数解锁的任何字段或文案（incentive_audit 在写入前检查）；
- 学习记录（已读方法说明 / 完成可复核案例 / 纠正来源错误）只在浏览器本地保存，与盈亏、交易频次无关。
"""
from __future__ import annotations

import json
import re

from ..domain import clock, seat_registry as sr
from ..domain.canonical import canonical_json, content_hash, derived_id
from ..web.errors import ApiError

# 诱导交易/收益激励类文案：卡组里一律不允许出现
BANNED_INCENTIVE = ("SSR", "稀有度", "抽卡", "连胜", "充值", "加仓奖励", "解锁加仓", "签到", "必涨", "保底盈利",
                    "稳赚", "胜率王", "最强席位", "战力", "段位")
_ROLE_ZH = {"primary": "主方法", "validator": "反证", "risk": "风控", "context": "环境", "confirmer": "确认",
            "discipline": "纪律"}
_VERDICT_ZH = {"conditions_met": "条件满足", "conditions_failed": "条件不满足", "watch": "观察（A 级满足，B/C 待核）",
               "unknown": "结论未知"}
_RESULT_ZH = {"met": "满足", "failed": "不满足", "unknown": "未知", "inapplicable": "不适用"}


def incentive_audit(texts: list[str]) -> list[str]:
    hits = []
    for t in texts:
        for w in BANNED_INCENTIVE:
            if w in t:
                hits.append(w)
    return sorted(set(hits))


def _card(source_key: str, idx: int, front: str, back: str, evidence: list[str]) -> dict:
    front = (front or "").strip() or "（空）"
    back = (back or "").strip() or "（无记录）"
    return {"card_id": derived_id("card", source_key, str(idx)), "front": front[:400], "back": back[:2000],
            "evidence_ids": sorted(set(e for e in evidence if re.match(r"^[a-z][a-z0-9_-]{1,95}$", e)))[:50]}


def _cond_lines(conds: list[dict], limit: int = 8) -> str:
    out = []
    for c in conds[:limit]:
        miss = f"（缺 {', '.join(c['missing_inputs'])}）" if c.get("missing_inputs") else ""
        out.append(f"· {_RESULT_ZH.get(c['result'], c['result'])}：{c['description']}{miss}")
    if len(conds) > limit:
        out.append(f"· 另有 {len(conds) - limit} 条条件见原 SeatRun")
    return "\n".join(out)


def cards_from_case(db, case: dict) -> list[dict]:
    key = f"case:{case['case_id']}"
    roles = {r["seat_id"]: r["role"] for r in case["role_assignments"]}
    runs = [json.loads(r["canonical_payload"]) for r in db.query(
        "SELECT canonical_payload FROM seat_runs WHERE case_id=? AND round='initial' ORDER BY created_at, seat_run_id",
        (case["case_id"],))]
    fin = case.get("final_result") or {}
    cards = [_card(key, 0, f"问题：{case['question']}", (fin.get("summary") or "Case 尚无综合结果") +
                   "\n（综合不计票、不平均价位；同一事实被多席引用只算一份证据）", fin.get("evidence_ids") or [])]
    for i, r in enumerate(runs, start=1):
        name = sr.SEAT_NAMES.get(r["seat_id"], r["seat_id"])
        role = _ROLE_ZH.get(roles.get(r["seat_id"], ""), roles.get(r["seat_id"], ""))
        state = {"skipped": "未运行（不适用此任务）", "failed": "运行失败"}.get(r["run_state"])
        head = f"{state or _VERDICT_ZH.get(r['verdict'], r['verdict'])}；适用性：{r['applicability']}（{r['applicability_reason']}）"
        back = head + ("\n" + _cond_lines(r["condition_results"]) if r["condition_results"] else "")
        if r["missing_inputs"]:
            back += f"\n本环境缺：{', '.join(r['missing_inputs'])}（缺什么就判不了什么，不用日线代理）"
        cards.append(_card(key, i, f"{name}（{role}）按自己的方法怎么看 {', '.join(case['symbols']) or '本任务'}？", back,
                           r["evidence_ids"]))
    n = len(cards)
    if fin.get("disagreements"):
        cards.append(_card(key, n, "各席分歧在哪里？（分歧保留，不合并成多数票）",
                           "\n".join(f"· {d}" for d in fin["disagreements"][:12]), fin.get("evidence_ids") or []))
        n += 1
    if fin.get("invalidation_conditions"):
        cards.append(_card(key, n, "主方法与反证给出的失效条件有哪些？",
                           _cond_lines(fin["invalidation_conditions"], 10),
                           [e for c in fin["invalidation_conditions"] for e in c.get("evidence_ids", [])]))
        n += 1
    if fin.get("missing_inputs"):
        cards.append(_card(key, n, "本次哪些输入缺失、哪些条件判定不了？",
                           "、".join(fin["missing_inputs"]) + "\n缺失项一律标为未知或待核，不当作满足或不满足", []))
    return cards


def _rules(rules: list[dict]) -> str:
    if not rules:
        return "（未写）"
    out = []
    for r in rules:
        px = f" @{r['price_decimal']}" if r.get("price_decimal") else ""
        origin = {"author_quote": "原书原文", "editorial_summary": "编者提炼", "engineering_policy": "工程口径",
                  "user_rule": "我自己的规则"}.get(r.get("rule_origin"), r.get("rule_origin") or "")
        out.append(f"· {r['description']}{px}（来源：{origin}）")
    return "\n".join(out)


def cards_from_plan(plan: dict) -> list[dict]:
    key = f"plan:{plan['plan_id']}:{plan['version']}"
    ev = sorted({e for k in ("entry_rules", "exit_rules", "cancel_rules", "invalidation_rules")
                 for r in plan.get(k) or [] for e in r.get("evidence_ids") or []})
    budget = plan.get("risk_budget_minor")
    reasons = plan.get("execution_eligibility_reasons") or []
    cards = [
        _card(key, 0, f"「{plan['title']}」的假设是什么？", plan.get("hypothesis") or "（未写）", ev),
        _card(key, 1, "触发条件（满足才考虑入场）", _rules(plan.get("entry_rules") or []), ev),
        _card(key, 2, "什么情况下取消这笔计划？", _rules(plan.get("cancel_rules") or []), ev),
        _card(key, 3, "失效 / 防守在哪里？", _rules((plan.get("invalidation_rules") or []) + (plan.get("exit_rules") or [])), ev),
        _card(key, 4, "时间退出规则", f"{plan.get('time_exit_rule') or '（未写）'}"
              + (f"；最迟 {plan['exit_deadline']}" if plan.get("exit_deadline") else ""), ev),
        _card(key, 5, "风险预算与仓位上限",
              (f"风险预算 {_yuan(budget)} 元" if budget is not None else "风险预算未写（研究计划不可执行）")
              + (f"；单票仓位上限 {plan['max_position_ratio']}" if plan.get("max_position_ratio") else ""), ev),
        _card(key, 6, "这份计划能用于模拟执行吗？",
              f"{plan['execution_eligibility']}" + (f"：{'；'.join(reasons)}" if reasons else "")
              + f"\n版本 v{plan['version']}，状态 {plan['status']}" + (f"，冻结于 {plan['frozen_at']}" if plan.get("frozen_at") else ""),
              ev),
    ]
    return cards


def _yuan(minor) -> str:
    try:
        v = int(minor)
    except (TypeError, ValueError):
        return str(minor)
    return f"{v // 100}.{v % 100:02d}"


def cards_from_review(review: dict) -> list[dict]:
    key = f"review:{review['review_id']}"
    ev = review.get("corporate_action_evidence_ids") or []
    adh = review.get("rule_adherence") or []
    if review["review_kind"] == "preflight":
        ret = "事前审查：交易尚未发生，所有收益字段为空（不是 0，也不是违纪）"
    else:
        ret = (f"方向价格变化 {review['directional_price_return_ratio'] or '未知'}；"
               f"执行收益 {review['executed_return_ratio'] if review['executed_return_ratio'] is not None else '无成交 → 空'}；"
               f"含息总回报 {review['total_return_ratio'] if review['total_return_ratio'] is not None else '未单列'}")
    cards = [
        _card(key, 0, f"复盘（{'事前' if review['review_kind'] == 'preflight' else '事后'}）对应哪份计划？",
              f"{review['plan_id']} v{review['plan_version']}；冻结哈希 {review['frozen_hash'] or '（未冻结）'}", ev),
        _card(key, 1, "纪律检查只看事前写下的规则：结果如何？", _cond_lines(adh, 12) if adh else "没有可检查的事前规则 → 未知（不是违规）",
              [e for c in adh for e in c.get("evidence_ids", [])]),
        _card(key, 2, "收益与纪律分开看", ret + "\n盈亏不反推守规；守规亏损与违规盈利分别记录", ev),
    ]
    if review.get("missing_inputs"):
        cards.append(_card(key, 3, "复盘缺了哪些输入？", "、".join(review["missing_inputs"]), []))
    if review.get("conclusions"):
        cards.append(_card(key, 4, "复盘结论（只来自结构化结果）", "\n".join(f"· {c}" for c in review["conclusions"][:10]), ev))
    return cards


def _source(db, body: dict, replay_id) -> tuple[list[dict], str | None]:
    st, sid, sv = body["source_type"], body["source_id"], body["source_version"]
    if st == "case":
        if sv is not None:
            raise ApiError(422, "VALIDATION_ERROR", "Case 没有版本号，source_version 必须为 null",
                           [{"field": "source_version", "reason": "must be null for case"}])
        from . import case_orchestrator
        case = case_orchestrator.load(db, sid, replay_id)
        if case is None:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "Case 不存在或在当前上下文中不可见",
                           [{"field": "source_id", "reason": "not visible"}])
        if case["state"] not in ("succeeded", "partial", "failed"):
            raise ApiError(409, "SOURCE_NOT_FINAL", "Case 尚未完成，不能派生卡组", [{"field": "source_id", "reason": case["state"]}])
        return cards_from_case(db, case), None
    if st == "plan":
        if sv is None or not str(sv).isdigit():
            raise ApiError(422, "VALIDATION_ERROR", "计划卡组必须指定版本号",
                           [{"field": "source_version", "reason": "plan version (integer string) required"}])
        from . import plan_service
        p = plan_service.version(db, sid, int(sv), replay_id)
        if p is None:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "计划版本不存在或不可见", [{"field": "source_id", "reason": "not visible"}])
        return cards_from_plan(p), str(int(sv))
    if sv is not None:
        raise ApiError(422, "VALIDATION_ERROR", "复盘没有版本号，source_version 必须为 null",
                       [{"field": "source_version", "reason": "must be null for review"}])
    r = db.one("SELECT canonical_payload, replay_id FROM reviews WHERE review_id=?", (sid,))
    if r is None or r["replay_id"] != replay_id:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "复盘不存在或不可见", [{"field": "source_id", "reason": "not visible"}])
    return cards_from_review(json.loads(r["canonical_payload"])), None


def create(c, db, body: dict, replay_id) -> dict:
    cards, sv = _source(db, body, replay_id)
    hits = incentive_audit([body["title"]] + [x["front"] + x["back"] for x in cards])
    if hits:
        raise ApiError(422, "INCENTIVE_TEXT_REJECTED", "卡组文案不能含收益/频次激励或稀有度用语",
                       [{"field": "title", "reason": f"banned: {', '.join(hits)}"}])
    deck_id = derived_id("deck", body["source_type"], body["source_id"], sv or "-", replay_id or "live")
    latest = c.execute("SELECT version, canonical_payload FROM card_decks WHERE deck_id=? ORDER BY version DESC LIMIT 1",
                       (deck_id,)).fetchone()
    if latest:
        prev = json.loads(latest["canonical_payload"])
        if prev["title"] == body["title"] and content_hash(prev["cards"]) == content_hash(cards):
            return prev
        ver = latest["version"] + 1
    else:
        ver = 1
    deck = {"deck_id": deck_id, "version": ver, "title": body["title"], "source_type": body["source_type"],
            "source_id": body["source_id"], "source_version": sv, "cards": cards, "created_at": clock.utc_iso()}
    c.execute("INSERT INTO card_decks(deck_id, version, source_type, source_id, source_version, replay_id, created_at, "
              "canonical_payload) VALUES(?,?,?,?,?,?,?,?)",
              (deck_id, ver, body["source_type"], body["source_id"], sv, replay_id, deck["created_at"], canonical_json(deck)))
    return deck


def get(db, deck_id: str, replay_id) -> dict | None:
    r = db.one("SELECT canonical_payload, replay_id FROM card_decks WHERE deck_id=? ORDER BY version DESC LIMIT 1", (deck_id,))
    if r is None or r["replay_id"] != replay_id:
        return None
    return json.loads(r["canonical_payload"])


def seat_cards() -> list[dict]:
    """席位卡（方法入口）：只含注册表里的资料、版本、能力与缺口——没有收益、胜率、稀有度。"""
    from ..config import SKILLS_DIR
    out = []
    for s in sr.definitions(SKILLS_DIR):
        cap = sr.CAPABILITIES.get(s["seat_id"], {})
        out.append({"seat_id": s["seat_id"], "name": s["name"], "horizons": s["horizons"],
                    "capabilities": s["capabilities"], "rule_version": s["rule_version"],
                    "b_level_unknown": len(cap.get("b_level") or []), "c_level_unknown": len(cap.get("c_level") or [])})
    return out
