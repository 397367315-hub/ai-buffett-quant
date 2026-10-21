"""旧账本导入（JOURNAL-005）：先 dry_run 预览，再 commit 到独立新组合；原字节先归档。

- 输入是原样 UTF-8 文本（保留 BOM 与 CRLF）：分件 SHA-256 = sha256(text.encode("utf-8"))；
  组合 source_sha256 = sha256(canonical-json-v1([["ledger_json", 字节数, sha], ["trades_jsonl", 字节数, sha] | null]))；
- 解析用 utf-8-sig + parse_float=Decimal；金额超过两位小数、价格×股数≠成交额、卖超、坏 JSONL 行、缺 id、
  同 id 不同内容、JSONL 与账本不一致——任何差异都进拒绝清单，整批 status=rejected，不静默合并、不自动加现金调整；
- 现金对账：期初 + Σ 成交净额 必须等于账本现金（分）；
- commit 在一个事务里写：import_manifests + 新组合 + 期初/成交/观察事件 + 投影 + 幂等响应；
  同一 source 已提交过 → 返回原结果；旧文件本身不删、不改。
旧「模拟成交」导入后仍是 imported_legacy，不升级为券商成交。
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from ..config import get_settings
from ..data.evidence_store import EvidenceStore
from ..domain import clock, faults, trading_rules as tr
from ..domain.canonical import canonical_json, content_hash, new_id, sha256_hex
from ..domain.money import mul_shares
from ..web.errors import ApiError
from . import journal_service as js

SOURCE_PRIORITY = "legacy_source_priority_v1：portfolio.json 的 trades 为账户事实；JSONL 只作逐条交叉核对"


def combined_sha(ledger_text: str, trades_text: str | None) -> str:
    lb = ledger_text.encode("utf-8")
    items = [["ledger_json", len(lb), sha256_hex(lb)]]
    if trades_text is None:
        items.append(None)
    else:
        tb = trades_text.encode("utf-8")
        items.append(["trades_jsonl", len(tb), sha256_hex(tb)])
    return content_hash(items)


def _strip_bom(t: str) -> str:
    return t[1:] if t.startswith("﻿") else t


def _money2(v, field, row_id, rejected) -> Decimal | None:
    if v is None:
        rejected.append({"legacy_trade_id": row_id, "reason": f"{field} 缺失"})
        return None
    d = v if isinstance(v, Decimal) else Decimal(str(v))
    if d.as_tuple().exponent < -2 and d != d.quantize(Decimal("0.01")):
        rejected.append({"legacy_trade_id": row_id, "reason": f"{field}={v} 超过两位小数，无法解释"})
        return None
    return d


def analyze(body: dict) -> dict:
    lt, tt = body["ledger_json_text"], body["trades_jsonl_text"]
    raw_total = len(lt.encode("utf-8")) + (len(tt.encode("utf-8")) if tt is not None else 0)
    if raw_total > get_settings().max_import_raw_bytes:
        raise ApiError(413, "IMPORT_TOO_LARGE", "旧账本原始文本合计超过 4 MiB")
    if sha256_hex(lt.encode("utf-8")) != body["ledger_sha256"]:
        raise ApiError(422, "SOURCE_HASH_MISMATCH", "ledger_json_text 的 SHA-256 与声明不符（原字节必须原样传入）",
                       [{"field": "ledger_sha256", "reason": "mismatch"}])
    if (tt is None) != (body["trades_sha256"] is None) or (tt is not None and sha256_hex(tt.encode("utf-8")) != body["trades_sha256"]):
        raise ApiError(422, "SOURCE_HASH_MISMATCH", "trades_jsonl_text 的 SHA-256 与声明不符",
                       [{"field": "trades_sha256", "reason": "mismatch"}])
    if combined_sha(lt, tt) != body["source_sha256"]:
        raise ApiError(422, "SOURCE_HASH_MISMATCH", "组合 source_sha256 与原件清单不符",
                       [{"field": "source_sha256", "reason": "mismatch"}])
    rejected: list[dict] = []
    try:
        doc = json.loads(_strip_bom(lt), parse_float=Decimal)
    except ValueError as ex:
        raise ApiError(422, "LEGACY_LEDGER_UNPARSEABLE", f"portfolio.json 不是合法 JSON：{ex}")
    led = doc.get("ledger") if isinstance(doc, dict) and "ledger" in doc else doc
    if not isinstance(led, dict) or "trades" not in led:
        raise ApiError(422, "LEGACY_LEDGER_UNRECOGNIZED", "不认识的旧账本结构（缺 ledger.trades）")
    trades = led.get("trades") or []
    initial = _money2(led.get("initial"), "initial", None, rejected)
    cash = _money2(led.get("cash"), "cash", None, rejected)
    seen: dict[str, dict] = {}
    dup = 0
    good: list[dict] = []
    holdings: dict[str, int] = {}
    for t in sorted(trades, key=lambda x: str(x.get("ts"))):
        tid = t.get("id")
        if not tid:
            rejected.append({"legacy_trade_id": None, "reason": "成交缺 id"})
            continue
        key = canonical_json({k: (str(v) if isinstance(v, Decimal) else v) for k, v in t.items()})
        if tid in seen:
            if seen[tid]["key"] == key:
                dup += 1
                continue
            rejected.append({"legacy_trade_id": tid, "reason": "同一 id 内容不同（冲突，拒绝自动合并）"})
            continue
        seen[tid] = {"key": key, "t": t}
        sym = tr.to_symbol(str(t.get("code") or ""))
        if sym is None:
            rejected.append({"legacy_trade_id": tid, "reason": f"无法识别代码 {t.get('code')!r}"})
            continue
        side = t.get("side")
        sh = t.get("shares")
        if side not in ("buy", "sell") or not isinstance(sh, int) or sh <= 0:
            rejected.append({"legacy_trade_id": tid, "reason": "side/shares 不合法"})
            continue
        price = t.get("price")
        price = price if isinstance(price, Decimal) else (Decimal(str(price)) if price is not None else None)
        amount = _money2(t.get("amount"), "amount", tid, rejected)
        fee = t.get("fee") or {}
        fees = {k: _money2(fee.get(k), f"fee.{k}", tid, rejected) for k in ("commission", "stamp", "transfer", "total")}
        net = _money2(t.get("net"), "net", tid, rejected)
        if price is None or amount is None or net is None or any(v is None for v in fees.values()):
            continue
        if mul_shares(price, sh) != amount:
            rejected.append({"legacy_trade_id": tid, "reason": f"价格×股数={mul_shares(price, sh)} 与成交额 {amount} 不符"})
            continue
        if fees["commission"] + fees["stamp"] + fees["transfer"] != fees["total"]:
            rejected.append({"legacy_trade_id": tid, "reason": "费用分项之和不等于合计"})
            continue
        exp_net = -(amount + fees["total"]) if side == "buy" else amount - fees["total"]
        if exp_net != net:
            rejected.append({"legacy_trade_id": tid, "reason": f"净额 {net} 与成交额/费用不符（应为 {exp_net}）"})
            continue
        if side == "sell" and sh > holdings.get(sym, 0):
            rejected.append({"legacy_trade_id": tid, "reason": f"卖出 {sh} 股超过当时持仓 {holdings.get(sym, 0)}"})
            continue
        holdings[sym] = holdings.get(sym, 0) + (sh if side == "buy" else -sh)
        good.append({"id": tid, "ts": t.get("ts"), "symbol": sym, "name": t.get("name") or "", "side": side,
                     "shares": sh, "price": price, "amount": amount, "fees": fees, "net": net})
    # JSONL 交叉核对
    if tt is not None:
        for n, line in enumerate(_strip_bom(tt).splitlines(), start=1):
            if not line.strip():
                continue
            try:
                j = json.loads(line, parse_float=Decimal)
            except ValueError:
                rejected.append({"legacy_trade_id": None, "reason": f"JSONL 第 {n} 行不是合法 JSON"})
                continue
            jid = j.get("id") if isinstance(j, dict) else None
            if not jid:
                rejected.append({"legacy_trade_id": None, "reason": f"JSONL 第 {n} 行缺 id"})
                continue
            jj = {k: v for k, v in j.items() if k != "savedAt"}
            if jid not in seen:
                rejected.append({"legacy_trade_id": jid, "reason": "JSONL 有、当前账本没有（可能是重置前的旧流水）；不自动并入"})
                continue
            if canonical_json({k: (str(v) if isinstance(v, Decimal) else v) for k, v in jj.items()}) != seen[jid]["key"]:
                rejected.append({"legacy_trade_id": jid, "reason": "JSONL 与账本同 id 内容不同（冲突）"})
    delta = None
    if initial is not None and cash is not None:
        total_net = sum((g["net"] for g in good), Decimal(0))
        delta = int(((initial + total_net - cash) * 100).to_integral_value())
        if delta != 0:
            rejected.append({"legacy_trade_id": None, "reason": f"现金对账差 {delta} 分（期初+净额 ≠ 账本现金）；不自动调整"})
    watch = [w for w in (led.get("watch") or []) if isinstance(w, dict) and w.get("code")]
    return {"initial": initial, "cash": cash, "good": good, "duplicates": dup, "rejected": rejected,
            "delta": delta, "watch": watch}


def run(c, db, body: dict, idem: tuple[str, str]) -> dict:
    a = analyze(body)
    src = body["source_sha256"]
    prev = c.execute("SELECT canonical_payload FROM import_manifests WHERE source_sha256=? AND status='committed'",
                     (src,)).fetchone()
    if prev and not body["dry_run"]:
        return json.loads(prev["canonical_payload"])
    status = "rejected" if a["rejected"] else ("validated" if body["dry_run"] else "committed")
    import_id = new_id("import")
    now = clock.utc_iso()
    res = {"import_id": import_id, "dry_run": body["dry_run"], "source_sha256": src, "portfolio_id": None,
           "accepted_event_count": 0 if a["rejected"] else len(a["good"]) + len(a["watch"]) + 1,
           "duplicate_event_count": a["duplicates"], "rejected_rows": a["rejected"][:500],
           "cash_reconciliation_delta_minor": str(a["delta"]) if a["delta"] is not None else None,
           "status": status, "backup_manifest_path": None, "created_at": now}
    store = EvidenceStore(get_settings().evidence_dir)
    files = [("ledger.json", body["ledger_json_text"].encode("utf-8"))]
    if body["trades_jsonl_text"] is not None:
        files.append(("trades.jsonl", body["trades_jsonl_text"].encode("utf-8")))
    manifest = {"import_id": import_id, "source_kind": body["source_kind"], "source_sha256": src,
                "source_priority": SOURCE_PRIORITY,
                "files": [{"name": n, "size_bytes": len(b), "sha256": store.put(b)} for n, b in files],
                "created_at": now}
    if status == "committed":
        bdir = get_settings().runtime_dir / "imports" / import_id
        bdir.mkdir(parents=True, exist_ok=True)
        for n, b in files:
            (bdir / n).write_bytes(b)
            if sha256_hex((bdir / n).read_bytes()) != sha256_hex(b):
                raise ApiError(503, "BACKUP_VERIFY_FAILED", "原件备份校验失败，未导入")
        (bdir / "backup_manifest.json").write_text(canonical_json(manifest), encoding="utf-8")
        res["backup_manifest_path"] = f"runtime/imports/{import_id}/backup_manifest.json"
        pid = new_id("portfolio")
        c.execute("""INSERT INTO portfolios(portfolio_id, mode, revision, cash_minor, market_value_minor, total_equity_minor,
                     quality, last_event_sequence, replay_id, created_at, updated_at, canonical_payload)
                     VALUES(?,'paper',0,0,NULL,NULL,'complete',0,NULL,?,?,?)""",
                  (pid, now, now, canonical_json({"name": f"旧账本导入 {now[:10]}", "watch": [], "import_id": import_id})))
        res["portfolio_id"] = pid
        p = dict(c.execute("SELECT * FROM portfolios WHERE portfolio_id=?", (pid,)).fetchone())
        init = js._event(pid, "opening_balance", "imported_legacy", None, effective_at=now,
                         session_date=clock.shanghai_date(), net_cash_delta_minor=str(int(a["initial"] * 100)),
                         note=f"旧账本期初资金（导入 {import_id}）")
        js._append(c, p, init, idem)
        for i, g in enumerate(a["good"]):
            if i == 1:
                faults.checkpoint("legacy_import_after_first_batch")
            p = dict(c.execute("SELECT * FROM portfolios WHERE portfolio_id=?", (pid,)).fetchone())
            ts = clock.parse_utc(str(g["ts"]))
            fees = {"commission_minor": str(int(g["fees"]["commission"] * 100)),
                    "stamp_minor": str(int(g["fees"]["stamp"] * 100)),
                    "transfer_minor": str(int(g["fees"]["transfer"] * 100)),
                    "total_minor": str(int(g["fees"]["total"] * 100)), "fee_model_version": "legacy-paper-fee-v1"}
            ev = js._event(pid, "paper_fill", "imported_legacy", None, effective_at=clock.utc_iso(ts),
                           session_date=clock.shanghai_date(ts), symbol=g["symbol"], side=g["side"], shares=g["shares"],
                           price_decimal=format(g["price"], "f"), gross_amount_minor=str(int(g["amount"] * 100)),
                           net_cash_delta_minor=str(int(g["net"] * 100)), fees=fees, legacy_trade_id=g["id"],
                           note=f"旧模拟盘成交（{g['name']}，导入 {import_id}；不是券商成交）")
            ev["_legacy_source_sha256"] = src
            js._append(c, p, ev, idem)
        for w in a["watch"]:
            sym = tr.to_symbol(str(w["code"]))
            if not sym:
                continue
            p = dict(c.execute("SELECT * FROM portfolios WHERE portfolio_id=?", (pid,)).fetchone())
            js._append(c, p, js._event(pid, "watch_added", "imported_legacy", None, symbol=sym,
                                       note=f"旧观察清单：{w.get('name') or ''}（来源席位 {w.get('seat') or '未记'}）"), idem)
        state = js.replay_events(js.events_of(c, pid))
        if state["cash"] != int(a["cash"] * 100):
            raise ApiError(422, "IMPORT_RECONCILIATION_FAILED", "导入后现金与旧账本不一致，已回滚")
    c.execute("""INSERT INTO import_manifests(import_id, source_sha256, source_kind, portfolio_id, dry_run, status,
                 accepted_event_count, duplicate_event_count, cash_reconciliation_delta_minor, backup_manifest_json,
                 created_at, canonical_payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (import_id, src, body["source_kind"], res["portfolio_id"], int(body["dry_run"]), status,
               res["accepted_event_count"], res["duplicate_event_count"], a["delta"],
               canonical_json(manifest) if status == "committed" else None, now, canonical_json(res)))
    return res
