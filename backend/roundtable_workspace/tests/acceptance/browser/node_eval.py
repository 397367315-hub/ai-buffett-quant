"""用 node 执行前端纯函数（src/lib/*.ts，经 esbuild 打包），让「前端怎么解析/怎么显示」也由真实代码给出投影。"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
FRONTEND = REPO_ROOT / "integrations/trading-roundtable/frontend"
ESBUILD = FRONTEND / "node_modules" / "esbuild"


def run_ts(body: str) -> dict:
    """body 是一段 TS：可 import '@lib/money' 等，最后 console.log(JSON.stringify(result))。"""
    with tempfile.TemporaryDirectory(prefix="rt-node-") as d:
        d = Path(d)
        src = body.replace("@lib/", str(FRONTEND / "src" / "lib") + "/")
        entry = d / "entry.ts"
        entry.write_text(src, encoding="utf-8")
        out = d / "out.cjs"
        build = (f"require({json.dumps(str(ESBUILD))}).buildSync({{entryPoints:[{json.dumps(str(entry))}],"
                 f"bundle:true,platform:'node',format:'cjs',target:'node18',outfile:{json.dumps(str(out))},logLevel:'error'}})")
        p = subprocess.run(["node", "-e", build], capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise RuntimeError(f"esbuild failed: {p.stderr[-800:]}")
        r = subprocess.run(["node", str(out)], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"node failed: {r.stderr[-800:]}")
        return json.loads(r.stdout.strip().splitlines()[-1])


COERCE = re.compile(r"(Number|parseFloat|parseInt)\s*\(\s*[^)]*_minor|\+\s*[A-Za-z_.]*_minor\b|_minor\s*\*|_minor\s*/")


def static_number_coercions() -> list[str]:
    hits = []
    for p in sorted((FRONTEND / "src").rglob("*.ts*")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            if COERCE.search(line):
                hits.append(f"{p.relative_to(FRONTEND)}:{i}: {line.strip()[:120]}")
    return hits


def money_client_check(v: str) -> dict:
    res = run_ts(f"""
import {{ parseMinor, formatMinor }} from '@lib/money';
const body = JSON.parse({json.dumps(json.dumps({"cash_minor": v}))});
const b = parseMinor(body.cash_minor);
console.log(JSON.stringify({{ client_type: typeof body.cash_minor, parsed: b === null ? null : b.toString(),
  formatted: formatMinor(body.cash_minor) }}));
""")
    hits = static_number_coercions()
    res.update({"parsed_equals": res["parsed"] == v, "js_number_coercions": len(hits), "coercion_sites": hits})
    return res


def seat_labels(triplets: list[dict]) -> list[str]:
    res = run_ts(f"""
import {{ seatStatusLabel }} from '@lib/labels';
const ts = {json.dumps(triplets)};
console.log(JSON.stringify({{ labels: ts.map(seatStatusLabel) }}));
""")
    return res["labels"]
