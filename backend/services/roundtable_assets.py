"""Server-only encrypted resources for the private roundtable book library."""

from __future__ import annotations

import base64
import hashlib
import io
import os
from pathlib import Path
import tarfile
import tempfile
import threading


MAGIC = b"AI-BUFFETT-ROUNDTABLE-ASSETS-1\x00"
STATE_MAGIC = b"AI-BUFFETT-ROUNDTABLE-SECRETS-1\x00"
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 96 * 1024 * 1024
_lock = threading.Lock()
_prepared: dict[str, Path] = {}


class RoundtableAssetsError(RuntimeError):
    pass


def decode_asset_key(value: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(value.strip().encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise RoundtableAssetsError("交易圆桌资料密钥格式无效") from exc
    if len(key) != 32:
        raise RoundtableAssetsError("交易圆桌资料密钥格式无效")
    return key


def encrypt_private_state(data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    key = decode_asset_key(os.environ.get("ROUNDTABLE_ASSET_KEY", ""))
    return STATE_MAGIC + nonce + AESGCM(key).encrypt(nonce, data, STATE_MAGIC)


def decrypt_private_state(data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not data.startswith(STATE_MAGIC) or len(data) < len(STATE_MAGIC) + 28:
        raise RoundtableAssetsError("圆桌模型密钥存档格式无效")
    key = decode_asset_key(os.environ.get("ROUNDTABLE_ASSET_KEY", ""))
    nonce = data[len(STATE_MAGIC):len(STATE_MAGIC) + 12]
    try:
        return AESGCM(key).decrypt(nonce, data[len(STATE_MAGIC) + 12:], STATE_MAGIC)
    except Exception as exc:
        raise RoundtableAssetsError("圆桌模型密钥存档无法解密") from exc


def _unpack(payload: bytes, target: Path) -> None:
    total = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tf:
        members = tf.getmembers()
        if len(members) > 10000:
            raise RoundtableAssetsError("交易圆桌资料包条目过多")
        for member in members:
            path = Path(member.name)
            if (path.is_absolute() or ".." in path.parts or not path.parts
                    or path.parts[0] not in {"skills", "knowledge"}
                    or not (member.isdir() or member.isfile())):
                raise RoundtableAssetsError("交易圆桌资料包路径无效")
            total += member.size
            if total > MAX_EXPANDED_BYTES:
                raise RoundtableAssetsError("交易圆桌资料包过大")
        for member in members:
            dest = target / member.name
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = tf.extractfile(member)
                if source is None:
                    raise RoundtableAssetsError("交易圆桌资料包不完整")
                with dest.open("xb") as out:
                    out.write(source.read())
                dest.chmod(0o600)
    if not (target / "knowledge/manifest.json").is_file() or not (target / "skills").is_dir():
        raise RoundtableAssetsError("交易圆桌资料包缺少知识库或技能")


def prepare_roundtable_assets(vendor_root: Path) -> Path:
    """Decrypt once per process. Plain source material is never a web asset."""
    root = Path(vendor_root).resolve()
    override = os.environ.get("ROUNDTABLE_ASSET_ROOT")
    if override:
        candidate = Path(override).resolve()
        if (candidate / "knowledge/manifest.json").is_file() and (candidate / "skills").is_dir():
            return candidate
    encrypted = root / "private_assets.aesgcm"
    if not encrypted.is_file():
        if os.environ.get("ROUNDTABLE_ALLOW_PLAINTEXT_ASSETS") == "1" and not os.environ.get("RENDER"):
            if (root / "knowledge/manifest.json").is_file() and (root / "skills").is_dir():
                return root
        raise RoundtableAssetsError("交易圆桌私有资料包尚未安装")
    key_text = os.environ.get("ROUNDTABLE_ASSET_KEY", "")
    if not key_text:
        raise RoundtableAssetsError("交易圆桌私有资料尚未配置解密密钥")
    data = encrypted.read_bytes()
    if len(data) > MAX_ARCHIVE_BYTES or not data.startswith(MAGIC) or len(data) < len(MAGIC) + 28:
        raise RoundtableAssetsError("交易圆桌私有资料包格式无效")
    digest = hashlib.sha256(data).hexdigest()
    with _lock:
        ready = _prepared.get(digest)
        if ready and ready.is_dir():
            return ready
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = data[len(MAGIC):len(MAGIC) + 12]
        try:
            plaintext = AESGCM(decode_asset_key(key_text)).decrypt(nonce, data[len(MAGIC) + 12:], MAGIC)
        except Exception as exc:
            raise RoundtableAssetsError("交易圆桌私有资料解密失败，请检查服务端配置") from exc
        target = Path(tempfile.mkdtemp(prefix="roundtable-private-assets-"))
        target.chmod(0o700)
        try:
            _unpack(plaintext, target)
        except Exception:
            import shutil
            shutil.rmtree(target, ignore_errors=True)
            raise
        _prepared[digest] = target
        return target
