#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"
SITE_ID="${NETLIFY_SITE_ID:-cffe3243-094e-44f9-8bc4-d3274c6fe8da}"
SITE_URL="${NETLIFY_SITE_URL:-https://ai-buffett-quant.netlify.app}"
VERIFY_PATH="${NETLIFY_VERIFY_PATH:-/pro/stock?code=600519}"

cd "${ROOT_DIR}"
npx netlify status >/dev/null

# Build separately: the Next.js plugin restores .next on completion. Deploy
# only the prepared runtime directory, then verify before promoting it.
rm -rf "${FRONTEND_DIR}/.next" "${FRONTEND_DIR}/.netlify"
cd "${FRONTEND_DIR}"
NETLIFY_SITE_ID="${SITE_ID}" npx netlify build --context production
test -d "${FRONTEND_DIR}/.netlify/static/_next/static"
test -d "${FRONTEND_DIR}/.netlify/functions-internal"

DEPLOY_REPORT="$(mktemp /tmp/ai-buffett-deploy.XXXXXX)"
PROMOTE_REPORT="$(mktemp /tmp/ai-buffett-promote.XXXXXX)"
trap 'rm -f "${DEPLOY_REPORT}" "${PROMOTE_REPORT}"' EXIT
npx netlify deploy \
  --no-build \
  --dir .netlify/static \
  --functions .netlify/functions-internal \
  --site "${SITE_ID}" \
  --skip-functions-cache \
  --timeout 900 \
  --message "${NETLIFY_DEPLOY_MESSAGE:-Production frontend deploy}" \
  --json > "${DEPLOY_REPORT}"

verify_page() {
python3 - "$1" <<'PY'
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
}

PREVIEW_URL="$(python3 -c 'import json,sys; value=json.load(open(sys.argv[1])); print(value["deploy_url"])' "${DEPLOY_REPORT}")"
verify_page "${PREVIEW_URL}${VERIFY_PATH}"
PROMOTE_DATA="$(python3 -c 'import json,sys; value=json.load(open(sys.argv[1])); print(json.dumps({"site_id":sys.argv[2],"deploy_id":value["deploy_id"]}))' "${DEPLOY_REPORT}" "${SITE_ID}")"
npx netlify api restoreSiteDeploy --data "${PROMOTE_DATA}" > "${PROMOTE_REPORT}"
verify_page "${SITE_URL}${VERIFY_PATH}"
