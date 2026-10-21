"""内容寻址的不可变证据仓（原始供应商响应、导入原件、席位原始输出、历史窗口归档）。

文件名就是 SHA-256：写入前算哈希、写入后读回校验；同内容只存一份；不提供删除或覆盖。
数据库只保存哈希与索引，取用时按哈希读回并再校验一次——被改过的文件直接报错，不自动修复。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from ..domain.canonical import canonical_bytes


class EvidenceCorrupted(RuntimeError):
    pass


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, sha: str) -> Path:
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("非法 sha256")
        return self.root / "blobs" / sha[:2] / f"{sha}.bin"

    def put(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        p = self._path(sha)
        if p.exists():
            if hashlib.sha256(p.read_bytes()).hexdigest() != sha:
                raise EvidenceCorrupted(f"已有证据文件被篡改：{sha}")
            return sha
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        try:
            os.chmod(p, 0o440)
        except OSError:
            pass
        return sha

    def put_json(self, obj) -> str:
        return self.put(canonical_bytes(obj))

    def get(self, sha: str) -> bytes:
        p = self._path(sha)
        if not p.exists():
            raise FileNotFoundError(f"证据不存在：{sha}")
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise EvidenceCorrupted(f"证据文件哈希不符：{sha}")
        return data

    def exists(self, sha: str) -> bool:
        return self._path(sha).exists()

    def locator(self, sha: str) -> str:
        return f"evidence:{sha}"
