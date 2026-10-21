"""总指挥（ADR-013）：站在 13 席之上的调度员，不是第 14 个席位。

他能做四件事：
1. **派活**：把问题拆给任意席位组合（1–3 席），每席用**自己的模型、自己的书、自己的脚本**回答（复用 ADR-012 的工具循环）；
2. **自己查**：在全部 13 席的知识库里检索（超级大脑）、读任一席的技能原文、看当前快照的市场/板块、
   查猫爪（Numcat）的只读数据（指数/估值/涨停事件/日历，白名单见 `data_bridge`）；
3. **汇总**：最后必须给结构化报告——结论、各席要点（点名到席位）、分歧、下一步观察、缺什么、数据来源；
4. **平常聊天学习**：不派活也能问，用全部席位知识库回答。

边界（与其它模型接口一致）：
- 不给买卖指令、目标价、胜率；数字只能来自工具返回；报告里点到的席位必须是真派过活的；
- 每次会话最多 6 步、最多 2 轮派活、每轮最多 3 席；每席内部还有 2 步上限；全部调用共用当天 token 总账；
- 训练/回放上下文一律拒绝；全程留证（一步一条摘要，整个会话进证据仓，`model_runs` 一条）。
"""
from __future__ import annotations

import json

from ..adapters import deepseek as ds
from ..adapters import llm_provider as llm
from ..config import SKILLS_DIR
from ..domain import seat_registry as sr
from . import data_bridge, kb_service, postmarket_service, seat_agent

TOOLS = {
    "list_seats": {"args": {}, "desc": "列出 13 席：各自能判断什么、缺什么；派活前先看这个"},
    "dispatch": {"args": {"seats": "string[]（1–3 个 seat_id）", "question": "string（要写清楚问什么）",
                          "symbols": "string[]（可选，例如 000001.SZ）"},
                 "desc": "派活给指定席位：每席用自己的书与技能回答（一次最多 3 席、整场最多 2 轮）"},
    "search_all": {"args": {"query": "string", "k": "1–8，默认 6"},
                   "desc": "在全部 13 席的知识库里检索（总指挥的超级大脑），返回片段与出处"},
    "read_skill": {"args": {"seat_id": "string", "section": "string（可选，技能里的标题关键字）"},
                   "desc": "读某一席技能文件（SKILL.md）的目录或某一节原文"},
    "market_now": {"args": {}, "desc": "当前冻结快照的市场/板块摘要（宽度、情绪、涨停生态、主线、缺口）"},
    "list_data_api": {"args": {"tag": "string（可选，例如 融资融券/互联互通/板块指数）", "query": "string（可选，按关键词找）"},
                      "desc": "看猫爪数据有哪些接口可查（按 20 个分类；含两融、北向、板块资金流、龙虎榜、情绪周期等）"},
    "data_api": {"args": {"op": "string（接口名或别名，例如 reference_margin_summary / margin / northbound / theme_fundflow）",
                          "params": "object（接口参数，只接受该接口声明过的参数；日期 YYYYMMDD 或 YYYY-MM-DD，代码 6 位）"},
                 "desc": "查猫爪（Numcat）只读数据：89 个接口，含行情、两融、北向、板块资金流、龙虎榜、财务、资讯"},
}

COMMANDER_SYSTEM = """你是交易圆桌的总指挥。你手下有 13 位独立的席位分析师，每人有自己的书、技能与规则脚本。
你不是第 14 个席位：你不站队，你负责把人派对、把账算清、把分歧摊开。

工作方式：
1. 先判断问题类型：选股 / 看个股 / 看板块与市场 / 只是聊天学习。
2. 需要席位干活就 dispatch：一次最多 3 席，问题写清楚（标的、时间范围、要他回答什么）；
   选谁看 list_seats 里每席「能判断什么」，别让审查席去选股。
3. 自己也能查：search_all 在全部席位知识库找依据；read_skill 读某席技能原文；
   market_now 看当前快照；list_data_api 看猫爪有哪些接口；data_api 查猫爪数据（行情/两融/北向/板块资金流/龙虎榜/财务/资讯等）。
   查数据前先用 list_data_api 确认接口名与参数，不要猜；只传该接口声明过的参数。
4. 最后给汇总报告：结论（3-6 句）、各席要点（点名到席位）、分歧、下一步看什么、缺什么、数据来源与时间。
5. 不给买卖指令、目标价、胜率；数字只能来自工具返回；没有出处的不要说；缺数据就写进 missing_inputs。

工具（一次只调一个）：
{tools}

输出必须是 JSON，二选一：
1) 继续干活：{{"step": "tool", "tool": "dispatch", "args": {{"seats": ["trading-wyckoff-v1"], "question": "…", "symbols": ["000001.SZ"]}}, "why": "…"}}
2) 给报告：{{"step": "final", "report": {{"summary": "3-6 句", "seat_findings": [{{"seat_id": "trading-wyckoff-v1", "point": "他说的要点"}}], "disagreements": [], "watch_next": [], "missing_inputs": [], "data_notes": ["用了什么数据、什么时候取的"], "action": "explain_only"}}}}

最多 {max_steps} 步；没派活也可以直接给报告（聊天学习场景）。
"""


class ToolError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _tool_help() -> str:
    lines = []
    for name, spec in TOOLS.items():
        args = "；".join(f"{k}: {v}" for k, v in spec["args"].items()) or "无参数"
        lines.append(f"- {name}（{args}）—— {spec['desc']}")
    return "\n".join(lines)


def _seat_name(seat_id: str) -> str:
    return next((s["name"] for s in sr.definitions(SKILLS_DIR) if s["seat_id"] == seat_id), seat_id)


def _list_seats() -> list[dict]:
    out = []
    for s in sr.definitions(SKILLS_DIR):
        cap = sr.CAPABILITIES.get(s["seat_id"], {})
        out.append({"seat_id": s["seat_id"], "name": s["name"], "role": s.get("role"),
                    "horizons": s.get("horizons"),
                    "can_judge": (cap.get("a_level") or [])[:4],
                    "cannot_judge": (cap.get("b_level") or [])[:2] + (cap.get("c_level") or [])[:2],
                    "script": cap.get("script")})
    return out


def _dispatch(db, settings, args: dict, *, snapshot, replay_id, state: dict) -> dict:
    seats = args.get("seats") or []
    if isinstance(seats, str):
        seats = [seats]
    seats = [str(s).strip() for s in seats if str(s).strip()]
    known = {s["seat_id"] for s in sr.definitions(SKILLS_DIR)}
    bad = [s for s in seats if s not in known]
    if bad:
        raise ToolError("TOOL_BAD_ARGS", f"没有这些席位：{'、'.join(bad)}（先用 list_seats 看）")
    if not 1 <= len(seats) <= 3:
        raise ToolError("TOOL_BAD_ARGS", "一次派 1–3 席")
    if state.get("dispatches", 0) >= 2:
        raise ToolError("TOOL_LIMIT", "整场已经派过两轮了：先看结果，别再派")
    question = str(args.get("question") or "").strip()
    if not question:
        raise ToolError("TOOL_BAD_ARGS", "派活要写清楚问题（question）")
    symbols = [str(s) for s in (args.get("symbols") or [])][:5] or None
    results = []
    for sid in seats:
        try:
            r = seat_agent.agent_chat(db, settings, seat_id=sid, question=question, snapshot=snapshot,
                                      symbols=symbols, replay_id=replay_id, max_steps=2)
            ans = r["answer"]
            results.append({"seat_id": sid, "seat_name": r["seat_name"], "ok": True,
                            "answer": ans["answer"],
                            "kb_refs": [{k: ref.get(k) for k in ("kb_id", "book", "locator")}
                                        for ref in ans["kb_refs"]][:4],
                            "missing_inputs": ans.get("missing_inputs") or [],
                            "uncertainties": ans.get("uncertainties") or [],
                            "steps": len(r.get("steps") or []), "tokens": r.get("tokens"),
                            "audit": r["audit"]["model_run_id"]})
        except seat_agent.AgentRejected as ex:
            results.append({"seat_id": sid, "seat_name": _seat_name(sid), "ok": False,
                            "reason": f"{ex.code}：{str(ex)[:160]}"})
        except ds.LlmUnavailable as ex:
            results.append({"seat_id": sid, "seat_name": _seat_name(sid), "ok": False,
                            "reason": f"{ex.code}：{str(ex)[:160]}"})
    state["dispatches"] = state.get("dispatches", 0) + 1
    ok_n = sum(1 for r in results if r["ok"])
    return {"ok": True, "summary": f"派了 {len(seats)} 席（成功 {ok_n}）", "payload": results}


def _search_all(args: dict) -> dict:
    q = str(args.get("query") or "").strip()
    if not q:
        raise ToolError("TOOL_BAD_ARGS", "search_all 需要 query")
    try:
        k = max(1, min(8, int(args.get("k") or 6)))
    except (TypeError, ValueError):
        k = 6
    hits: list[dict] = []
    for s in sr.definitions(SKILLS_DIR):
        r = kb_service.search(s["seat_id"], q, k=2)
        for h in r["results"]:
            hits.append({"seat_id": s["seat_id"], "seat_name": s["name"], "kb_id": h["kb_id"],
                         "book": h.get("book"), "locator": h.get("locator"), "kind": h.get("kind"),
                         "score": h.get("score") or 0, "text": (h.get("text") or "")[:400]})
    hits.sort(key=lambda x: -float(x["score"]))
    top = hits[:k]
    return {"ok": True, "summary": f"全席检索「{q}」命中 {len(hits)} 条，取前 {len(top)}", "payload": top}


def _market_now(snapshot) -> dict:
    if not snapshot:
        return {"ok": False, "summary": "当前没有快照：先「采集最新快照」，或用 data_api 查猫爪", "payload": {}}
    rep = postmarket_service.build(snapshot)
    sec = {s["id"]: s for s in rep["sections"]}
    breadth = [(i["label"], i["value"]) for i in (sec.get("breadth") or {}).get("items", [])][:6]
    leaders = [(i["label"], i["value"]) for i in (sec.get("leaders") or {}).get("items", [])][:4]
    return {"ok": True, "summary": rep["summary"].get("headline") or "市场摘要", "payload": {
        "session_date": rep.get("session_date"), "quote_date": rep.get("quote_date"),
        "tone": rep["summary"].get("tone"), "breadth": breadth, "leaders": leaders,
        "gaps": [g["item"] for g in rep.get("gaps") or []][:8],
        "generated_at": rep.get("generated_at")}}


def _list_data_api(args: dict) -> dict:
    tag = str(args.get("tag") or "").strip() or None
    query = str(args.get("query") or "").strip() or None
    rows = data_bridge.list_ops(tag=tag, query=query)
    if not rows:
        return {"ok": False, "summary": f"没有匹配的接口（分类：{'、'.join(data_bridge.tags())}）", "payload": []}
    by_tag: dict[str, list] = {}
    for r in rows:
        by_tag.setdefault(r["tag"] or "未分类", []).append(r)
    return {"ok": True, "summary": f"{len(rows)} 个接口可查（{len(by_tag)} 个分类）",
            "payload": [{"tag": k, "ops": [{"op": o["op"], "summary": o["summary"], "params": o["params"]}
                                           for o in v[:12]]} for k, v in by_tag.items()][:12]}


def _data_api(settings, args: dict) -> dict:
    op = str(args.get("op") or args.get("kind") or "").strip()
    if not op:
        raise ToolError("TOOL_BAD_ARGS", "data_api 需要 op（先用 list_data_api 看接口名）")
    params = args.get("params") if isinstance(args.get("params"), dict) else {}
    # 兼容旧写法：直接给 symbols/date
    for k in ("symbols", "date", "tradedate"):
        if args.get(k) not in (None, "", []):
            params.setdefault(k, args[k])
    res = data_bridge.query(settings, op, params)
    if not res.get("ok"):
        return {"ok": False, "summary": res.get("reason", "查询失败"), "payload": {"op": op}}
    rows = res["rows"][:8]
    return {"ok": True,
            "summary": f"{res['label']}（{res['op']}）：{res['count']} 行（{res['captured_at']}）",
            "payload": {"op": res["op"], "label": res["label"], "params": res["params"], "rows": rows,
                        "count": res["count"], "captured_at": res["captured_at"], "note": res["note"]}}


def _run_tool(name: str, args: dict, *, db, settings, snapshot, replay_id, state) -> dict:
    args = args or {}
    if name == "list_seats":
        seats = _list_seats()
        return {"ok": True, "summary": f"{len(seats)} 席可派", "payload": seats}
    if name == "dispatch":
        return _dispatch(db, settings, args, snapshot=snapshot, replay_id=replay_id, state=state)
    if name == "search_all":
        return _search_all(args)
    if name == "read_skill":
        seat_id = str(args.get("seat_id") or "").strip()
        if seat_id not in {s["seat_id"] for s in sr.definitions(SKILLS_DIR)}:
            raise ToolError("TOOL_BAD_ARGS", f"没有这个席位：{seat_id}")
        from . import seat_tools
        out = seat_tools.read_skill_text(seat_id, args.get("section"))
        if not out.get("ok"):
            return {"ok": False, "summary": out.get("reason", "读不到技能文件"), "payload": {"headings": out.get("headings") or []}}
        return {"ok": True, "summary": f"读了 {_seat_name(seat_id)} 的技能：{out.get('section') or '目录'}",
                "payload": {"seat_id": seat_id, "section": out.get("section"),
                            "headings": out.get("headings"), "text": out["text"]}}
    if name == "market_now":
        return _market_now(snapshot)
    if name == "list_data_api":
        return _list_data_api(args)
    if name == "data_api":
        return _data_api(settings, args)
    raise ToolError("TOOL_UNKNOWN", f"没有这个工具：{name}（可用：{'、'.join(TOOLS)}）")


def validate_report(data: dict, dispatched: dict[str, str], haystack: str) -> dict:
    report = data.get("report") if isinstance(data.get("report"), dict) else None
    if report is None and isinstance(data.get("answer"), dict):
        report = data["answer"]
    if not isinstance(report, dict):
        raise seat_agent.AgentRejected("LLM_BAD_PROTOCOL", "总指挥没有给出 report")
    summary = str(report.get("summary") or "").strip()
    if not summary:
        raise seat_agent.AgentRejected("LLM_EMPTY_REPORT", "报告没有结论")
    findings = []
    for f in report.get("seat_findings") or []:
        if not isinstance(f, dict):
            continue
        sid = str(f.get("seat_id") or "").strip()
        point = str(f.get("point") or "").strip()
        if not sid or not point:
            continue
        if sid not in dispatched:
            raise seat_agent.AgentRejected("LLM_UNKNOWN_SEAT_FINDING",
                                           f"报告点了没派过活的席位：{sid}",
                                           [{"field": "seat_findings", "reason": sid}])
        findings.append({"seat_id": sid, "seat_name": dispatched[sid], "point": point[:400]})
    policy = llm.validate_text(summary + " " + " ".join(f["point"] for f in findings))
    if policy["status"] != "accepted":
        raise seat_agent.AgentRejected("LLM_POLICY", "报告包含目标价/胜率/买卖指令等禁用内容",
                                       [{"field": "summary", "reason": c} for c in policy["policy_errors"]])
    stray = [n for n in seat_agent._numbers(summary) if n not in haystack]  # noqa: SLF001
    if stray:
        raise seat_agent.AgentRejected("LLM_NUMBER_NOT_IN_FACTS", "报告里的数字不是来自工具返回",
                                       [{"field": "summary", "reason": f"数字 {n} 没有出处"} for n in stray[:5]])
    out = {"summary": summary, "seat_findings": findings,
           "disagreements": [str(x)[:300] for x in report.get("disagreements") or []][:8],
           "watch_next": [str(x)[:300] for x in report.get("watch_next") or []][:8],
           "missing_inputs": [str(x)[:200] for x in report.get("missing_inputs") or []][:8],
           "data_notes": [str(x)[:300] for x in report.get("data_notes") or []][:8],
           "action": "explain_only"}
    if report.get("action") not in (None, "explain_only"):
        raise seat_agent.AgentRejected("LLM_POLICY", "报告试图给出行动指令",
                                       [{"field": "action", "reason": str(report.get("action"))}])
    return out


def run(db, settings, *, question: str, snapshot: dict | None = None, symbols: list[str] | None = None,
        replay_id: str | None = None, client=None, max_steps: int = 6,
        prefer_seats: list[str] | None = None) -> dict:
    if replay_id:
        raise seat_agent.AgentRejected("LLM_DISABLED_IN_REPLAY", "历史训练/回放上下文禁用模型：模型记忆会污染盲测")
    question = (question or "").strip()
    if not question:
        raise seat_agent.AgentRejected("VALIDATION_ERROR", "问题不能为空")
    client = client or seat_agent.client_for(settings, None)
    ok, why = client.available()
    if not ok:
        raise ds.LlmUnavailable("LLM_DISABLED", why)

    max_steps = max(2, min(8, int(max_steps or 6)))
    system = COMMANDER_SYSTEM.format(tools=_tool_help(), max_steps=max_steps)
    state: dict = {"dispatches": 0}
    haystack_parts: list[str] = []
    dispatched: dict[str, str] = {}
    steps: list[dict] = []
    details: list[dict] = []
    tokens = 0
    last_raw = None
    user = (f"用户的问题：{question}\n\n"
            "先用 list_seats 看谁能干这活（如果已经清楚也可以直接派），再决定要不要派活。"
            "最后按约定给 report。\n")
    wanted = [str(s) for s in (prefer_seats or []) if str(s).strip()][:4]
    if wanted:
        user += f"\n【用户希望优先派这几席】{('、'.join(wanted))}（除非与问题明显不匹配，否则照办）\n"
    try:
        for i in range(max_steps):
            res = client.chat_json(system, user, max_tokens=1400)
            tokens += int(res.get("tokens") or 0)
            last_raw = res.get("raw")
            data = res.get("data") or {}
            step = str(data.get("step") or "").strip().lower()
            if step == "final" or (not step and ("report" in data or "answer" in data)):
                report = validate_report(data, dispatched, " ".join(haystack_parts))
                audit = seat_agent._audit(db, settings, client, "commander", prompt=f"{system}\n\n{user}",  # noqa: SLF001
                                          output=last_raw, status="ok", tokens=tokens)
                return {"status": "ok", "question": question, "report": report, "steps": steps,
                        "dispatches": details,
                        "dispatched": [{"seat_id": k, "seat_name": v} for k, v in dispatched.items()],
                        "model": res.get("model"), "tokens": tokens,
                        "audit": {"model_run_id": audit["model_run_id"], "evidence_sha256": audit["evidence_sha256"]}}
            if step != "tool":
                raise seat_agent.AgentRejected("LLM_BAD_PROTOCOL", "总指挥既没说 step=tool，也没给 report",
                                               [{"field": "step", "reason": step[:40]}])
            name = str(data.get("tool") or "").strip()
            args = data.get("args") if isinstance(data.get("args"), dict) else {}
            try:
                out = _run_tool(name, args, db=db, settings=settings, snapshot=snapshot,
                                replay_id=replay_id, state=state)
            except ToolError as ex:
                out = {"ok": False, "summary": f"{ex.code}：{ex}", "payload": {}}
            if name == "dispatch" and out.get("ok"):
                for r in out["payload"]:
                    details.append(r)
                    if r.get("ok"):
                        dispatched[r["seat_id"]] = r["seat_name"]
                        haystack_parts.append(r.get("answer") or "")
            payload_text = out["payload"] if isinstance(out["payload"], str) else json.dumps(out["payload"], ensure_ascii=False)
            haystack_parts.append(payload_text)
            steps.append({"n": i + 1, "tool": name, "args": args, "ok": out["ok"], "summary": out["summary"]})
            user += (f"\n\n【工具结果 · {name}】{out['summary']}\n{payload_text[:1800]}\n\n"
                     f"本场成功返回的席位只有：{json.dumps(list(dispatched), ensure_ascii=False)}。"
                     "report.seat_findings 只能填写这个列表内的 seat_id；没有成功结果就用空数组，失败写入 missing_inputs。\n"
                     "继续：还需要谁/什么数据就用工具；够了就输出 step=final 的 report。")
        raise seat_agent.AgentRejected("LLM_STEP_LIMIT", f"用了 {max_steps} 步还没给报告",
                                       [{"field": "steps", "reason": "limit"}])
    except seat_agent.AgentRejected as rej:
        seat_agent._audit(db, settings, client, "commander", prompt=f"{system}\n\n{user}",  # noqa: SLF001
                          output=None, status="invalid_output", tokens=tokens, error_codes=[rej.code])
        raise
    except ds.LlmUnavailable as ex:
        seat_agent._audit(db, settings, client, "commander", prompt=f"{system}\n\n{user}",  # noqa: SLF001
                          output=None, status="failed", tokens=tokens, error_codes=[ex.code])
        raise
