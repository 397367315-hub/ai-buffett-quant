#!/usr/bin/env python3
"""把一份全局 CSS 作用域化到一个外壳选择器下（旧版五页与新工作台两套样式并存，必须互不串味）。

    python3 tools/scope_css.py <输入.css> <输出.css> <作用域选择器> [--check]

- 普通规则：每个选择器前加作用域（`.legacy-shell .panel`）；
- html / body / :root：映射成作用域本身（把 CSS 变量挂在外壳上，不动页面根）；
- @media / @supports / @layer / @container：递归处理内部规则；
- @keyframes / @font-face / @page / @property / @import / @charset：原样保留（它们本来就不是选择器）；
- 注释原样保留。

--check：不写文件，只比较现有输出是否与重新生成的一致（不一致退出码 1），用于防止「改了源样式忘了重新生成」。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

NESTED_AT = ("@media", "@supports", "@layer", "@container", "@scope")
KEEP_AT = ("@keyframes", "@-webkit-keyframes", "@-moz-keyframes", "@font-face", "@page", "@property",
           "@counter-style", "@font-feature-values", "@viewport")


def _iter_blocks(css: str):
    i, n = 0, len(css)
    while i < n:
        while i < n and css[i].isspace():
            i += 1
        if i >= n:
            return
        if css.startswith("/*", i):
            j = css.find("*/", i + 2)
            j = n if j < 0 else j + 2
            yield ("comment", css[i:j], None)
            i = j
            continue
        j = i
        while j < n and css[j] not in "{;":
            if css.startswith("/*", j):
                k = css.find("*/", j + 2)
                j = n if k < 0 else k + 2
                continue
            j += 1
        if j >= n:
            yield ("text", css[i:], None)
            return
        if css[j] == ";":
            yield ("stmt", css[i:j + 1], None)
            i = j + 1
            continue
        prelude = css[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
            k += 1
        yield ("block", prelude, css[j + 1:k - 1])
        i = k


def scope_selector(sel: str, scope: str) -> str:
    sel = " ".join(sel.split())
    if not sel:
        return sel
    low = sel.lower()
    if low in ("html", "body", ":root", "html,body"):
        return scope
    m = re.match(r"^(html|body|:root)\b(?![\w-])\s*", sel)
    if m:
        rest = sel[m.end():].strip()
        return scope if not rest else f"{scope} {rest}"
    return f"{scope} {sel}"


def scope_rule(prelude: str, scope: str) -> str:
    sels = [s for s in (x.strip() for x in prelude.split(",")) if s]
    return ", ".join(scope_selector(s, scope) for s in sels)


def scope_css(css: str, scope: str, depth: int = 0) -> str:
    out = []
    for kind, a, b in _iter_blocks(css):
        if kind == "comment":
            out.append(a)
        elif kind == "stmt":
            out.append(a.strip())
        elif kind == "text":
            if a.strip():
                out.append(a.strip())
        else:
            prelude = a
            low = prelude.lower()
            if low.startswith(KEEP_AT):
                out.append(f"{prelude}{{{b}}}")
            elif low.startswith(NESTED_AT):
                out.append(f"{prelude}{{\n{scope_css(b, scope, depth + 1)}\n}}")
            else:
                out.append(f"{scope_rule(prelude, scope)}{{{b.strip()}}}")
    sep = "\n" if depth == 0 else "\n"
    return sep.join(x for x in out if x)


def build(src: Path, dst: Path, scope: str) -> str:
    css = src.read_text(encoding="utf-8")
    header = f"/* 由 tools/scope_css.py 从 {src.name} 生成，作用域 {scope}；不要手改，改源文件后重新生成。 */\n"
    return header + scope_css(css, scope) + "\n"


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 0 if argv else 2
    args = [a for a in argv if a != "--check"]
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    if len(args) != 3:
        print(__doc__.splitlines()[0], file=sys.stderr)
        print("用法：scope_css.py <输入.css> <输出.css> <作用域选择器> [--check]", file=sys.stderr)
        return 2
    src, dst, scope = Path(args[0]), Path(args[1]), args[2]
    text = build(src, dst, scope)
    if check:
        current = dst.read_text(encoding="utf-8") if dst.exists() else ""
        if current != text:
            print(f"过时：{dst} 与 {src} 作用域化后的结果不一致，请重新生成", file=sys.stderr)
            return 1
        print(f"一致：{dst}")
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    print(f"写入 {dst}（{len(text)} 字节，作用域 {scope}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
