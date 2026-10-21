"""模型解释层适配（SEAT-006）：默认 disabled；任何模型输出都要过事实与政策校验。

- 规则程序产生数值、条件判断和证据链；模型只能解释既有事实、提出反证，不能改字段、补价格或改规则结论；
- 本仓不内置任何供应商、不读取或要求 API 密钥；llm_mode=required 而没有已配置的 provider → 422；
  optional 时降级为仅规则，并在结果里写明；
- validate_claims：引用不存在 / 截点之后的事实、数值或单位与事实不符 → invalid_output；
- validate_text：目标价、无证胜率、必涨保证、替用户下单的指令 → invalid_output。
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..domain import clock


class ProviderNotConfigured(Exception):
    pass


class DisabledProvider:
    provider_id = None
    model_id = None

    def explain(self, *_a, **_k):
        return None


_provider_factory = None


def set_provider_factory(fn) -> None:
    """测试/验收注入离线确定性 provider（不联网、不读密钥）。生产默认没有任何 provider。"""
    global _provider_factory
    _provider_factory = fn


def injected_provider():
    return _provider_factory() if _provider_factory else None


def facts_from_snapshot(snap: dict, extra: list[dict] | None = None) -> dict[str, dict]:
    """事实表：快照证据（按 evidence_id 与 value.fact_id 两种键）+ 额外独立事实（可能晚于截点）。"""
    facts: dict[str, dict] = {}
    for e in list(snap.get("evidence") or []) + list(extra or []):
        v = e.get("value")
        rec = {"available_at": e.get("available_at"), "unit": e.get("unit")}
        if isinstance(v, dict):
            for k in ("price", "money_minor", "value"):
                if k in v:
                    rec[k] = v[k]
        elif e.get("unit") == "price_decimal":
            rec["price"] = v
        elif e.get("unit") == "money_minor":
            rec["money_minor"] = v
        else:
            rec["value"] = v
        facts[e["evidence_id"]] = rec
        if isinstance(v, dict) and v.get("fact_id"):
            facts[v["fact_id"]] = rec
    return facts


def configured_providers(runtime_dir: Path) -> dict:
    """本地配置的 provider 列表（只登记标识，不保存密钥）。v1 默认没有任何 provider。"""
    f = runtime_dir / "config" / "llm_providers.json"
    if not f.exists():
        return {}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return {p["provider_id"]: p for p in d.get("providers", []) if p.get("enabled")}
    except (ValueError, KeyError):
        return {}


def resolve(llm_mode: str, provider_id: str | None, model_id: str | None, runtime_dir: Path):
    """返回 (provider 或 None, 降级说明)。required 且不可用 → ProviderNotConfigured。"""
    if llm_mode == "disabled":
        return None, "llm_mode=disabled：仅规则"
    provs = configured_providers(runtime_dir)
    if provider_id and provider_id in provs:
        # v1 不实现任何网络调用：已登记的 provider 也只能由后续明确任务接入
        msg = f"provider {provider_id} 已登记但 v1 未实现调用适配"
    else:
        msg = "没有已配置且允许使用的模型 provider"
    if llm_mode == "required":
        raise ProviderNotConfigured(msg)
    return None, f"llm_mode=optional 已降级为仅规则：{msg}"


# ── 输出校验 ──
_POLICY = [
    (re.compile(r"目标价|目标位|看到\s*\d+(\.\d+)?\s*元|上看\s*\d+"), "unsupported_target_or_probability"),
    (re.compile(r"胜率\s*\d+(\.\d+)?\s*%|成功率\s*\d+|概率\s*\d+(\.\d+)?\s*%"), "unsupported_target_or_probability"),
    (re.compile(r"必涨|稳赚|保本|保证收益|一定涨|稳了"), "unsupported_target_or_probability"),
    (re.compile(r"直接买|可以买|立即买入|马上买|满仓|梭哈|帮你下单"), "directive_trading_instruction"),
    (re.compile(r"主力(抢筹|建仓|出货)|游资抢筹"), "unverifiable_intent_claim"),
]

PRICE_UNITS = {"CNY", "price_decimal", "CNY/share", "yuan"}
MONEY_UNITS = {"CNY", "money_minor", "fen"}


def validate_text(text: str) -> dict:
    errs = sorted({code for pat, code in _POLICY if pat.search(text or "")})
    return {"status": "invalid_output" if errs else "accepted", "policy_errors": errs,
            "execution_eligibility": False}


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def validate_claims(facts: dict[str, dict], claims: list[dict], cutoff_at: str | None = None) -> dict:
    """facts: fact_id → {"price"|"money_minor"|"value", "unit", "available_at"}。"""
    unknown, future, codes = [], [], set()
    for c in claims:
        fid = c.get("fact_id")
        f = facts.get(fid)
        if f is None:
            unknown.append(fid)
            codes.add("unknown_fact_id")
            continue
        av = f.get("available_at")
        if cutoff_at and av and clock.parse_utc(av) > clock.parse_utc(cutoff_at):
            future.append(fid)
            codes.add("future_fact")
            continue
        if "price" in c:
            if _dec(c["price"]) != _dec(f.get("price")):
                codes.add("claim_value_mismatch")
            if c.get("unit") and c["unit"] not in PRICE_UNITS:
                codes.add("claim_unit_mismatch")
        if "money_minor" in c:
            if str(c["money_minor"]) != str(f.get("money_minor")):
                codes.add("claim_value_mismatch")
            if c.get("unit") and c["unit"] not in MONEY_UNITS:
                codes.add("claim_unit_mismatch")
        if "value" in c and f.get("value") is not None and str(c["value"]) != str(f["value"]):
            codes.add("claim_value_mismatch")
    status = "invalid_output" if codes else "accepted"
    return {"status": status, "unknown_fact_ids": unknown, "future_fact_ids": future, "error_codes": sorted(codes),
            "execution_eligibility": False if codes else None}
