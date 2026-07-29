import sys
import os
import asyncio
import json
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from main import app
from fastapi.testclient import TestClient

# Initialize client once
_client = None

def get_client():
    global _client
    if _client is None:
        _client = TestClient(app)
    return _client

def handler(event, context):
    """Netlify Function handler - proxies requests to FastAPI app"""
    try:
        http_method = event.get("httpMethod", "GET")
        path = event.get("path", "/")
        query_params = event.get("queryStringParameters") or {}
        body = event.get("body") or ""
        headers = event.get("headers") or {}

        # Build URL with query params
        url = path
        if query_params:
            params = "&".join(f"{k}={v}" for k, v in query_params.items())
            url = f"{path}?{params}"

        # Normalize headers
        req_headers = {}
        for k, v in headers.items():
            k_lower = k.lower()
            if k_lower in ("host", "connection", "content-length"):
                continue
            req_headers[k] = v

        if body and "content-type" not in req_headers:
            req_headers["content-type"] = "application/json"

        content = body.encode() if body else None

        client = get_client()
        response = client.request(
            method=http_method,
            url=url,
            headers=req_headers,
            content=content,
        )

        return {
            "statusCode": response.status_code,
            "headers": {
                "content-type": response.headers.get("content-type", "application/json"),
                "access-control-allow-origin": "*"
            },
            "body": response.text,
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"code": 500, "message": str(e)}),
        }
