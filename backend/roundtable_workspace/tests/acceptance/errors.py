"""driver 可抛出的受控状态（与断言失败区分）。"""
from __future__ import annotations


class Blocked(Exception):
    """前置条件不满足而无法执行（例如 P4 能力闸门按设计关闭）。status=blocked，不算 pass。"""

    def __init__(self, reason: str, code: str = "BLOCKED"):
        super().__init__(reason)
        self.reason, self.code = reason, code


class NotRun(Exception):
    """按规格本阶段不执行（R1 上线用例：用户确认发布候选后才在隔离预发/正式目标运行）。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class DriverNotImplemented(Exception):
    pass
