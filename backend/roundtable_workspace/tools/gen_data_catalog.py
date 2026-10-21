#!/usr/bin/env python3
"""从猫爪（Numcat）OpenAPI 文档生成数据接口目录（ADR-014）。

    python3 tools/gen_data_catalog.py            # 读 contracts/vendor/numcat-openapi.json
    python3 tools/gen_data_catalog.py --spec /path/api-1-2.json --out panel/services/data_catalog.json

生成物 `panel/services/data_catalog.json` 是**只读白名单**：`data_bridge` 只允许调这里列出的接口，
参数只允许文档里声明的那些。文档更新了就重新生成，不要手改。

规则：
- 每个 POST 端点取 `operationId / apiname / fields 默认值 / params 表格`；
- `stream`（订阅类）与 `download`（文件下载）标记出来并从查询白名单里排除（订阅要长连接，下载落盘都超出聊天工具的范围）；
- 额外补几个站点文档里没有、但本工作台已经在用的接口（见 EXTRA_OPS）。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "contracts/vendor/numcat-openapi.json"
DEFAULT_OUT = ROOT / "records/roundtable/2026-10-18-圆桌面板设计/panel/services/data_catalog.json"

# 站点文档（api-1-2）里没有、但行情/席位脚本一直在用的接口：照旧放进目录，标注 source=extra。
EXTRA_OPS = [
    {"op": "reference_index_indexdaily", "apiname": "indexdaily", "path": "(vendor) 指数日线",
     "tag": "指数", "summary": "指数日线（收盘/涨跌幅）", "fields": "tradedate,symbol,name,close,pre_close,pct_chg",
     "params": {"symbols": {"type": "array", "required": False, "example": ["000001.SH"], "desc": "指数代码"},
                "tradedate": {"type": "string", "required": False, "example": "20261021", "desc": "交易日 YYYYMMDD"}}},
]

ROW_RE = re.compile(r"^\|\s*(?P<name>`[^`]+`|[^|]+?)\s*\|\s*(?P<type>[^|]+?)\s*\|\s*(?P<req>可选|必选|必需)\s*\|"
                    r"\s*(?P<example>[^|]*?)\s*\|\s*(?P<desc>[^|]*?)\s*\|\s*$")


def parse_params(desc: str | None) -> dict:
    """把参数说明的 markdown 表格解析成 {name: {type, required, example, desc}}。"""
    out: dict = {}
    for line in (desc or "").splitlines():
        # 文档里类型列会写成 `string\|array`（转义竖线），先换掉，否则整行解析失败
        m = ROW_RE.match(line.strip().replace("\\|", "/"))
        if not m:
            continue
        name = m.group("name").strip().strip("`")
        if not name or name in ("参数", "---"):
            continue
        typ = m.group("type").strip().strip("`")
        example = m.group("example").strip().strip("`")
        out[name] = {"type": typ, "required": m.group("req") in ("必选", "必需"),
                     "example": example, "desc": m.group("desc").strip()[:160]}
    return out


def build(spec_path: Path) -> dict:
    doc = json.loads(spec_path.read_text(encoding="utf-8"))
    ops = []
    for path, item in sorted(doc.get("paths", {}).items()):
        for method, op in item.items():
            if method.lower() != "post":
                continue
            schema = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema") or {}
            props = schema.get("properties") or {}
            ap = props.get("apiname") or {}
            apiname = ap.get("default") or ""
            fields = (props.get("fields") or {}).get("default") or ""
            params_block = props.get("params") or {}
            tag = (op.get("tags") or ["未分类"])[0]
            summary = op.get("summary") or ""
            opid = op.get("operationId") or ""
            stream = "stream" in opid or "订阅" in summary
            download = "download" in opid or "下载" in summary
            ops.append({"op": opid, "apiname": apiname, "path": path, "tag": tag, "summary": summary,
                        "fields": fields, "params": parse_params(params_block.get("description")),
                        "stream": stream, "download": download, "source": "spec"})
    have = {o["op"] for o in ops}
    for extra in EXTRA_OPS:
        if extra["op"] not in have:
            ops.append({**extra, "stream": False, "download": False, "source": "extra"})
    info = doc.get("info") or {}
    return {"version": 1, "source": {"openapi": doc.get("openapi"), "info_version": info.get("version"),
                                     "title": info.get("title"), "spec_file": spec_path.name},
            "ops": sorted(ops, key=lambda o: (o["tag"], o["op"]))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="从猫爪 OpenAPI 生成数据接口目录")
    ap.add_argument("--spec", default=str(DEFAULT_SPEC))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    spec = Path(a.spec).expanduser()
    if not spec.is_file():
        print(f"找不到 OpenAPI 文档：{spec}")
        return 2
    cat = build(spec)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cat, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    q = [o for o in cat["ops"] if not o["stream"] and not o["download"]]
    print(f"{len(cat['ops'])} 个接口 → {out}（可查询 {len(q)}，订阅 {sum(o['stream'] for o in cat['ops'])}，"
          f"下载 {sum(o['download'] for o in cat['ops'])}，额外 {sum(o['source']=='extra' for o in cat['ops'])}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
