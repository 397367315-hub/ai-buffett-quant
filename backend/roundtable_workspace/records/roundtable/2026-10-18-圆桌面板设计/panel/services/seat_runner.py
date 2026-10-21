"""席位运行器（SEAT-005）：同一不可变快照 → 物化席位输入 → 白名单脚本 → 契约 SeatRun。

- 只运行注册表里登记的技能脚本（固定路径 + shell=False + 固定参数），原书、供应商字段与模型输出一律当数据；
- 所有席位读同一 snapshot_id：输入从快照引用的原始字节（证据仓）物化，不再临时取数；
- 输出原样存证（内容寻址），SeatRun 里的结论全部能回溯到这份原始输出与快照证据；
- 三维状态正交：run_state / applicability / verdict；inapplicable → verdict unknown、无候选、count=null；
  failed → verdict unknown；只有覆盖所需输入且适用时才可判 conditions_met / conditions_failed；
- 候选按快照的候选池政策（沪深主板非 ST）过滤，被政策排除的数量写进 explanation；
- B/C 级条件一律 unknown（缺 minute/level2），不拿日线代理冒充；没有事前计划时纪律审查为 unknown。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from decimal import Decimal
from pathlib import Path

from ..config import SKILLS_DIR, get_settings
from ..data.evidence_store import EvidenceStore
from ..domain import clock, seat_registry as sr, trading_rules as tr
from ..domain.canonical import canonical_json, content_hash, derived_id, new_id, sha256_hex, strict_json_loads
from ..domain.enums import check_triplet
from ..jobs import queue, worker
from ..jobs.worker import JobFailed, JobResult
from . import snapshot_service
from .feature_rows import write_feature_rows
from .workload import memory_work

PROMPT_DISABLED_SHA = sha256_hex("roundtable-llm-disabled-template-v1")
TOP_ALL = "5000"
_RUNNER_VERSION = "seat_runner_v1"
_proc_sem = threading.BoundedSemaphore(int(os.environ.get("ROUNDTABLE_SEAT_PROCS", "1")))
_mat_lock = threading.Lock()

SLUG = {sid: sid.split("-")[1] for sid in sr.seat_ids()}
SCRIPT_OUT = {sid: ("screen.json" if sid == "trading-wildman-v2" else f"{SLUG[sid]}.json") for sid in sr.seat_ids()}
HORIZON_OK = {"short": {"short"}, "swing": {"swing"}, "long": {"long"}, "multi": {"short", "swing", "long"}}


def code_revision() -> str:
    me = Path(__file__).read_bytes()
    return f"{_RUNNER_VERSION}:{sha256_hex(me)[:12]}"


# ── 输入物化 ──
def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rows_from_blob(raw: bytes, fmt: str) -> list[dict]:
    doc = strict_json_loads(raw)
    if fmt == "numcat_matrix_v1":
        d = (doc["results"][0].get("data") if doc.get("results") else doc.get("data")) or {}
        f = d.get("fields") or []
        return [dict(zip(f, it)) if isinstance(it, (list, tuple)) else dict(it) for it in d.get("items") or d.get("rows") or []]
    if fmt == "explicit_rows_v1":
        return doc["rows"] if isinstance(doc, dict) else doc
    return doc if isinstance(doc, list) else (doc.get("rows") or [])


def _daily_row(r: dict, day: str) -> dict | None:
    sym = str(r.get("symbol") or r.get("code") or "").split(".")[0].zfill(6)
    if not sym.strip("0") and not sym:
        return None
    out = {"tradedate": day.replace("-", ""), "symbol": sym, "name": r.get("name") or ""}
    for k in ("open", "high", "low", "close", "pre_close", "pct_chg", "amount", "vol"):
        out[k] = _num(r.get(k))
    if out["close"] is None:
        return None
    return out


def _evidence_by_path(snap: dict) -> dict[str, dict]:
    return {e["field_path"]: e for e in snap["evidence"] if e.get("field_path")}


def materialize(db, snap: dict, store: EvidenceStore) -> dict:
    """把快照引用的原始数据物化成席位脚本的输入文件；同一快照只做一次。"""
    s = get_settings()
    root = s.work_dir / "seat_inputs" / snap["snapshot_id"]
    manifest = root / "manifest.json"
    with _mat_lock:
        if manifest.exists():
            return json.loads(manifest.read_text(encoding="utf-8"))
        tmp = Path(tempfile.mkdtemp(dir=str(s.work_dir), prefix="mat-"))
        ev = _evidence_by_path(snap)
        D = snap["metadata"]["session_date"]
        allowed = {i["symbol"].split(".")[0] for i in snap["instruments"]}
        missing: list[str] = []
        inputs: dict = {"snapshot_id": snap["snapshot_id"], "fingerprint": snap["fingerprint"], "files": {},
                        "missing_inputs": missing, "evidence": {}}

        def blob(e):
            loc = e["source_ref"]["locator"] or ""
            return store.get(loc.split(":", 1)[1]) if loc.startswith("evidence:") else None

        # 日线：历史 + 当日
        hist_keys = sorted(k for k in ev if k.startswith("seat_inputs.daily_history["))
        used_ev = []
        sess = ev.get("daily_bars[*]")
        if sess is None:
            raise JobFailed("SNAPSHOT_WITHOUT_DAILY", "快照没有当日日线，席位无法运行")
        def feature_rows():
            sources = [(ev[k], k[len("seat_inputs.daily_history["):-1]) for k in hist_keys] + [(sess, D)]
            for e, day in sources:
                fmt = (e.get("value") or {}).get("raw_format", "numcat_matrix_v1")
                for r in _rows_from_blob(blob(e), fmt):
                    x = _daily_row(r, day)
                    if x and x["symbol"] in allowed:
                        yield x
                used_ev.append(e["evidence_id"])
        n_days = len(hist_keys) + 1
        inputs["history_sessions"] = n_days
        if n_days < 70:
            missing.append(f"daily_history_70(have {n_days})")
        write_feature_rows(tmp / "features.json", feature_rows())
        inputs["files"]["features"] = "features.json"
        inputs["evidence"]["daily"] = used_ev
        # 行业（stockbasic 派生）
        ind = ev.get("seat_inputs.industry_map")
        if ind is not None:
            m = json.loads(blob(ind))
            (tmp / "industry.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
            inputs["files"]["industry"] = "industry.json"
            inputs["evidence"]["industry"] = [ind["evidence_id"]]
        else:
            missing.append("industry_map")
        # 涨停池
        lp = ev.get("seat_inputs.limit_pool")
        if lp is not None:
            pool = []
            for r in json.loads(blob(lp)):
                r = dict(r)
                for k in ("close", "amount", "pct_chg", "fd_amount"):
                    r[k] = _num(r.get(k))
                pool.append(r)
            (tmp / "limit_pool.json").write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")
            inputs["files"]["limit_pool"] = "limit_pool.json"
            inputs["evidence"]["limit_pool"] = [lp["evidence_id"]]
        else:
            missing.append("limit_pool_session")
        # 异动事件 → 封板质量聚合（与旧 refresh.fetch_limit_events 同口径）
        le = ev.get("seat_inputs.limit_events")
        if le is not None:
            agg: dict = {}
            for r in _rows_from_blob(blob(le), (le.get("value") or {}).get("raw_format", "numcat_matrix_v1")):
                code = str(r.get("symbol") or "").split(".")[0].zfill(6)
                cur = agg.setdefault(code, {"first_seal": None, "open_times": None, "fd_amount": None,
                                            "limit_times": None, "turnover": None})
                t = r.get("servertime")
                if str(r.get("event")) == "2" and t and (cur["first_seal"] is None or str(t) < cur["first_seal"]):
                    cur["first_seal"] = str(t)
                if r.get("open_times") is not None:
                    cur["open_times"] = int(r["open_times"])
                if r.get("fd_amount") is not None:
                    cur["fd_amount"] = _num(r["fd_amount"])
                if r.get("limit_times") is not None:
                    cur["limit_times"] = int(r["limit_times"])
                if r.get("turnover_rate") is not None:
                    cur["turnover"] = _num(r["turnover_rate"])
            (tmp / "limit_events.json").write_text(json.dumps(agg, ensure_ascii=False), encoding="utf-8")
            inputs["files"]["limit_events"] = "limit_events.json"
            inputs["evidence"]["limit_events"] = [le["evidence_id"]]
        else:
            missing.append("limit_events_session")
        # 估值
        va = ev.get("seat_inputs.valuation")
        if va is not None:
            vrows = []
            for r in _rows_from_blob(blob(va), (va.get("value") or {}).get("raw_format", "numcat_matrix_v1")):
                vrows.append({k: (_num(v) if k not in ("symbol", "name", "tradedate", "code") else v) for k, v in r.items()})
            (tmp / "valuation.json").write_text(json.dumps(vrows, ensure_ascii=False), encoding="utf-8")
            inputs["files"]["valuation"] = "valuation.json"
            inputs["evidence"]["valuation"] = [va["evidence_id"]]
        else:
            missing.append("valuation_session")
        # 连续分红年数（当前表，只对最新会话有效；回放中不可用）
        dv = ev.get("seat_inputs.dividend_years_current")
        if dv is not None and snap["metadata"]["source_mode"] != "fixture":
            (tmp / "dividend_years.json").write_bytes(blob(dv))
            inputs["files"]["dividend_years"] = "dividend_years.json"
            inputs["evidence"]["dividend_years"] = [dv["evidence_id"]]
        else:
            missing.append("dividend_history")
        missing.append("hourly_bars")
        missing.append("minute_bars")
        missing.append("level2")
        (tmp / "manifest.json").write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
        root.parent.mkdir(parents=True, exist_ok=True)
        if root.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            os.replace(tmp, root)
        return json.loads(manifest.read_text(encoding="utf-8"))


def _cmd(seat_id: str, root: Path, mf: dict, symbols6: list[str], out: Path) -> list[list[str]] | None:
    f = mf["files"]
    script = SKILLS_DIR / seat_id / "scripts" / "screen.py"
    # Isolate subprocesses while making the trusted compact input reader
    # available. Provider credentials are still excluded from their env.
    panel_parent = str(Path(__file__).resolve().parents[2])
    launcher = (f"import runpy,sys;sys.path.insert(0,{panel_parent!r});"
                "p=sys.argv.pop(1);sys.argv[0]=p;runpy.run_path(p,run_name='__main__')")
    py = [sys.executable, "-I", "-c", launcher]
    F = str(root / f["features"])
    ind = ["--industry", str(root / f["industry"])] if "industry" in f else []
    if seat_id == "trading-wildman-v2":
        if "limit_pool" not in f:
            return None
        compact = out / "features_compact.json"
        return [py + [str(SKILLS_DIR / seat_id / "scripts/build_features.py"), "--daily", F, "--out", str(compact)] + ind,
                py + [str(script), "--features", str(compact), "--limit-pool", str(root / f["limit_pool"]),
                      "--top", TOP_ALL, "--out", str(out)]]
    if seat_id in ("trading-jianmen-v1", "trading-yangnan-v1"):
        lp = ["--limit-pool", str(root / f["limit_pool"])] if "limit_pool" in f else []
        return [py + [str(script), "--features", F, "--top", TOP_ALL, "--out", str(out)] + lp + ind]
    if seat_id == "trading-yangjia-v1":
        return [py + [str(script), "--features", F, "--out", str(out)]]
    if seat_id in ("trading-gushiguifei-v1", "trading-livermore-v1"):
        return [py + [str(script), "--features", F, "--top", TOP_ALL, "--out", str(out)] + ind]
    if seat_id in ("trading-wyckoff-v1", "trading-candle-v1"):
        return [py + [str(script), "--features", F, "--top", TOP_ALL, "--out", str(out)]]
    if seat_id == "trading-chenkai-v1":
        sym = ["--symbols", ",".join(symbols6)] if symbols6 else []
        return [py + [str(script), "--features", F, "--top", TOP_ALL, "--out", str(out)] + ind + sym]
    if seat_id == "trading-darwin-v1":
        if "valuation" not in f:
            return None
        dv = ["--dividend-years", str(root / f["dividend_years"])] if "dividend_years" in f else []
        return [py + [str(script), "--valuation", str(root / f["valuation"]), "--top", TOP_ALL, "--out", str(out)] + dv + ind]
    if seat_id == "trading-boardtech-v1":
        if "limit_pool" not in f:
            return None
        le = ["--limit-events", str(root / f["limit_events"])] if "limit_events" in f else []
        return [py + [str(script), "--features", F, "--limit-pool", str(root / f["limit_pool"]), "--top", TOP_ALL,
                      "--out", str(out)] + le + ind]
    if seat_id == "trading-lidaxiao-v1":
        if "valuation" not in f:
            return None
        return [py + [str(script), "--features", F, "--valuation", str(root / f["valuation"]), "--top", TOP_ALL,
                      "--out", str(out)] + ind]
    return None


def run_script(seat_id: str, snap: dict, mf: dict, symbols6: list[str], store: EvidenceStore,
               timeout: float) -> tuple[dict | None, str | None, str]:
    """返回 (原始输出 dict, 原始字节 sha256, 错误说明)。按 (快照指纹, 技能哈希, 参数) 缓存。"""
    s = get_settings()
    root = s.work_dir / "seat_inputs" / snap["snapshot_id"]
    skill = sr.skill_dir_sha256(SKILLS_DIR, seat_id)
    key = content_hash({"fp": snap["fingerprint"], "seat": seat_id, "skill": skill, "symbols": sorted(symbols6),
                        "runner": _RUNNER_VERSION})
    cache = s.work_dir / "seat_cache" / f"{key}.json"
    if cache.exists():
        meta = json.loads(cache.read_text(encoding="utf-8"))
        raw = store.get(meta["raw_sha256"])
        return json.loads(raw), meta["raw_sha256"], ""
    out = Path(tempfile.mkdtemp(dir=str(s.work_dir), prefix=f"run-{SLUG[seat_id]}-"))
    cmds = _cmd(seat_id, root, mf, symbols6, out)
    if cmds is None:
        return None, None, "required_input_missing"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "PYTHONHASHSEED": "0"}
    try:
        with _proc_sem, memory_work:
            for c in cmds:
                p = subprocess.run(c, capture_output=True, timeout=timeout, shell=False, env=env, cwd=str(out))
                if p.returncode != 0:
                    tail = (p.stderr or p.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
                    return None, None, f"script_exit_{p.returncode}: {(tail or ['?'])[0][:200]}"
        f = out / SCRIPT_OUT[seat_id]
        if not f.exists():
            return None, None, "script_no_output"
        raw = f.read_bytes()
        obj = json.loads(raw)
        sha = store.put(raw)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"raw_sha256": sha, "seat_id": seat_id, "snapshot_id": snap["snapshot_id"]}),
                         encoding="utf-8")
        return obj, sha, ""
    except subprocess.TimeoutExpired:
        return None, None, "script_timeout"
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ── 映射到 SeatRun ──
def _candidate_universe(snap: dict) -> set[str]:
    return {i["symbol"] for i in snap["instruments"]
            if i["board"] in ("SH_MAIN", "SZ_MAIN") and i["is_st"] is False and "退" not in (i["name"] or "")}


def _cond(cid: str, desc: str, result: str, evidence: list[str], missing: list[str] | None = None) -> dict:
    return {"condition_id": cid, "description": desc or cid, "result": result, "evidence_ids": evidence,
            "missing_inputs": missing or []}


def _cid(seat_id: str, *parts: str) -> str:
    base = "_".join([SLUG[seat_id], *[p.replace(".", "_").lower() for p in parts]])
    return base[:95]


def bc_conditions(seat_id: str) -> list[dict]:
    """B 级（分钟线）与 C 级（Level2/逐笔）条件：本环境一律 unknown，并写明缺什么——不论脚本是否运行成功。"""
    cap = sr.CAPABILITIES[seat_id]
    out = []
    for i, item in enumerate(cap.get("b_level") or []):
        out.append(_cond(_cid(seat_id, "b", str(i + 1)), f"B 级（需分钟线）：{item}", "unknown", [], ["minute_bars"]))
    for i, item in enumerate(cap.get("c_level") or []):
        out.append(_cond(_cid(seat_id, "c", str(i + 1)), f"C 级（需 Level2/逐笔）：{item}", "unknown", [], ["level2"]))
    return out


def map_output(seat_id: str, raw: dict, snap: dict, symbols: list[str], horizon: str, ev_ids: list[str],
               missing: list[str]) -> dict:
    """原始输出 → SeatRun 的结论字段（不含身份/时间字段）。"""
    cap = sr.CAPABILITIES[seat_id]
    seat_def = next(s for s in sr.definitions(SKILLS_DIR) if s["seat_id"] == seat_id)
    caps, seat_horizons = seat_def["capabilities"], seat_def["horizons"]
    selector = "screen" in caps
    gate = raw.get("gate") or {}
    gate_open = gate.get("open")
    universe = _candidate_universe(snap)
    conds: list[dict] = []
    counter = [str(x) for x in (gate.get("counter_evidence") or [])][:12]
    counter += [f"待核：{u}" for u in (raw.get("unknowns") or [])][:12]
    counter += [f"注意：{w}" for w in (raw.get("warnings") or [])][:6]
    explanation_parts = []
    bc_missing = []
    if cap.get("b_level"):
        bc_missing.append("minute_bars")
    if cap.get("c_level"):
        bc_missing.append("level2")
    conds += bc_conditions(seat_id)

    # 野人哥：周期阶段与角色（context）
    if seat_id == "trading-wildman-v2":
        st = raw.get("stage") or {}
        label = str(st.get("stage") or "未判定")
        undetermined = label in ("未判定", "unknown", "") or bool(st.get("undetermined"))
        allow = (not undetermined) and any(k in label for k in ("启动", "发酵", "主升", "修复"))
        conds.insert(0, _cond(_cid(seat_id, "cycle"), f"情绪周期阶段：{label}（{'；'.join(st.get('evidence') or [])[:120]}）",
                              "unknown" if undetermined else ("met" if allow else "failed"), ev_ids,
                              [str(u) for u in (raw.get("unknowns") or [])][:6] if undetermined else None))
        if undetermined:
            explanation_parts.append(f"周期阶段未能判定（{'、'.join(str(u) for u in (raw.get('unknowns') or [])) or '关键输入缺失'}）"
                                     "：不给方向结论，不是看空")
            return {"applicability": "unknown", "applicability_reason": "周期阶段无法由现有输入判定",
                    "verdict": "unknown", "candidate_count": None, "candidates": [], "condition_results": conds,
                    "explanation": "；".join(explanation_parts), "counter_evidence": counter}
        roles = {r["symbol"]: r for r in raw.get("roles") or []}
        sym_results = []
        for s in symbols:
            r = roles.get(s.split(".")[0])
            if r:
                conds.append(_cond(_cid(seat_id, "role", s.split(".")[0]), f"{s} 角色：{'、'.join(r.get('roles') or [])}",
                                   "met", ev_ids))
                sym_results.append("met")
            else:
                conds.append(_cond(_cid(seat_id, "role", s.split(".")[0]), f"{s} 不在当日主线角色表中", "failed", ev_ids))
                sym_results.append("failed")
        explanation_parts.append(f"周期阶段「{label}」；角色标注 {len(roles)} 只（不直接出买入候选）")
        verdict = "conditions_failed" if not allow else ("watch" if not symbols or "met" in sym_results else "conditions_failed")
        return {"applicability": "applicable", "applicability_reason": "周期与角色可由日线与涨停池判定（A 级）；分时与盘口待核",
                "verdict": verdict, "candidate_count": None, "candidates": [], "condition_results": conds,
                "explanation": "；".join(explanation_parts), "counter_evidence": counter}

    # 蜡烛图：确认器
    if seat_id == "trading-candle-v1":
        pats: dict[str, list] = {}
        for p in raw.get("patterns") or []:
            pats.setdefault(str(p.get("symbol")).zfill(6), []).append(p)
        res = []
        for s in symbols:
            ps = pats.get(s.split(".")[0], [])
            if ps:
                desc = "、".join(f"{p['pattern']}（确认：{p.get('confirms')}）" for p in ps[:3])
                conds.append(_cond(_cid(seat_id, "pattern", s.split(".")[0]), f"{s} 当日形态：{desc}", "met", ev_ids))
                res.append("met")
            else:
                conds.append(_cond(_cid(seat_id, "pattern", s.split(".")[0]), f"{s} 当日无已定义的确认形态", "unknown", ev_ids))
                res.append("unknown")
        explanation_parts.append(f"形态统计 {sum(len(v) for v in pats.values())} 处；本席只作确认，不出独立候选")
        verdict = "watch" if "met" in res else "unknown"
        return {"applicability": "applicable" if symbols else "unknown",
                "applicability_reason": "确认器：只对已有研究标的给形态确认" if symbols else "没有指定研究标的，确认器无对象",
                "verdict": verdict if symbols else "unknown", "candidate_count": None, "candidates": [],
                "condition_results": conds, "explanation": "；".join(explanation_parts), "counter_evidence": counter}

    # 陈凯：小周期（小时线）不可判 → unknown，只给日线解释
    if seat_id == "trading-chenkai-v1":
        ctx = {c["symbol"]: c for c in raw.get("daily_context") or []}
        for s in symbols:
            c = ctx.get(s.split(".")[0])
            if c:
                conds.append(_cond(_cid(seat_id, "weekly", s.split(".")[0]), f"{s} 周线方向向上", "met" if c.get("weekly_trend_up") else "failed", ev_ids))
                conds.append(_cond(_cid(seat_id, "pullback", s.split(".")[0]), f"{s} 日线回踩 20/30 日均线 2% 内（工程容忍度）",
                                   "met" if c.get("near_ma20_30") else "failed", ev_ids))
                conds.append(_cond(_cid(seat_id, "divergence", s.split(".")[0]), f"{s} 中周期无 DIF 顶背离（{c.get('divergence_note')}）",
                                   "failed" if c.get("dif_top_divergence") == "formed" else "met", ev_ids))
            conds.append(_cond(_cid(seat_id, "hourly", s.split(".")[0]), f"{s} 小周期（小时线）方向", "unknown", [], ["hourly_bars"]))
        explanation_parts.append("三周期共振需要小时线（B 级），本环境没有 → 共振结论 unknown；只给日线级解释（规则版本 chenkai-v2-20261019）")
        return {"applicability": "unknown", "applicability_reason": "核心判据需要小时线（hourly_bars 缺失）",
                "verdict": "unknown", "candidate_count": None, "candidates": [], "condition_results": conds,
                "explanation": "；".join(explanation_parts), "counter_evidence": counter}

    # 选股/环境类席位
    raw_cands = raw.get("candidates") or []
    kept, dropped = [], 0
    for c in raw_cands:
        sym = tr.to_symbol(str(c.get("symbol") or ""))
        if sym is None:
            continue
        if sym not in universe:
            dropped += 1
            continue
        kept.append((sym, c))
    gate_result = "met" if gate_open is True else ("failed" if gate_open is False else "unknown")
    conds.insert(0, _cond(_cid(seat_id, "gate"), f"本席闸门「{gate.get('state') or '未判定'}」：{gate.get('reason') or ''}"[:500],
                          gate_result, ev_ids))
    has_bc = bool(bc_missing)
    cand_verdict = "watch" if has_bc else "conditions_met"
    candidates = []
    if selector and gate_open:
        for sym, c in kept:
            reason = str(c.get("buy_point") or c.get("trigger") or c.get("role") or c.get("phase") or "满足本席 A 级条件")
            cid = _cid(seat_id, "sym", sym.split(".")[0])
            candidates.append({"symbol": sym, "seat_id": seat_id,
                               "horizon": horizon if horizon in ("short", "swing", "long") else seat_horizons[0],
                               "verdict": cand_verdict, "condition_ids": [conds[0]["condition_id"], cid],
                               "evidence_ids": ev_ids, "reason": reason[:300]})
    kept_set = {s for s, _ in kept}
    rejected = {tr.to_symbol(str(r.get("symbol") or "")): str(r.get("why") or "") for r in raw.get("rejected_sample") or []}
    sym_results = []
    for s in symbols:
        cid = _cid(seat_id, "sym", s.split(".")[0])
        if s not in universe:
            conds.append(_cond(cid, f"{s} 不在候选池政策（沪深主板非 ST）内", "inapplicable", ev_ids))
            sym_results.append("inapplicable")
        elif not selector:
            conds.append(_cond(cid, f"{s}：本席是环境判断，不对个股出结论", "inapplicable", ev_ids))
            sym_results.append("inapplicable")
        elif s in kept_set:
            conds.append(_cond(cid, f"{s} 满足本席 A 级条件" + ("；B/C 级条件待核" if has_bc else ""), "met", ev_ids))
            sym_results.append("watch" if has_bc else "met")
        elif s in rejected:
            conds.append(_cond(cid, f"{s} 未满足：{rejected[s]}", "failed", ev_ids))
            sym_results.append("failed")
        else:
            conds.append(_cond(cid, f"{s} 未出现在本席完整候选清单中（本席 A 级条件未满足）", "failed", ev_ids))
            sym_results.append("failed")
    applicable = not any(m for m in missing if m.split("(")[0] in sr.hard_required(seat_id))
    if gate_open is None:
        verdict = "unknown"
    elif not selector:
        verdict = "watch" if gate_open else "conditions_failed"
    elif gate_open is False:
        verdict = "conditions_failed"
    elif symbols:
        rs = [r for r in sym_results if r != "inapplicable"]
        if not rs:
            verdict = "unknown"
        elif all(r == "met" for r in rs):
            verdict = "conditions_met"
        elif all(r == "failed" for r in rs):
            verdict = "conditions_failed"
        else:
            verdict = "watch"
    else:
        verdict = (cand_verdict if candidates else "conditions_failed")
    if not applicable:
        verdict = "unknown"
    explanation_parts.append(f"闸门「{gate.get('state') or '未判定'}」{'开' if gate_open else '关' if gate_open is False else '未判'}")
    if selector:
        explanation_parts.append(f"脚本候选 {len(raw_cands)} 只，按候选池政策排除 {dropped} 只，保留 {len(kept)} 只"
                                 + ("" if gate_open else "（闸门关闭，按纪律不出候选）"))
    return {"applicability": "applicable" if applicable else "unknown",
            "applicability_reason": "所需输入齐全（A 级）" if applicable else f"缺少必需输入：{', '.join(missing)}",
            "verdict": verdict,
            "candidate_count": (len(candidates) if (selector and applicable) else None),
            "candidates": candidates if applicable else [],
            "condition_results": conds, "explanation": "；".join(explanation_parts), "counter_evidence": counter}


# ── 持久化 ──
def seat_run_payload(row) -> dict:
    return json.loads(row["canonical_payload"])


def create_seat_run(c, *, seat_id: str, snapshot: dict, case_id: str | None, round_: str, cycle_id: str,
                    registry: dict, job_id: str, replay_id: str | None, symbols: list[str], horizon: str,
                    llm_mode: str, attempt: int = 1, seat_run_id: str | None = None) -> dict:
    sid = seat_run_id or new_id("seatrun")
    seat = next(s for s in registry["seats"] if s["seat_id"] == seat_id)
    fps = {"cycle_id": cycle_id, "snapshot_fingerprint": snapshot["fingerprint"],
           "registry_fingerprint": registry["fingerprint"], "skill_sha256": seat["skill_sha256"],
           "rule_version": seat["rule_version"], "prompt_sha256": PROMPT_DISABLED_SHA,
           "provider_id": None, "model_id": None, "model_config_sha256": None,
           "code_revision": code_revision(), "user_context_sha256": None, "replay_id": replay_id}
    obj = {"seat_run_id": sid, "job_id": job_id, "case_id": case_id, "seat_id": seat_id,
           "snapshot_id": snapshot["snapshot_id"], "round": round_, "run_state": "queued",
           "applicability": "unknown", "applicability_reason": "排队中", "verdict": "unknown",
           "candidate_count": None, "candidates": [], "condition_results": [], "evidence_ids": [],
           "missing_inputs": [], "explanation": "", "counter_evidence": [], "challenged_seat_run_ids": [],
           "fingerprints": fps, "created_at": clock.utc_iso(), "started_at": None, "finished_at": None, "error": None}
    c.execute("""INSERT INTO seat_runs(seat_run_id, job_id, case_id, seat_id, snapshot_id, registry_version, round, attempt,
                 run_state, applicability, verdict, candidate_count, replay_id, created_at, finished_at, fingerprints_json,
                 error_json, canonical_payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (sid, job_id, case_id, seat_id, snapshot["snapshot_id"], registry["registry_version"], round_, attempt,
               "queued", "unknown", "unknown", None, replay_id, obj["created_at"], None, canonical_json(fps), None,
               canonical_json({**obj, "_ctx": {"symbols": symbols, "horizon": horizon, "llm_mode": llm_mode}})))
    return obj


def _update(c, obj: dict, ctx: dict) -> None:
    err = check_triplet(obj["run_state"], obj["applicability"], obj["verdict"], obj["candidate_count"])
    if err:
        raise AssertionError(f"SeatRun 三维状态不一致：{err}")
    c.execute("""UPDATE seat_runs SET run_state=?, applicability=?, verdict=?, candidate_count=?, finished_at=?,
                 error_json=?, canonical_payload=? WHERE seat_run_id=?""",
              (obj["run_state"], obj["applicability"], obj["verdict"], obj["candidate_count"], obj["finished_at"],
               canonical_json(obj["error"]) if obj["error"] else None, canonical_json({**obj, "_ctx": ctx}),
               obj["seat_run_id"]))


def public(obj: dict) -> dict:
    """对外形状：去掉内部字段，但把 _ctx 里的研究标的与周期带出来（界面要用，M3）。"""
    out = {k: v for k, v in obj.items() if not k.startswith("_")}
    ctx = obj.get("_ctx") or {}
    out["symbols"] = list(ctx.get("symbols") or [])
    out["horizon"] = ctx.get("horizon")
    return out


def get(db, seat_run_id: str, replay_id) -> dict | None:
    r = db.one("SELECT canonical_payload, replay_id FROM seat_runs WHERE seat_run_id=?", (seat_run_id,))
    if r is None or r["replay_id"] != replay_id:
        return None
    return public(json.loads(r["canonical_payload"]))


def execute(db, seat_run_id: str, job_ctx: worker.JobContext | None = None) -> dict:
    row = db.one("SELECT * FROM seat_runs WHERE seat_run_id=?", (seat_run_id,))
    full = json.loads(row["canonical_payload"])
    ctx = full.pop("_ctx", {})
    obj = full
    seat_id = obj["seat_id"]
    symbols, horizon = ctx.get("symbols") or [], ctx.get("horizon") or "short"
    snap = snapshot_service.get(db, obj["snapshot_id"], row["replay_id"])
    store = EvidenceStore(get_settings().evidence_dir)
    obj["started_at"] = clock.utc_iso()
    seat = next(s for s in sr.definitions(SKILLS_DIR) if s["seat_id"] == seat_id)
    # 适用性：周期不适用 → skipped / inapplicable，不运行脚本
    if horizon != "multi" and horizon not in seat["horizons"]:
        obj.update({"run_state": "skipped", "applicability": "inapplicable",
                    "applicability_reason": f"本席适用周期 {'/'.join(seat['horizons'])}，不适用于 {horizon} 任务",
                    "verdict": "unknown", "candidate_count": None, "candidates": [],
                    "explanation": "不适用于此任务（不代表看空）", "finished_at": clock.utc_iso()})
        with db.txn() as c:
            _update(c, obj, ctx)
        return obj
    if snap is None:
        obj.update({"run_state": "failed", "verdict": "unknown", "candidate_count": None, "finished_at": clock.utc_iso(),
                    "error": _err("SNAPSHOT_NOT_VISIBLE", "快照不存在或不可见", obj["seat_run_id"])})
        with db.txn() as c:
            _update(c, obj, ctx)
        return obj
    # 退学：纪律审查席；没有事前计划就是 unknown（不判违规）
    if seat_id == "trading-tuixue-v1":
        obj.update({"run_state": "succeeded", "applicability": "applicable",
                    "applicability_reason": "纪律审查只审查用户事前写下的计划与动作",
                    "verdict": "unknown", "candidate_count": None, "candidates": [],
                    "condition_results": [_cond("tuixue_plan_present", "已有事前冻结计划（四要件：触发、失效、防守、时间退出）",
                                                "unknown", [], ["decision_plan"])],
                    "missing_inputs": ["decision_plan", "journal_events"],
                    "explanation": "研究阶段尚无事前计划：只能列出待补的四要件，不能判断执行是否合规；计划冻结后由事前审查（preflight）检查",
                    "counter_evidence": ["模式定义需使用者事前书面给出，本席无法代拟"], "finished_at": clock.utc_iso()})
        with db.txn() as c:
            _update(c, obj, ctx)
        return obj
    obj["run_state"] = "running"
    with db.txn() as c:
        _update(c, {**obj, "verdict": "unknown", "candidate_count": None}, ctx)
    mf = materialize(db, snap, store)
    missing = [m for m in mf["missing_inputs"] if m.split("(")[0] in sr.hard_required(seat_id)]
    timeout = job_ctx.remaining() if job_ctx else 300.0
    if missing:
        # 硬性必需输入缺失：不运行脚本（不让脚本在残缺输入上「算出」结论），直接给 unknown 与缺失清单
        raw, raw_sha, err = None, None, "required_input_missing"
    else:
        raw, raw_sha, err = run_script(seat_id, snap, mf, [s.split(".")[0] for s in symbols], store, max(30.0, timeout))
    if raw is None:
        bc = bc_conditions(seat_id)
        bc_missing = sorted({m for c_ in bc for m in c_["missing_inputs"]})
        if err == "required_input_missing":
            obj.update({"run_state": "succeeded", "applicability": "unknown",
                        "applicability_reason": f"缺少必需输入：{', '.join(missing) or err}", "verdict": "unknown",
                        "candidate_count": None, "candidates": [], "condition_results": bc,
                        "missing_inputs": sorted(set((missing or [err]) + bc_missing)),
                        "explanation": "必需输入缺失，未运行规则（结论未知，不是看空）", "finished_at": clock.utc_iso()})
        else:
            obj.update({"run_state": "failed", "verdict": "unknown", "candidate_count": None, "finished_at": clock.utc_iso(),
                        "condition_results": bc, "missing_inputs": sorted(set(missing + bc_missing)),
                        "error": _err("SEAT_SCRIPT_FAILED", err, obj["seat_run_id"], retryable=err == "script_timeout")})
        with db.txn() as c:
            _update(c, obj, ctx)
        return obj
    return finalize_from_raw(db, obj, ctx, snap, raw, raw_sha, mf, missing, row["replay_id"])


def finalize_from_raw(db, obj: dict, ctx: dict, snap: dict, raw: dict, raw_sha: str, mf: dict, missing: list[str],
                      replay_id) -> dict:
    """原始输出（已存证）→ SeatRun 终态。execute 与「席位适配器」验收共用这一段映射。"""
    seat_id = obj["seat_id"]
    symbols, horizon = ctx.get("symbols") or [], ctx.get("horizon") or "short"
    out_ev = _store_output_evidence(db, snap, seat_id, raw_sha, replay_id)
    base_ev = [out_ev] + (mf.get("evidence") or {}).get("daily", [])[-1:]
    m = map_output(seat_id, raw, snap, symbols, horizon, base_ev, missing)
    obj.update(m)
    obj["evidence_ids"] = sorted(set(base_ev + [e for k, v in (mf.get("evidence") or {}).items() if k != "daily" for e in v]))
    obj["missing_inputs"] = sorted(set(missing + [x for c_ in m["condition_results"] for x in c_["missing_inputs"]
                                                   if x in ("minute_bars", "level2", "hourly_bars")]))
    obj["run_state"] = "partial" if (obj["missing_inputs"] and obj["applicability"] == "unknown") else "succeeded"
    obj["finished_at"] = clock.utc_iso()
    with db.txn() as c:
        _update(c, obj, ctx)
    return obj


def _err(code, message, rid, retryable=False):
    return {"error": {"code": code, "message": message, "request_id": rid if rid[0].isalpha() else "seatrun_x",
                      "retryable": retryable, "details": []}}


def _store_output_evidence(db, snap: dict, seat_id: str, raw_sha: str, replay_id) -> str:
    eid = derived_id("ev", snap["snapshot_id"], seat_id, raw_sha)
    with db.txn() as c:
        if c.execute("SELECT 1 FROM evidence WHERE evidence_id=?", (eid,)).fetchone():
            return eid
        now = clock.utc_iso()
        e = {"evidence_id": eid, "kind": "rule_derivation", "snapshot_id": snap["snapshot_id"], "symbol": None,
             "observed_at": None, "available_at": None, "first_observed_at": now, "time_precision": "unknown",
             "availability_basis": "unknown", "captured_at": now,
             "source_ref": {"source_id": f"seat_{SLUG[seat_id]}", "authority_type": "user", "locator": f"evidence:{raw_sha}",
                            "content_sha256": raw_sha, "retrieved_at": now, "published_at": None,
                            "citation": f"skills/{seat_id} 原始输出（规则推导，不是市场事实）"},
             "field_path": f"seat_outputs.{seat_id}", "value": {"raw_sha256": raw_sha, "seat_id": seat_id},
             "unit": "json", "quality": "verified", "related_evidence_ids": []}
        c.execute("""INSERT INTO evidence(evidence_id, snapshot_id, kind, symbol, source_id, authority_type, content_sha256,
                     observed_at, available_at, first_observed_at, captured_at, time_precision, availability_basis, quality,
                     replay_id, canonical_payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (eid, snap["snapshot_id"], "rule_derivation", None, e["source_ref"]["source_id"], "user", raw_sha,
                   None, None, now, now, "unknown", "unknown", "verified", replay_id, canonical_json(e)))
    return eid


# ── 任务 ──
@worker.register("seat")
def run_job(ctx: worker.JobContext):
    sid = ctx.payload["seat_run_id"]
    obj = execute(ctx.db, sid, ctx)
    case_id = obj.get("case_id")
    if case_id:
        from . import case_orchestrator
        case_orchestrator.on_seat_done(ctx.db, case_id)
    state = "failed" if obj["run_state"] == "failed" else ("partial" if obj["run_state"] == "partial" else "succeeded")
    if state == "failed":
        raise JobFailed(obj["error"]["error"]["code"] if obj.get("error") else "SEAT_FAILED",
                        obj["error"]["error"]["message"] if obj.get("error") else "席位运行失败", partial_resource=sid)
    return JobResult(state, resource_id=sid, resource_type="seat_run")


@worker.on_failure("seat")
def _seat_failed(db, row, code):
    import json as _j
    p = _j.loads(row["canonical_payload"])
    sid = p.get("seat_run_id")
    r = db.one("SELECT canonical_payload FROM seat_runs WHERE seat_run_id=?", (sid,))
    if r:
        full = _j.loads(r["canonical_payload"])
        ctx = full.pop("_ctx", {})
        if full["run_state"] in ("queued", "running"):
            full.update({"run_state": "failed", "verdict": "unknown", "candidate_count": None, "candidates": [],
                         "finished_at": clock.utc_iso(), "error": _err(code, "席位任务失败", sid)})
            with db.txn() as c:
                _update(c, full, ctx)
        if full.get("case_id"):
            from . import case_orchestrator
            case_orchestrator.on_seat_done(db, full["case_id"])
