#!/usr/bin/env python3
"""本机预览用的最小 HTTP 客户端（与浏览器同样的会话/CSRF/幂等键规则），可选地预置一份 file_import 快照。

    python3 tools/preview_client.py --port 8787 --seed-snapshot 2026-10-19 --symbols 600000.SH,000001.SZ

只连 127.0.0.1；不读任何凭据。
"""
from __future__ import annotations

import argparse
import http.client
import json
import secrets
import sys
import time


class LocalClient:
    def __init__(self, port: int):
        self.port = port
        self.cookie = None
        self.csrf = None

    def req(self, method: str, path: str, body=None, key: str | None = None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=600)
        h = {"Host": f"127.0.0.1:{self.port}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            h["Content-Type"] = "application/json"
        if method == "POST":
            h["Origin"] = f"http://127.0.0.1:{self.port}"
            h["Idempotency-Key"] = key or f"cli-{secrets.token_hex(8)}"
            if self.csrf:
                h["X-CSRF-Token"] = self.csrf
        if self.cookie:
            h["Cookie"] = f"rt_local_session={self.cookie}"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        for k, v in r.getheaders():
            if k.lower() == "set-cookie" and v.startswith("rt_local_session="):
                self.cookie = v.split(";")[0].split("=", 1)[1]
        try:
            obj = json.loads(raw) if raw else None
        except ValueError:
            obj = None
        return r.status, obj

    def bootstrap(self):
        st, obj = self.req("POST", "/api/v1/session/bootstrap", {})
        assert st == 201, (st, obj)
        self.csrf = obj["csrf_token"]
        return obj

    def wait(self, job_id: str, timeout: float = 900):
        t0 = time.time()
        while True:
            st, j = self.req("GET", f"/api/v1/jobs/{job_id}")
            if j and j["state"] in ("succeeded", "partial", "failed", "cancelled"):
                return j
            if time.time() - t0 > timeout:
                raise TimeoutError(job_id)
            time.sleep(0.5)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--seed-snapshot")
    ap.add_argument("--symbols", default="")
    a = ap.parse_args(argv)
    c = LocalClient(a.port)
    c.bootstrap()
    if a.seed_snapshot:
        syms = [s for s in a.symbols.split(",") if s]
        st, acc = c.req("POST", "/api/v1/snapshots", {"session_date": a.seed_snapshot, "source_mode": "file_import",
                                                      "symbols": syms, "allow_stale": True})
        print(st, json.dumps(acc, ensure_ascii=False)[:300])
        if st == 202:
            j = c.wait(acc["job"]["job_id"])
            print(json.dumps(j, ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
