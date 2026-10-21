#!/usr/bin/env python3
"""把「旧五页面板」的构建产物压成一个可双击打开的只读单文件 standalone.html（legacy 静态快照）。

v1 工作台（frontend/src）只在本地服务下运行（需要 /api/v1 会话、CSRF 与服务端账本），不能做成离线单文件；
这里打包的是旧界面的最后一次构建（frontend/legacy_src_20261019/dist），只作只读查看——服务端在训练上下文里一律 409。

做的事只有三件：
  1. dist/index.html 里的 <script src> 与 <link rel=stylesheet> 换成内联
  2. dashboard.json 里每个席位的 /avatars/*.webp 换成 base64 data URL
  3. 把 dashboard 整体注入成 window.__DASHBOARD__

用法：
    python3 build_standalone.py            # 读 ../frontend/dist 与 ./data/dashboard.json
    python3 build_standalone.py --check    # 只报告体积与是否已过期，不写文件

前端改了要重跑 npm run build，数据刷新了要重跑这个脚本——否则单文件还停在旧数据上。
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FRONT = ROOT / "frontend"
DIST = FRONT / "legacy_src_20261019" / "dist"      # 旧界面构建产物（v1 工作台不再打成单文件）
DATA = HERE / "data" / "dashboard.json"
OUT = FRONT / "standalone.html"


def inline_assets(html: str) -> str:
    """把 dist 的 js/css 引用替换成内联内容。"""
    def js(m: re.Match) -> str:
        src = DIST / m.group(1).lstrip("/")
        code = src.read_text(encoding="utf-8")
        # 内联进 HTML 的脚本里若出现字面量 </script> 会提前闭合标签
        code = code.replace("</script>", "<\\/script>")
        return f"<script type=\"module\">{code}</script>"

    def css(m: re.Match) -> str:
        src = DIST / m.group(1).lstrip("/")
        return "<style>\n" + src.read_text(encoding="utf-8") + "\n</style>"

    html = re.sub(r'<script type="module"[^>]*src="([^"]+)"[^>]*></script>', js, html)
    html = re.sub(r'<link rel="stylesheet"[^>]*href="([^"]+)"[^>]*>', css, html)
    return html


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def inline_portraits(dash: dict) -> tuple[dict, int, list[str]]:
    """席位头像是 /avatars/xxx.webp 形式的相对路径，压成 data URL。"""
    missing, done = [], 0
    for seat in dash.get("seats", []):
        p = seat.get("portrait")
        if not p or p.startswith("data:"):
            continue
        f = FRONT / "public" / p.lstrip("/")
        if not f.exists():
            missing.append(p)
            continue
        seat["portrait"] = data_url(f)
        done += 1
    return dash, done, missing


def build(check: bool = False) -> int:
    if not (DIST / "index.html").exists():
        print(f"找不到旧界面构建产物 {DIST}/index.html", file=sys.stderr)
        return 2
    if not DATA.exists():
        print("找不到 panel/data/dashboard.json，先跑 refresh.py", file=sys.stderr)
        return 2

    html = inline_assets((DIST / "index.html").read_text(encoding="utf-8"))
    dash = json.loads(DATA.read_text(encoding="utf-8"))
    dash, n_portrait, missing = inline_portraits(dash)

    payload = json.dumps(dash, ensure_ascii=False, separators=(",", ":"))
    html = html.replace(
        "</head>",
        f'    <script>window.__DASHBOARD__={payload};</script>\n  </head>', 1)

    stale = OUT.exists() and (OUT.stat().st_mtime < max(
        DATA.stat().st_mtime,
        (DIST / "index.html").stat().st_mtime))
    size = len(html.encode("utf-8"))

    print(f"席位头像内联 {n_portrait} 张" + (f"，缺 {missing}" if missing else ""))
    print(f"候选 {len(dash.get('candidates') or [])} 只 · 数据时间 {dash.get('asOf')}")
    if check:
        print(f"当前单文件 {OUT.stat().st_size/1e6:.2f}MB" if OUT.exists() else "单文件尚未生成")
        print("已过期，需要重建" if stale else "是最新的")
        return 1 if stale else 0

    OUT.write_text(html, encoding="utf-8")
    print(f"写入 {OUT}  {size/1e6:.2f}MB")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    sys.exit(build(ap.parse_args().check))
