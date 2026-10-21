"""统一错误（02 §9）。

形状：{"error":{"code","message","request_id","retryable","details":[{field,reason}]}}
400 语法/未知参数，401 无会话，403 来源/CSRF/上下文不符，404 无资源或不可见，
409 冻结/版本/游标/幂等/状态冲突，413 超大体，422 语义/证据/精度，429 队列或源限流，
501 阶段关闭，503 数据源或存储不可用。栈、环境变量、供应商原始认证响应不返回浏览器。
"""
from __future__ import annotations

RETRYABLE_CODES = {"SOURCE_UNAVAILABLE", "STORAGE_UNAVAILABLE", "QUEUE_FULL", "SOURCE_RATE_LIMITED",
                   "DATA_STALE", "SNAPSHOT_PENDING"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list | None = None,
                 retryable: bool | None = None, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []
        self.retryable = (code in RETRYABLE_CODES) if retryable is None else retryable
        self.headers = headers or {}

    def payload(self, request_id: str) -> dict:
        return {"error": {"code": self.code, "message": self.message, "request_id": request_id,
                          "retryable": bool(self.retryable), "details": list(self.details)}}


def detail(field: str, reason: str) -> dict:
    return {"field": field, "reason": reason}


def bad_request(code: str, message: str, details=None) -> ApiError:
    return ApiError(400, code, message, details)


def unprocessable(code: str, message: str, details=None) -> ApiError:
    return ApiError(422, code, message, details)


def conflict(code: str, message: str, details=None) -> ApiError:
    return ApiError(409, code, message, details)


def not_found(code: str = "NOT_FOUND", message: str = "资源不存在或在当前上下文中不可见") -> ApiError:
    return ApiError(404, code, message)


def forbidden(code: str, message: str, details=None) -> ApiError:
    return ApiError(403, code, message, details)
