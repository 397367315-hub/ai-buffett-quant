"""模型相关接口（ADR-007 / ADR-008 / ADR-009）：状态、模型存档、单席问答、圆桌联动、盘后复盘解读。

默认关闭：没有密钥时，状态接口如实说「关闭」，其它接口返回 503 `LLM_DISABLED`；
回放/训练上下文一律拒绝（模型记忆会污染盲测）；所有调用都落 `model_runs` 与证据仓（脱敏）。
"""
from __future__ import annotations

import time

from ...adapters.deepseek import LlmUnavailable
from ...adapters import llm_registry
from ...services import seat_agent, snapshot_service
from ...config import SKILLS_DIR
from ..app import Ctx, handler
from ..errors import ApiError


def _client(ctx: Ctx, seat_id: str | None = None):
    return seat_agent.client_for(ctx.settings, seat_id)


def _map_error(ex: Exception) -> ApiError:
    if isinstance(ex, LlmUnavailable):
        return ApiError(503, ex.code, str(ex))
    if isinstance(ex, seat_agent.AgentRejected):
        return ApiError(422, ex.code, str(ex), ex.details)
    raise ex


def _map_profile_error(ex: llm_registry.ProfileError) -> ApiError:
    details = [{"field": ex.field, "reason": ex.code}] if ex.field else []
    return ApiError(422, ex.code, str(ex), details)


def _summary_of(st: dict, prof: dict) -> dict:
    """返回该存档在状态列表里的那一行（带 enabled / reason / key_configured），没有就退回原字典。"""
    return next((p for p in st.get("profiles") or [] if p["id"] == prof["id"]), prof)


def _key_env_for(ctx: Ctx, profile_id: str) -> str:
    prof = llm_registry.build(ctx.settings, llm_registry.config_for(ctx.settings, profile_id))
    return prof.api_key_env


def _apply_secret(ctx: Ctx, prof: dict, body: dict) -> None:
    """把页面里填的密钥写进该存档自己的私有条目（不回显、不写日志）。"""
    pid = prof["id"]
    name = _key_env_for(ctx, pid)
    if body.get("clear_api_key"):
        llm_registry.clear_secret(ctx.settings, name, profile_id=pid)
    key = str(body.get("api_key") or "").strip()
    if key:
        if len(key) < 8:
            raise ApiError(422, "VALIDATION_ERROR", "密钥太短", [{"field": "api_key", "reason": "min 8"}])
        llm_registry.save_secret(ctx.settings, name, key, profile_id=pid)


@handler("get_api_v1_llm_status")
def llm_status(ctx: Ctx):
    return 200, llm_registry.status(ctx.settings)


@handler("post_api_v1_llm_config")
def llm_config(ctx: Ctx):
    """在页面里改**当前存档**：写运行目录的 config/llm.json；密钥单独存私有文件，接口不回显。"""
    b = ctx.body or {}
    patch: dict = {}
    for k in ("provider", "model", "base_url", "api_key_env", "json_mode"):
        if k in b and b[k] is not None:
            if not isinstance(b[k], (str, bool)):
                raise ApiError(422, "VALIDATION_ERROR", f"{k} 类型不对", [{"field": k, "reason": "type"}])
            patch[k] = b[k]
    if b.get("daily_token_limit") is not None:
        try:
            lim = int(b["daily_token_limit"])
        except (TypeError, ValueError):
            raise ApiError(422, "VALIDATION_ERROR", "daily_token_limit 必须是整数",
                           [{"field": "daily_token_limit", "reason": "integer"}])
        if lim <= 0:
            raise ApiError(422, "VALIDATION_ERROR", "daily_token_limit 必须大于 0",
                           [{"field": "daily_token_limit", "reason": "positive"}])
        patch["daily_token_limit"] = lim
    try:
        cfg = llm_registry.update_active(ctx.settings, patch)
    except llm_registry.ProfileError as ex:
        raise _map_profile_error(ex)
    prof = next((p for p in (cfg.get("_doc") or {}).get("profiles") or [] if p["id"] == cfg.get("_profile_id")), None)
    if prof:
        _apply_secret(ctx, prof, b)
    st = llm_registry.status(ctx.settings)
    st["key_configured"] = st.get("key_source") not in (None, "none")
    return 200, st


@handler("post_api_v1_llm_profiles")
def llm_profiles(ctx: Ctx):
    """模型存档：保存 / 设为当前 / 复制 / 删除（像 CC Switch 那样存多份、随时切换）。"""
    b = ctx.body or {}
    action = str(b.get("action") or "save").strip()
    prof = b.get("profile") if isinstance(b.get("profile"), dict) else b
    try:
        if action == "save":
            activate = b.get("activate")
            saved, _ = llm_registry.save_profile(ctx.settings, prof,
                                                 activate=activate if isinstance(activate, bool) else None)
            _apply_secret(ctx, saved, b)
            st = llm_registry.status(ctx.settings)
            return 200, {**st, "profile": _summary_of(st, saved)}
        if action == "activate":
            llm_registry.activate_profile(ctx.settings, str(b.get("id") or prof.get("id") or ""))
            return 200, llm_registry.status(ctx.settings)
        if action == "duplicate":
            saved, _ = llm_registry.duplicate_profile(ctx.settings, str(b.get("id") or prof.get("id") or ""))
            st = llm_registry.status(ctx.settings)
            return 200, {**st, "profile": _summary_of(st, saved)}
        if action == "delete":
            llm_registry.delete_profile(ctx.settings, str(b.get("id") or prof.get("id") or ""))
            return 200, llm_registry.status(ctx.settings)
    except llm_registry.ProfileError as ex:
        raise _map_profile_error(ex)
    raise ApiError(422, "VALIDATION_ERROR", f"不认识的动作：{action}",
                   [{"field": "action", "reason": "save|activate|duplicate|delete"}])


@handler("post_api_v1_llm_models")
def llm_models(ctx: Ctx):
    """拉取某份存档可用的模型清单（GET {base}/models）。失败也 200 + ok=false，不打断页面。"""
    b = ctx.body or {}
    pid = str(b.get("id") or "").strip()
    try:
        return 200, llm_registry.fetch_models(ctx.settings, pid or None)
    except llm_registry.ProfileError as ex:
        raise _map_profile_error(ex)


@handler("post_api_v1_llm_test")
def llm_test(ctx: Ctx):
    """试连：用某份存档发一次极短请求（算进当天额度）。失败也返回 200 + ok=false，让页面如实显示原因。"""
    b = ctx.body or {}
    pid = str(b.get("id") or "").strip()
    try:
        cfg = llm_registry.config_for(ctx.settings, pid) if pid else llm_registry.load(ctx.settings)
    except llm_registry.ProfileError as ex:
        raise _map_profile_error(ex)
    provider = llm_registry.build(ctx.settings, cfg)
    base = {"provider": provider.provider_id, "model": provider.model,
            "profile_id": cfg.get("_profile_id"), "profile_name": cfg.get("_profile_name")}
    ok, why = provider.available()
    if not ok:
        return 200, {**base, "ok": False, "code": "LLM_DISABLED", "reason": why, "ms": 0}
    t0 = time.monotonic()
    try:
        out = provider.chat_json("你在做连通性自检，只输出 JSON。", '只回复 {"ok": true}',
                                 max_tokens=16, temperature=0.0)
    except LlmUnavailable as ex:
        return 200, {**base, "ok": False, "code": ex.code, "reason": str(ex),
                     "ms": round((time.monotonic() - t0) * 1000, 1)}
    return 200, {**base, "ok": True, "model": out.get("model") or provider.model,
                 "ms": round((time.monotonic() - t0) * 1000, 1), "tokens": out.get("tokens") or 0}



@handler("post_api_v1_seats_seat_id_ask")
def seat_ask(ctx: Ctx):
    if ctx.replay_id is not None:
        raise ApiError(409, "LLM_DISABLED_IN_REPLAY", "训练/回放上下文不调用模型：模型记忆会污染盲测")
    seat_id = ctx.path_params["seat_id"]
    from ...domain import seat_registry as sr
    if not any(s["seat_id"] == seat_id for s in sr.definitions(SKILLS_DIR)):
        raise ApiError(404, "SEAT_NOT_FOUND", f"没有这个席位：{seat_id}")
    b = ctx.body or {}
    question = str(b.get("question") or "").strip()
    if not question:
        raise ApiError(422, "VALIDATION_ERROR", "问题不能为空", [{"field": "question", "reason": "required"}])
    snap = None
    if b.get("snapshot_id"):
        snap = snapshot_service.get(ctx.db, b["snapshot_id"], ctx.replay_id)
        if snap is None:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或不可见")
    try:
        return 200, seat_agent.ask(ctx.db, ctx.settings, seat_id=seat_id, question=question, snapshot=snap,
                                   symbols=b.get("symbols") or None, use_search=bool(b.get("use_search")),
                                   replay_id=ctx.replay_id, client=_client(ctx, seat_id))
    except (LlmUnavailable, seat_agent.AgentRejected) as ex:
        raise _map_error(ex)


@handler("post_api_v1_seats_seat_id_chat")
def seat_chat(ctx: Ctx):
    """会自己查资料的问答（ADR-012）：模型可在步数内调工具，最后仍要过四道校验。"""
    if ctx.replay_id is not None:
        raise ApiError(409, "LLM_DISABLED_IN_REPLAY", "训练/回放上下文不调用模型：模型记忆会污染盲测")
    seat_id = ctx.path_params["seat_id"]
    from ...domain import seat_registry as sr
    if not any(s["seat_id"] == seat_id for s in sr.definitions(SKILLS_DIR)):
        raise ApiError(404, "SEAT_NOT_FOUND", f"没有这个席位：{seat_id}")
    b = ctx.body or {}
    question = str(b.get("question") or "").strip()
    if not question:
        raise ApiError(422, "VALIDATION_ERROR", "问题不能为空", [{"field": "question", "reason": "required"}])
    snap = None
    if b.get("snapshot_id"):
        snap = snapshot_service.get(ctx.db, b["snapshot_id"], ctx.replay_id)
        if snap is None:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或不可见")
    try:
        return 200, seat_agent.agent_chat(ctx.db, ctx.settings, seat_id=seat_id, question=question, snapshot=snap,
                                          symbols=b.get("symbols") or None, replay_id=ctx.replay_id,
                                          client=_client(ctx, seat_id),
                                          max_steps=b.get("max_steps") or 4,
                                          context=str(b.get("context") or "")[:2000] or None)
    except (LlmUnavailable, seat_agent.AgentRejected) as ex:
        raise _map_error(ex)


@handler("post_api_v1_commander_sessions")
def commander_session(ctx: Ctx):
    """总指挥（ADR-013）：派活给席位组合、自己查全席知识与猫爪数据，最后给汇总报告。"""
    if ctx.replay_id is not None:
        raise ApiError(409, "LLM_DISABLED_IN_REPLAY", "训练/回放上下文不调用模型：模型记忆会污染盲测")
    b = ctx.body or {}
    question = str(b.get("question") or "").strip()
    if not question:
        raise ApiError(422, "VALIDATION_ERROR", "问题不能为空", [{"field": "question", "reason": "required"}])
    snap = None
    if b.get("snapshot_id"):
        snap = snapshot_service.get(ctx.db, b["snapshot_id"], ctx.replay_id)
        if snap is None:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或不可见")
    from ...services import commander
    try:
        return 200, commander.run(ctx.db, ctx.settings, question=question, snapshot=snap,
                                  symbols=b.get("symbols") or None, replay_id=ctx.replay_id,
                                  client=_client(ctx), max_steps=b.get("max_steps") or 6,
                                  prefer_seats=b.get("prefer_seats") or None)
    except (LlmUnavailable, seat_agent.AgentRejected) as ex:
        raise _map_error(ex)


@handler("post_api_v1_llm_seat_model")
def llm_seat_model(ctx: Ctx):
    """把某一席绑到某份模型存档（ADR-012）；profile_id 为 null 表示跟随全局。"""
    b = ctx.body or {}
    seat_id = str(b.get("seat_id") or "").strip()
    if not seat_id:
        raise ApiError(422, "VALIDATION_ERROR", "缺 seat_id", [{"field": "seat_id", "reason": "required"}])
    from ...domain import seat_registry as sr
    if not any(s["seat_id"] == seat_id for s in sr.definitions(SKILLS_DIR)):
        raise ApiError(404, "SEAT_NOT_FOUND", f"没有这个席位：{seat_id}")
    pid = b.get("profile_id")
    pid = str(pid).strip() if pid else None
    try:
        llm_registry.bind_seat(ctx.settings, seat_id, pid)
    except llm_registry.ProfileError as ex:
        raise _map_profile_error(ex)
    st = llm_registry.status(ctx.settings)
    st["seat_model"] = {"seat_id": seat_id, **(st.get("seat_bindings") or {}).get(
        seat_id, {"profile_id": None, "profile_name": st.get("profile_name")})}
    return 200, st


@handler("post_api_v1_seats_seat_id_analyze")
def seat_analyze(ctx: Ctx):
    """个股分析（M3）：把某一席已跑完的规则结论交回给这一席解释，并要求明确表态（一致/分歧/数据不够）。"""
    if ctx.replay_id is not None:
        raise ApiError(409, "LLM_DISABLED_IN_REPLAY", "训练/回放上下文不调用模型")
    seat_id = ctx.path_params["seat_id"]
    b = ctx.body or {}
    srid = str(b.get("seat_run_id") or "").strip()
    symbol = str(b.get("symbol") or "").strip()
    if not srid or not symbol:
        raise ApiError(422, "VALIDATION_ERROR", "需要 seat_run_id 与 symbol",
                       [{"field": "seat_run_id" if not srid else "symbol", "reason": "required"}])
    from ...services import seat_runner
    run = seat_runner.get(ctx.db, srid, ctx.replay_id)
    if run is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "席位运行不存在或在当前上下文中不可见")
    if run["seat_id"] != seat_id:
        raise ApiError(422, "SEAT_MISMATCH", "这次运行不属于这个席位", [{"field": "seat_run_id", "reason": seat_id}])
    if run["run_state"] not in ("succeeded", "partial"):
        raise ApiError(409, "SEAT_RUN_NOT_READY", f"这次运行还没跑完（{run['run_state']}）")
    snap = snapshot_service.get(ctx.db, run["snapshot_id"], ctx.replay_id)
    if snap is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "这次运行对应的快照不可见")
    ran = [s.split(".")[0] for s in run.get("symbols") or []]
    if ran and symbol.split(".")[0] not in ran:
        raise ApiError(422, "SYMBOL_NOT_IN_RUN", "这只票不在这次运行的研究标的里",
                       [{"field": "symbol", "reason": symbol}])
    from ...domain import seat_registry as sr
    seat = next((s for s in sr.definitions(SKILLS_DIR) if s["seat_id"] == seat_id), None)
    try:
        return 200, seat_agent.analyze(ctx.db, ctx.settings, seat_id=seat_id,
                                       seat_name=(seat or {}).get("name") or seat_id, run=run, snapshot=snap,
                                       symbol=symbol, replay_id=ctx.replay_id, client=_client(ctx, seat_id))
    except (LlmUnavailable, seat_agent.AgentRejected) as ex:
        raise _map_error(ex)


@handler("post_api_v1_reports_postmarket_interpret")
def postmarket_interpret(ctx: Ctx):
    if ctx.replay_id is not None:
        raise ApiError(409, "LLM_DISABLED_IN_REPLAY", "训练/回放上下文不调用模型")
    b = ctx.body or {}
    sid = b.get("snapshot_id")
    snap = snapshot_service.get(ctx.db, sid, ctx.replay_id) if sid else None
    if snap is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或不可见")
    from ...services import postmarket_service
    report = postmarket_service.build(snap)
    try:
        out = seat_agent.interpret_report(ctx.db, ctx.settings, report=report,
                                          enable_search=bool(b.get("enable_search")), client=_client(ctx))
    except (LlmUnavailable, seat_agent.AgentRejected) as ex:
        raise _map_error(ex)
    return 200, {**out, "report": report}


@handler("post_api_v1_roundtable_sessions")
def roundtable_session(ctx: Ctx):
    if ctx.replay_id is not None:
        raise ApiError(409, "LLM_DISABLED_IN_REPLAY", "训练/回放上下文不调用模型")
    b = ctx.body or {}
    question = str(b.get("question") or "").strip()
    seats = b.get("seats") or []
    if not question:
        raise ApiError(422, "VALIDATION_ERROR", "问题不能为空", [{"field": "question", "reason": "required"}])
    if not isinstance(seats, list) or not (1 <= len(seats) <= 4):
        raise ApiError(422, "VALIDATION_ERROR", "一次圆桌选 1–4 席", [{"field": "seats", "reason": "1..4"}])
    from ...domain import seat_registry as sr
    known = {s["seat_id"] for s in sr.definitions(SKILLS_DIR)}
    for s in seats:
        if not isinstance(s, dict) or s.get("seat_id") not in known:
            invalid_id = s.get("seat_id") if isinstance(s, dict) else "格式无效"
            raise ApiError(422, "VALIDATION_ERROR", f"未知席位：{invalid_id}",
                           [{"field": "seats", "reason": "unknown seat"}])
    snap = None
    if b.get("snapshot_id"):
        snap = snapshot_service.get(ctx.db, b["snapshot_id"], ctx.replay_id)
        if snap is None:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或不可见")
    try:
        return 200, seat_agent.roundtable(ctx.db, ctx.settings, snapshot=snap, question=question, seats=seats,
                                          use_search=bool(b.get("use_search")), client=_client(ctx))
    except (LlmUnavailable, seat_agent.AgentRejected) as ex:
        raise _map_error(ex)
