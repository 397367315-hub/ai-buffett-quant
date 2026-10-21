#!/usr/bin/env python3
"""从 contracts/openapi.yaml 生成运行时路由表 panel/web/operations.json。

运行时只用标准库（不读 YAML）；契约变化后重跑本脚本并提交生成结果：
    python3 tools/gen_operations.py
生成文件含每个操作的 method/path/operationId/x-phase/安全要求/请求与响应 schema 名/参数表。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml   # 开发工具依赖（requirements-spec.txt 固定版本），运行时不需要

HERE = Path(__file__).resolve().parent
WORK_ROOT = HERE.parent
OPENAPI = WORK_ROOT / "contracts" / "openapi.yaml"
OUT = WORK_ROOT / "records/roundtable/2026-10-18-圆桌面板设计/panel/web/operations.json"


def ref_name(s):
    if not s:
        return None
    if "$ref" in s:
        return s["$ref"].split("/")[-1]
    return None


def main() -> int:
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    global_security = doc.get("security") or []
    ops = []
    for path, item in doc["paths"].items():
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            security = op.get("security", global_security)
            needs_session = any("LocalSession" in s for s in security) if security else False
            needs_csrf = any("CsrfToken" in s for s in security) if security else False
            params = []
            for p in op.get("parameters", []):
                params.append({"name": p["name"], "in": p["in"], "required": bool(p.get("required")),
                               "schema": p.get("schema", {})})
            body = None
            rb = op.get("requestBody")
            if rb:
                sch = (rb.get("content", {}).get("application/json") or {}).get("schema")
                body = ref_name(sch)
            responses = {}
            for code, r in op.get("responses", {}).items():
                if "$ref" in r:
                    continue
                c = (r.get("content") or {}).get("application/json")
                responses[code] = {"schema": ref_name(c.get("schema")) if c else None,
                                   "headers": sorted((r.get("headers") or {}).keys())}
            ops.append({
                "operation_id": op["operationId"], "method": method.upper(), "path": path,
                "phase": op.get("x-phase"), "summary": op.get("summary"),
                "needs_session": needs_session, "needs_csrf": needs_csrf,
                "idempotency_required": any(p["name"] == "Idempotency-Key" and p["required"] for p in params),
                "params": params, "request_schema": body, "responses": responses,
            })
    out = {"generated_from": "contracts/openapi.yaml", "openapi_version": doc["info"]["version"],
           "operation_count": len(ops), "operations": ops}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(ops)} operations → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
