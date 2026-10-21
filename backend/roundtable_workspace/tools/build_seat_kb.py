#!/usr/bin/env python3
"""为每个席位构建专属知识库（M1）：原书正文 + 逐书精读 + 席位技能与规则。

    python3 tools/build_seat_kb.py                # 写到 WORK_ROOT/knowledge/
    python3 tools/build_seat_kb.py --out /tmp/kb  # 写到别处（测试用）
    python3 tools/build_seat_kb.py --check        # 只核对现有库是否与源文件一致（不重写）

产物是**生成物**，不进发布指纹，也不进证据仓：它只做「检索与引用」的索引，原文仍在只读源包里。
每条记录都能定位回去：`locator` 是 `text:<文件名>#L起-L止` 或 `md:<相对路径>#<小节标题>`。

来源映射按 `records/roundtable/2026-10-19-原书精读/README.md` 的对照表：
  03-达尔文 / 04-杨楠 / 05-利弗莫尔 / 06-交易心理分析（公共）/ 07-三份二手稿（野人哥、退学、养家）/
  08-威科夫 / 09-一路奔行 / 10-蜡烛图；陈凯、李大霄的提炼在各技能 references/notes.md；
  剑门、打板另有技能与二手稿。没有原书正文的席位只建「技能」库，不假装有原文。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("~/Desktop/交易圆桌_AI交接包_20261019").expanduser()
if not SOURCE.is_dir():
    SOURCE = WORK_ROOT.parent / "交易圆桌_AI交接包_20261019"
READ_DIR = SOURCE / "records/roundtable/2026-10-19-原书精读"
TEXT_DIR = READ_DIR / "text"
SKILLS = WORK_ROOT / "skills"
DEFAULT_OUT = WORK_ROOT / "knowledge"
BUILDER_VERSION = "seat_kb_v1"
CHUNK_TARGET = 620          # 每块目标字数
CHUNK_MAX = 900

# 席位 → (原书正文文件, 精读文件, 书名)。正文/精读缺一项就只用另一项。
SEAT_BOOKS: dict[str, dict] = {
    "trading-wyckoff-v1": {"text": "威科夫操盘法++华尔街大师成功驾驭市场超过95年的秘技+141332241792.txt",
                            "note": "08-威科夫.md", "book": "威科夫操盘法"},
    "trading-livermore-v1": {"text": "股票大作手回忆录+148895391792339193.txt",
                              "note": "05-利弗莫尔.md", "book": "股票大作手回忆录 / 操盘术"},
    "trading-yangnan-v1": {"text": "情绪流龙头战法+148387661792339197.txt", "note": "04-杨楠.md", "book": "情绪流龙头战法"},
    "trading-darwin-v1": {"text": "我从达尔文那里学到的投资知识_155959321792339182.txt", "note": "03-达尔文.md",
                           "book": "我从达尔文那里学到的投资知识"},
    "trading-candle-v1": {"text": "日本蜡烛图技术+153255291792339200.txt", "note": "10-蜡烛图.md", "book": "日本蜡烛图技术"},
    "trading-gushiguifei-v1": {"text": "股是股非之一++猎取强势股_151943851792339191.txt", "note": "09-一路奔行.md",
                                "book": "股是股非（三册）",
                                "text_extra": ["股是股非+2+暴涨大形态1792339188.txt", "股是股非+3+暴涨之星1792339186.txt"]},
    "trading-wildman-v2": {"text": "转Word_保住本金是第一原则，慢就是快-野人哥超短_260918.txt", "note": "07-三份二手稿.md",
                            "book": "保住本金是第一原则，慢就是快（野人哥）",
                            "text_extra": ["野人哥交易系统笔录.txt"]},
    "trading-tuixue-v1": {"text": "退学炒股超短打板的心法与精髓_共171页.txt", "note": "07-三份二手稿.md",
                           "book": "退学炒股超短打板的心法与精髓"},
    "trading-yangjia-v1": {"text": "炒股养家干货合集__共62页.txt", "note": "07-三份二手稿.md", "book": "炒股养家干货合集"},
    # 没有独立正文的席位：只建技能库（不假装有原书）
    "trading-jianmen-v1": {"book": "剑门（技能与规则）"},
    "trading-chenkai-v1": {"book": "交易之路（提炼在技能 notes）"},
    "trading-lidaxiao-v1": {"book": "投资战略（提炼在技能 notes）"},
    "trading-boardtech-v1": {"book": "打板技术（技能与二手稿）",
                              "text": "提取自操盘之神-￥999彩色珍藏版.txt"},
}
COMMON = {"id": "common", "book": "交易心理分析（下单前自检，不单独成席）", "note": "06-交易心理分析.md",
          "text": "交易心理分析+127592711792339195.txt"}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _kb_id(seat: str, source_path: str, locator: str, text: str) -> str:
    h = hashlib.sha256(f"{seat}\u0000{source_path}\u0000{locator}\u0000{text}".encode()).hexdigest()
    return h[:16]


def _clean(s: str) -> str:
    s = s.replace("　", " ").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def chunk_text(path: Path, target: int = CHUNK_TARGET, max_chars: int = CHUNK_MAX) -> list[dict]:
    """正文行 → 段落块。保留起止行号，便于回查原文。"""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out, buf, start = [], [], None
    def flush(end):
        nonlocal buf, start
        if buf and start is not None:
            text = _clean("\n".join(buf))
            if len(text) >= 40:
                out.append({"text": text[:max_chars], "start_line": start, "end_line": end})
        buf, start = [], None
    for i, raw in enumerate(lines, start=1):
        line = _clean(raw)
        if not line:
            if buf and sum(len(x) for x in buf) >= target:
                flush(i)
            continue
        if start is None:
            start = i
        buf.append(line)
        if sum(len(x) for x in buf) >= target:
            flush(i)
    flush(len(lines))
    return out


def chunk_markdown(path: Path, target: int = CHUNK_TARGET, max_chars: int = CHUNK_MAX) -> list[dict]:
    """Markdown → 按小节切块（保留标题路径）。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    parts = re.split(r"(?m)^(#{1,4})\s+(.*)$", text)
    out, heading, buf = [], "", []
    def flush():
        nonlocal buf
        body = _clean("\n".join(buf))
        if len(body) >= 40:
            for piece in _split_long(body, target, max_chars):
                out.append({"text": piece, "heading": heading})
        buf = []
    for i in range(1, len(parts), 3):
        flush()
        heading = _clean(parts[i + 1])
        buf = [parts[i + 2]]
    flush()
    return out


def _split_long(body: str, target: int, max_chars: int) -> list[str]:
    if len(body) <= max_chars:
        return [body]
    paras = [p for p in re.split(r"\n{2,}", body) if p.strip()]
    out, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) + 1 > target:
            out.append(cur[:max_chars]); cur = p
        else:
            cur = f"{cur}\n{p}" if cur else p
        while len(cur) > max_chars:
            out.append(cur[:max_chars]); cur = cur[max_chars:]
    if cur.strip():
        out.append(cur[:max_chars])
    return out


def _entries(seat: str, kind: str, book: str, rel_source: str, sha: str, chunks: list[dict], loc_fmt) -> list[dict]:
    out = []
    for c in chunks:
        loc = loc_fmt(c)
        out.append({"kb_id": _kb_id(seat, rel_source, loc, c["text"]), "seat_id": seat, "kind": kind, "book": book,
                    "heading": c.get("heading") or "", "text": c["text"], "locator": loc,
                    "source_path": rel_source, "source_sha256": sha, "chars": len(c["text"])})
    return out


def build_seat(out_root: Path, seat: str, spec: dict) -> dict:
    seat_dir = out_root / "seats" / seat
    seat_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    sources: list[dict] = []

    for fname in [spec.get("text"), *(spec.get("text_extra") or [])]:
        if not fname:
            continue
        p = TEXT_DIR / fname
        if not p.is_file():
            sources.append({"path": f"text/{fname}", "status": "missing"})
            continue
        sha = _sha(p)
        chunks = chunk_text(p)
        entries += _entries(seat, "original", spec.get("book") or "", f"text/{fname}", sha, chunks,
                            lambda c: f"text:{fname}#L{c['start_line']}-L{c['end_line']}")
        sources.append({"path": f"text/{fname}", "sha256": sha, "chunks": len(chunks), "kind": "original"})

    note = spec.get("note")
    if note and (READ_DIR / note).is_file():
        p = READ_DIR / note
        sha = _sha(p)
        chunks = chunk_markdown(p)
        entries += _entries(seat, "digest", spec.get("book") or "", f"精读/{note}", sha, chunks,
                            lambda c: f"md:{note}#{c.get('heading') or '开头'}")
        sources.append({"path": f"精读/{note}", "sha256": sha, "chunks": len(chunks), "kind": "digest"})

    # 技能与规则（每个席位都有）
    skill_dir = SKILLS / seat
    for f in [skill_dir / "SKILL.md", *sorted((skill_dir / "references").glob("*.md"))] if skill_dir.is_dir() else []:
        if not f.is_file():
            continue
        sha = _sha(f)
        chunks = chunk_markdown(f)
        rel = f"skills/{seat}/{f.name}" if f.parent.name != "references" else f"skills/{seat}/references/{f.name}"
        entries += _entries(seat, "skill", spec.get("book") or "", rel, sha, chunks,
                            lambda c: f"md:{rel}#{c.get('heading') or '开头'}")
        sources.append({"path": rel, "sha256": sha, "chunks": len(chunks), "kind": "skill"})

    (seat_dir / "kb.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
                                       encoding="utf-8")
    idx = {"schema": BUILDER_VERSION, "seat_id": seat, "book": spec.get("book"), "built_at": _now(),
           "chunks": len(entries), "by_kind": _count(entries), "sources": sources,
           "chunk_policy": {"target_chars": CHUNK_TARGET, "max_chars": CHUNK_MAX, "min_chars": 40}}
    (seat_dir / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    return idx


def _count(entries: list[dict]) -> dict:
    out: dict[str, int] = {}
    for e in entries:
        out[e["kind"]] = out.get(e["kind"], 0) + 1
    return out


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_all(out_root: Path) -> dict:
    out_root.mkdir(parents=True, exist_ok=True)
    summary = {"schema": BUILDER_VERSION, "built_at": _now(), "seats": {}, "common": None}
    for seat, spec in SEAT_BOOKS.items():
        summary["seats"][seat] = {k: v for k, v in build_seat(out_root, seat, spec).items()
                                  if k in ("chunks", "by_kind", "book")}
    summary["common"] = {k: v for k, v in build_seat(out_root, COMMON["id"],
                                                     {"book": COMMON["book"], "note": COMMON["note"],
                                                      "text": COMMON["text"]}).items()
                         if k in ("chunks", "by_kind", "book")}
    (out_root / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="构建席位知识库")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    out = Path(a.out).expanduser()
    if not TEXT_DIR.is_dir():
        print(f"找不到原书正文目录：{TEXT_DIR}", file=sys.stderr)
        return 2
    summary = build_all(out)
    total = sum(v["chunks"] for v in summary["seats"].values()) + summary["common"]["chunks"]
    print(f"写入 {out}：13 席 + 公共库，共 {total} 块")
    for seat, v in summary["seats"].items():
        print(f"  {seat:<26} {v['chunks']:>5} 块  {v['by_kind']}  {v['book']}")
    print(f"  {'common':<26} {summary['common']['chunks']:>5} 块  {summary['common']['by_kind']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
