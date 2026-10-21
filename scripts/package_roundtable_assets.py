"""Package private book notes for a public code repository without publishing them.

The generated key stays in a private local env file and must be provisioned as
ROUNDTABLE_ASSET_KEY on the backend. It is never printed or embedded in a bundle.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from services.roundtable_assets import MAGIC, decode_asset_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "backend/roundtable_workspace")
    parser.add_argument("--key-file", type=Path,
                        default=Path.home() / ".codex/credentials/ai-buffett-roundtable-assets.env")
    args = parser.parse_args()
    source = args.source.resolve()
    key_file = args.key_file.expanduser()
    if key_file.exists():
        values = dict(line.split("=", 1) for line in key_file.read_text().splitlines()
                      if line.strip() and not line.startswith("#") and "=" in line)
        key = decode_asset_key(values.get("ROUNDTABLE_ASSET_KEY", ""))
    else:
        key = secrets.token_bytes(32)
        key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write("ROUNDTABLE_ASSET_KEY=" + base64.urlsafe_b64encode(key).decode("ascii") + "\n")
    raw = io.BytesIO()
    count = 0
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for directory in ("skills", "knowledge"):
            base = source / directory
            if not base.is_dir():
                raise SystemExit(f"Missing private resource directory: {directory}")
            for path in sorted(base.rglob("*")):
                if not path.is_file() or path.name.startswith(".") or "__pycache__" in path.parts:
                    continue
                if path.is_symlink():
                    raise SystemExit("Private resource symlinks are not supported")
                data = path.read_bytes()
                info = tarfile.TarInfo(str(path.relative_to(source)))
                info.size, info.mode, info.mtime = len(data), 0o600, 0
                tf.addfile(info, io.BytesIO(data))
                count += 1
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    compressed = gzip.compress(raw.getvalue(), mtime=0)
    nonce = secrets.token_bytes(12)
    payload = MAGIC + nonce + AESGCM(key).encrypt(nonce, compressed, MAGIC)
    target = source / "private_assets.aesgcm"
    target.write_bytes(payload)
    manifest = {"format": "AES-256-GCM", "version": 1, "file_count": count,
                "encrypted_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                "key_environment": "ROUNDTABLE_ASSET_KEY"}
    target.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"bundle": str(target), "private_key_file": str(key_file), **manifest}))


if __name__ == "__main__":
    main()
