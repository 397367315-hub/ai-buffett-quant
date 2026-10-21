"""席位工具箱（ADR-012）：让每一席的模型在一次回答里自己决定「接下来查什么」。

工具都是**只读或受控**的：
- `search_kb`：只检索这一席自己的知识库（原书 / 精读 / 技能），拿回可引用的片段（kb_id + 定位）；
- `read_skill`：读这一席技能文件（SKILL.md）的某一节或目录——这是「调用技能」的文字部分；
- `get_facts`：从当前冻结快照取事实（指数/宽度/涨停/个股报价），取到的事实才允许被引用；
- `get_rule_run`：看这一席最近一次跑完的规则结论（判定、逐条条件、反证）；
- `run_skill_script`：**真的跑一遍这一席自己的规则脚本**（同一快照、最多 3 只票、20 秒超时、每轮最多一次），
  返回映射后的结论。脚本是受控子进程，不联网、不写业务库。

所有工具都不改数据、不下单、不访问这一席以外的资料。步数与预算由调用方（seat_agent.agent_chat）限制。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import SKILLS_DIR
from . import kb_service

MAX_TEXT = 600          # 单个片段回给模型的字符数
MAX_SKILL_TEXT = 1800
SCRIPT_TIMEOUT = 20.0
SCRIPT_MAX_SYMBOLS = 3

TOOL_SPECS = {
    "search_kb": {"args": {"query": "string（必填）", "k": "1-8，默认 5"},
                  "desc": "检索这一席自己的知识库（原书正文 / 精读 / 技能），返回可引用的片段与定位"},
    "read_skill": {"args": {"section": "string（可选，技能文件里的标题关键字）"},
                   "desc": "读这一席技能文件（SKILL.md）的目录或某一节原文"},
    "get_facts": {"args": {"symbol": "string（可选，例如 000001.SZ）"},
                  "desc": "从当前冻结快照取事实（市场宽度、涨停、成交、个股报价等）；只有取到的事实才能引用"},
    "get_rule_run": {"args": {"symbol": "string（可选）"},
                     "desc": "看这一席最近一次跑完的规则结论（判定、逐条条件、反证），只读"},
    "run_skill_script": {"args": {"symbols": "string[]（可选，最多 3 只；不给就用最近一次运行的标的）"},
                         "desc": "真的重跑这一席自己的规则脚本（同一快照、20 秒超时、每轮最多一次）"},
}


def tool_help() -> str:
    lines = []
    for name, spec in TOOL_SPECS.items():
        args = "；".join(f"{k}: {v}" for k, v in spec["args"].items()) or "无参数"
        lines.append(f"- {name}（{args}）—— {spec['desc']}")
    return "\n".join(lines)


class ToolError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _clip(text: str, n: int = MAX_TEXT) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "……"


def _skill_path(seat_id: str) -> Path | None:
    p = Path(SKILLS_DIR) / seat_id / "SKILL.md"
    return p if p.is_file() else None


def _headings(md: str) -> list[tuple[int, str, int]]:
    out = []
    for m in re.finditer(r"^(#{1,4})\s+(.+)$", md, re.M):
        out.append((len(m.group(1)), m.group(2).strip(), m.start()))
    return out


def read_skill_text(seat_id: str, section: str | None = None) -> dict:
    p = _skill_path(seat_id)
    if p is None:
        return {"ok": False, "reason": "这一席没有技能文件（SKILL.md）", "text": ""}
    try:
        md = p.read_text(encoding="utf-8")
    except OSError as ex:
        return {"ok": False, "reason": f"读不到技能文件：{type(ex).__name__}", "text": ""}
    heads = _headings(md)
    if section:
        key = section.strip().lower()
        hit = next((h for h in heads if key in h[1].lower()), None)
        if hit is None:
            return {"ok": False, "reason": f"技能文件里没有这一节：{section}",
                    "headings": [h[1] for h in heads][:30], "text": ""}
        start = hit[2]
        end = next((h[2] for h in heads if h[2] > start and h[0] <= hit[0]), len(md))
        return {"ok": True, "section": hit[1], "text": _clip(md[start:end], MAX_SKILL_TEXT)}
    return {"ok": True, "section": None, "headings": [h[1] for h in heads][:30],
            "text": _clip(md, MAX_SKILL_TEXT)}


def _latest_run(db, seat_id: str, snapshot_id: str | None):
    rows = db.conn().execute("SELECT canonical_payload, replay_id FROM seat_runs WHERE seat_id=? AND replay_id IS NULL "
                             "AND run_state IN ('succeeded','partial') ORDER BY created_at DESC LIMIT 20",
                             (seat_id,)).fetchall()
    out = None
    for r in rows:
        obj = json.loads(r["canonical_payload"])
        if snapshot_id and obj.get("snapshot_id") != snapshot_id:
            continue
        out = obj
        break
    return out


def _rule_facts(rule: dict) -> dict:
    from . import seat_agent
    facts = {"rule.verdict": {"label": "规则判定", "value": rule["verdict_label"]},
             "rule.applicability": {"label": "规则适用性", "value": rule["applicability"]}}
    for i, c in enumerate(rule["conditions"][:16], 1):
        facts[f"rule.cond{i}"] = {"label": c["description"], "value": c["result"]}
    return facts


def run_tool(name: str, args: dict, *, db, settings, seat_id: str, snapshot: dict | None,
             facts: dict[str, dict], state: dict) -> dict:
    """执行一个工具，返回 {ok, summary, payload}；会把可用的事实并进 facts，把引用并进 state。"""
    args = args or {}
    if name == "search_kb":
        q = str(args.get("query") or "").strip()
        if not q:
            raise ToolError("TOOL_BAD_ARGS", "search_kb 需要 query")
        try:
            k = max(1, min(8, int(args.get("k") or 5)))
        except (TypeError, ValueError):
            k = 5
        res = kb_service.search(seat_id, q, k=k)
        hits = res["results"]
        for h in hits:
            state.setdefault("kb_hits", {})[h["kb_id"]] = h
        payload = [{"kb_id": h["kb_id"], "locator": h.get("locator"), "book": h.get("book"), "kind": h.get("kind"),
                    "text": _clip(h["text"])} for h in hits]
        return {"ok": True, "summary": f"检索「{q}」：{len(hits)} 条" + ("（这一席知识库没有命中）" if not hits else ""),
                "payload": payload}

    if name == "read_skill":
        out = read_skill_text(seat_id, args.get("section"))
        if not out["ok"]:
            return {"ok": False, "summary": out.get("reason", "读不到技能文件"),
                    "payload": {"headings": out.get("headings") or []}}
        state["skill_reads"] = state.get("skill_reads", 0) + 1
        label = out.get("section") or "开头与目录"
        return {"ok": True, "summary": f"读技能：{label}（{len(out['text'])} 字）",
                "payload": {"section": out.get("section"), "headings": out.get("headings"), "text": out["text"]}}

    if name == "get_facts":
        from . import seat_agent
        sym = str(args.get("symbol") or "").strip()
        got = seat_agent.facts_from_snapshot(snapshot, [sym] if sym else None)
        if not got:
            return {"ok": False, "summary": "当前没有快照，取不到事实", "payload": {}}
        facts.update(got)
        lines = [f"{k} = {v['value']}（{v.get('unit') or ''}）" for k, v in list(got.items())[:40]]
        return {"ok": True, "summary": f"取事实：{len(got)} 条" + (f"（{sym}）" if sym else ""),
                "payload": "\n".join(lines)}

    if name == "get_rule_run":
        from . import seat_agent
        sym = str(args.get("symbol") or "").strip()
        run = _latest_run(db, seat_id, (snapshot or {}).get("snapshot_id"))
        if run is None:
            return {"ok": False, "summary": "这一席在当前快照上还没有跑完的规则运行", "payload": {}}
        rule = seat_agent.rule_view(run, sym)
        facts.update(_rule_facts(rule))
        return {"ok": True, "summary": f"规则结论：{rule['verdict_label']}（{len(rule['conditions'])} 条条件）",
                "payload": {"verdict": rule["verdict_label"], "explanation": rule["explanation"],
                            "conditions": [f"[{c['result']}] {c['description']}" for c in rule["conditions"]],
                            "counter_evidence": rule["counter_evidence"]}}

    if name == "run_skill_script":
        from . import seat_runner
        from ..data.evidence_store import EvidenceStore
        if state.get("script_runs", 0) >= 1:
            raise ToolError("TOOL_LIMIT", "这一轮已经跑过一次脚本了；先看结果再决定要不要换票")
        if snapshot is None:
            return {"ok": False, "summary": "当前没有快照，跑不了规则脚本", "payload": {}}
        raw_syms = args.get("symbols") or []
        if isinstance(raw_syms, str):
            raw_syms = [x.strip() for x in raw_syms.replace("，", ",").split(",") if x.strip()]
        syms = [str(s).split(".")[0] for s in raw_syms][:SCRIPT_MAX_SYMBOLS]
        if not syms:
            run = _latest_run(db, seat_id, snapshot.get("snapshot_id"))
            syms = [s.split(".")[0] for s in ((run or {}).get("_ctx", {}).get("symbols") or [])][:SCRIPT_MAX_SYMBOLS]
        if not syms:
            return {"ok": False, "summary": "没有指定标的，也没有可沿用的历史标的", "payload": {}}
        store = EvidenceStore(settings.evidence_dir)
        mf = seat_runner.materialize(db, snapshot, store)
        from ..domain import seat_registry as sr
        missing = [m for m in mf.get("missing_inputs") or [] if m.split("(")[0] in sr.hard_required(seat_id)]
        if missing:
            return {"ok": False, "summary": "缺必需输入，未运行脚本：" + "、".join(missing[:5]), "payload": {}}
        raw, sha, err = seat_runner.run_script(seat_id, snapshot, mf, syms, store, SCRIPT_TIMEOUT)
        state["script_runs"] = state.get("script_runs", 0) + 1
        if raw is None:
            return {"ok": False, "summary": f"脚本没有跑出结果：{err}", "payload": {"symbols": syms}}
        seat = next((s for s in sr.definitions(SKILLS_DIR) if s["seat_id"] == seat_id), {})
        horizon = "multi" if len(seat.get("horizons") or []) > 1 else (seat.get("horizons") or ["short"])[0]
        code2sym = {(i.get("symbol") or "").split(".")[0]: i["symbol"] for i in snapshot.get("instruments") or []}
        full = [code2sym.get(s, s) for s in syms]
        mapped = seat_runner.map_output(seat_id, raw, snapshot, full, horizon, [], missing)
        facts["script.verdict"] = {"label": "脚本判定", "value": str(mapped.get("verdict"))}
        return {"ok": True, "summary": f"跑完自己的脚本（{', '.join(syms)}）：{mapped.get('verdict')}",
                "payload": {"verdict": mapped.get("verdict"), "applicability": mapped.get("applicability"),
                            "explanation": _clip(mapped.get("explanation") or "", 400),
                            "conditions": [f"[{c['result']}] {c['description']}"
                                           for c in (mapped.get("condition_results") or [])][:12],
                            "raw_sha256": sha}}

    raise ToolError("TOOL_UNKNOWN", f"没有这个工具：{name}（可用：{'、'.join(TOOL_SPECS)}）")
