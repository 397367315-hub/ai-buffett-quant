"""v1 处理函数注册入口：导入即注册（handler 装饰器写入 app.HANDLERS）。"""
from . import session, health, snapshots, seats, jobs, legacy  # noqa: F401  P0
from . import seat_runs, cases, plans, reviews, journal, imports, workspace  # noqa: F401  P1
from . import replays, validations  # noqa: F401  P2
from . import decks, alerts  # noqa: F401  P3 / P4（P4 由能力闸门返回 501）
from . import llm  # noqa: F401  ADR-007 模型接口（默认关闭）
