"""受控故障注入点（AC-035 / AC-078）：只有测试与验收 runner 会 arm；生产进程里永远是空操作。

checkpoint(name) 放在「事件已写、投影/幂等记录尚未提交」等关键位置；被 arm 时抛 InjectedFault，
由外层事务整体 ROLLBACK——用来证明不会留下半个计划、半条账本事件或孤立投影。
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_armed: dict[str, int] = {}


class InjectedFault(RuntimeError):
    pass


def arm(point: str, times: int = 1) -> None:
    with _lock:
        _armed[point] = times


def disarm_all() -> None:
    with _lock:
        _armed.clear()


def armed() -> dict:
    with _lock:
        return dict(_armed)


def checkpoint(point: str) -> None:
    with _lock:
        n = _armed.get(point)
        if not n:
            return
        if n <= 1:
            _armed.pop(point, None)
        else:
            _armed[point] = n - 1
    raise InjectedFault(f"injected fault at {point}")
