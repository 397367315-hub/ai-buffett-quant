"""席位知识库检索（M1）：只做「在自己的书里找依据」，不生成、不改写。

- 库是 `tools/build_seat_kb.py` 生成的 `knowledge/seats/<seat_id>/kb.jsonl`（原文 / 精读 / 技能三类）；
- 检索用 BM25（中文按二元组切分，英文按词），不依赖任何外部 embedding 服务与密钥；
- 每条结果都带 `locator`（原文行号或 Markdown 小节），引用要能回查到那一句；
- 知识库没有建好时返回空列表 + `missing`，调用方要说明「知识库未构建」，不能编。
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from ..config import ASSET_ROOT

WORK_ROOT = Path(__file__).resolve().parents[3]

_CJK = re.compile(r"[一-鿿]+")
_WORD = re.compile(r"[a-z0-9]+")


def kb_root() -> Path:
    return Path(os.environ.get("ROUNDTABLE_KB_ROOT") or (ASSET_ROOT / "knowledge")).expanduser()


def seat_path(seat_id: str) -> Path:
    return kb_root() / "seats" / seat_id / "kb.jsonl"


def tokenize(text: str) -> list[str]:
    """中文二元组 + 英文/数字词。查询与文档用同一套切法。"""
    text = text.lower()
    toks: list[str] = []
    for run in _CJK.findall(text):
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    toks.extend(_WORD.findall(text))
    return toks


class KbMissing(RuntimeError):
    pass


def _read(seat_id: str) -> list[dict]:
    p = seat_path(seat_id)
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


@lru_cache(maxsize=32)
def _index(seat_id: str, mtime: float) -> tuple[list[dict], list[Counter], dict, float]:
    rows = _read(seat_id)
    doc_toks = [Counter(tokenize(r["text"] + " " + (r.get("heading") or ""))) for r in rows]
    df: Counter = Counter()
    for c in doc_toks:
        df.update(set(c))
    avgdl = (sum(sum(c.values()) for c in doc_toks) / len(doc_toks)) if doc_toks else 0.0
    return rows, doc_toks, dict(df), avgdl


def loaded(seat_id: str) -> list[dict]:
    p = seat_path(seat_id)
    if not p.is_file():
        return []
    return _index(seat_id, p.stat().st_mtime)[0]


def search(seat_id: str, query: str, k: int = 8, kinds: tuple[str, ...] | None = None) -> dict:
    """返回 {results: [...], missing: bool, total_chunks: int, query_terms: n}。"""
    p = seat_path(seat_id)
    if not p.is_file():
        return {"results": [], "missing": True, "total_chunks": 0, "query_terms": 0}
    rows, doc_toks, df, avgdl = _index(seat_id, p.stat().st_mtime)
    q = [t for t in tokenize(query) if len(t) > 0]
    if not q:
        return {"results": [], "missing": False, "total_chunks": len(rows), "query_terms": 0}
    qtf = Counter(q)
    n = len(rows)
    k1, b = 1.4, 0.75
    scored = []
    for i, row in enumerate(rows):
        if kinds and row.get("kind") not in kinds:
            continue
        dl = sum(doc_toks[i].values()) or 1
        s = 0.0
        for term, qn in qtf.items():
            f = doc_toks[i].get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / (avgdl or 1))) * (1 + math.log(qn))
        if s > 0:
            scored.append((s, i))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for s, i in scored[:k]:
        r = rows[i]
        out.append({"kb_id": r["kb_id"], "score": round(s, 4), "kind": r["kind"], "book": r.get("book"),
                    "heading": r.get("heading"), "locator": r["locator"], "text": r["text"],
                    "source_path": r["source_path"]})
    return {"results": out, "missing": False, "total_chunks": len(rows), "query_terms": len(set(q))}


def get(kb_id: str) -> dict | None:
    """按 kb_id 取一条（校验引用时用）。"""
    root = kb_root() / "seats"
    if not root.is_dir():
        return None
    for seat_dir in sorted(root.iterdir()):
        p = seat_dir / "kb.jsonl"
        if not p.is_file():
            continue
        for r in _index(seat_dir.name, p.stat().st_mtime)[0]:
            if r["kb_id"] == kb_id:
                return r
    return None


def stats(seat_id: str) -> dict:
    p = seat_path(seat_id)
    if not p.is_file():
        return {"seat_id": seat_id, "missing": True}
    rows = _index(seat_id, p.stat().st_mtime)[0]
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    idx = p.parent / "index.json"
    meta = json.loads(idx.read_text(encoding="utf-8")) if idx.is_file() else {}
    return {"seat_id": seat_id, "missing": False, "chunks": len(rows), "by_kind": by_kind,
            "built_at": meta.get("built_at"), "sources": meta.get("sources")}


def seats_available() -> list[str]:
    root = kb_root() / "seats"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "kb.jsonl").is_file() and p.name != "common")
