"""下单前心理自检（PLAN-002 / AC-039 / AC-103）。

- 规则来源四类分开：author_quote（原文，必须可定位到书与章/页）、editorial_summary（编者翻译成可执行问题）、
  engineering_policy（系统工程限制，引用 ADR）、user_rule（用户自己写）；工程规则永远不改写成作者原话；
- 自检只记录事前回答：must_yes 未勾 → 不放行；warn_if_no 未勾 → 记警告；info 只展示；
- 不输出任何心理健康/情绪诊断（emotional_health_diagnosis 恒为 null），不据此评价个人。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..web.errors import ApiError

RULES_FILE = Path(__file__).resolve().parent / "psychology_rules.v1.json"
ORIGINS = ("author_quote", "editorial_summary", "engineering_policy", "user_rule")
AUTHOR_REF_RE = re.compile(r"(p\.\s*\d+|第\s*[一二三四五六七八九十百\d]+\s*[章节页]|页\s*\d+|\d+\s*页|:\d+$)")
ADR_RE = re.compile(r"^(ADR-[A-Za-z0-9_-]+)")


def load_rules() -> dict:
    return json.loads(RULES_FILE.read_text(encoding="utf-8"))


def validate_origin(rule: dict) -> dict:
    """来源校验：没有可核验出处的「作者原话」拒收（422）；工程规则保持工程来源。"""
    origin = rule.get("rule_origin") or rule.get("origin_type")
    ref = rule.get("origin_ref") if "origin_ref" in rule else rule.get("source_ref")
    page = rule.get("page")
    if origin not in ORIGINS:
        raise ApiError(422, "RULE_ORIGIN_INVALID", f"未知来源类型 {origin!r}", [{"field": "rule_origin", "reason": "unknown"}])
    if origin == "author_quote":
        locatable = bool(ref) and (bool(AUTHOR_REF_RE.search(str(ref))) or page is not None)
        if not locatable:
            raise ApiError(422, "AUTHOR_QUOTE_UNVERIFIABLE", "标为作者原话但没有可核验的出处（书名与章/页）",
                           [{"field": "origin_ref", "reason": "author_quote requires a locatable source reference"}])
    if origin == "engineering_policy" and not (ref and ADR_RE.match(str(ref))):
        raise ApiError(422, "ENGINEERING_POLICY_UNREFERENCED", "工程规则需要引用决策记录（ADR-…）",
                       [{"field": "origin_ref", "reason": "engineering_policy requires ADR reference"}])
    return {"rule_origin": origin, "origin_ref": ref, "accepted": True, "rewritten_origin": None}


def submit(rules: list[dict], answers: dict[str, bool]) -> dict:
    blocked, warnings = [], []
    for r in rules:
        chk = r.get("check", "info")
        v = bool(answers.get(r["rule_id"]))
        if chk == "must_yes" and not v:
            blocked.append(r["rule_id"])
        elif chk == "warn_if_no" and not v:
            warnings.append(r["rule_id"])
    actionable = [r for r in rules if r.get("check") in ("must_yes", "warn_if_no")]
    return {"checklist_completion": all(bool(answers.get(r["rule_id"])) for r in actionable) if actionable else True,
            "allowed_to_proceed": not blocked, "blocked_by": blocked, "warnings": warnings,
            "emotional_health_diagnosis": None,
            "note": "自检只记录你在下单前的回答；系统不做心理或情绪诊断"}
