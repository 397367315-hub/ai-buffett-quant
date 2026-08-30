#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"
SITE_ID="${NETLIFY_SITE_ID:-cffe3243-094e-44f9-8bc4-d3274c6fe8da}"
SITE_URL="${NETLIFY_SITE_URL:-https://ai-buffett-quant.netlify.app}"
VERIFY_PATH="${NETLIFY_VERIFY_PATH:-/pro/stock?code=600519}"

cd "${ROOT_DIR}"
npx netlify status >/dev/null

# Netlify's Next.js plugin writes the deploy bundle under frontend/.netlify.
# Running from the repository root can leave a stale root-level bundle and
# publish HTML that references chunks which were never uploaded.
rm -rf "${FRONTEND_DIR}/.next" "${FRONTEND_DIR}/.netlify"
cd "${FRONTEND_DIR}"
npx netlify deploy \
  --prod \
  --site "${SITE_ID}" \
  --skip-functions-cache \
  --timeout 900 \
  --message "${NETLIFY_DEPLOY_MESSAGE:-Production frontend deploy}"

python3 - "${SITE_URL}${VERIFY_PATH}" <<'PY'
import re
import sys
import urllib.error
import urllib.request

page_url = sys.argv[1]
request = urllib.request.Request(
    page_url,
    headers={"Cache-Control": "no-cache", "User-Agent": "AI Buffett deploy verifier"},
)
with urllib.request.urlopen(request, timeout=45) as response:
    html = response.read().decode("utf-8", "replace")
    if response.status != 200:
        raise SystemExit(f"Page verification failed: HTTP {response.status}")

origin = page_url.split("/", 3)[:3]
origin = "/".join(origin)
assets = sorted(set(re.findall(
    r'(?:src|href)="([^"]+/_next/static/[^"]+|/_next/static/[^"]+)"',
    html,
)))
if not assets:
    raise SystemExit("Page verification failed: no Next.js assets found")

failed = []
for asset in assets:
    asset_url = asset if asset.startswith("http") else origin + asset
    try:
        asset_request = urllib.request.Request(
            asset_url,
            headers={"Cache-Control": "no-cache", "User-Agent": "AI Buffett deploy verifier"},
        )
        with urllib.request.urlopen(asset_request, timeout=45) as response:
            response.read(1)
            if response.status != 200:
                failed.append(f"{response.status} {asset}")
    except urllib.error.HTTPError as exc:
        failed.append(f"{exc.code} {asset}")
    except Exception as exc:  # noqa: BLE001 - verifier must report every asset failure.
        failed.append(f"{type(exc).__name__} {asset}")

if failed:
    raise SystemExit("Static asset verification failed:\n" + "\n".join(failed))
print(f"Verified page and {len(assets)} Next.js assets: {page_url}")
PY
