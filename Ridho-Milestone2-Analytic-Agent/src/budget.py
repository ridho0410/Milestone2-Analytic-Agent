"""Penghitung terpisah dan tenggat waktu monoton untuk setiap pesan pengguna."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .config import Settings, settings


class BudgetExceeded(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class RequestBudget:
    limits: Settings = field(default_factory=lambda: settings)
    clock: Callable[[], float] = time.monotonic
    tool_calls: int = 0
    model_calls: int = 0
    query_calls: int = 0
    repair_attempts: int = 0
    started: float = field(init=False)

    def __post_init__(self) -> None:
        self.started = self.clock()

    def remaining(self) -> float:
        remaining = self.limits.agent_timeout_seconds - (self.clock() - self.started)
        if remaining <= 0:
            raise BudgetExceeded("deadline")
        return remaining

    def timeout(self, operation_timeout: float) -> float:
        return min(operation_timeout, self.remaining())

    def consume(self, operation: str) -> None:
        self.remaining()
        field_name = {
            "tool": "tool_calls",
            "model": "model_calls",
            "query": "query_calls",
            "repair": "repair_attempts",
        }[operation]
        limit_name = {
            "tool": "max_tool_calls",
            "model": "max_model_calls",
            "query": "max_query_calls",
            "repair": "max_repair_attempts",
        }[operation]
        value = getattr(self, field_name)
        if value >= getattr(self.limits, limit_name):
            raise BudgetExceeded(field_name)
        # Hitung percobaan sebelum eksekusi agar kegagalan tidak dapat melewati batas.
        setattr(self, field_name, value + 1)

    def audit(self) -> dict:
        return {
            "tool_calls": self.tool_calls,
            "model_calls": self.model_calls,
            "query_calls": self.query_calls,
            "repair_attempts": self.repair_attempts,
            "elapsed_ms": round((self.clock() - self.started) * 1000),
        }


_active_budget: ContextVar[RequestBudget | None] = ContextVar(
    "request_budget", default=None
)


def current_budget() -> RequestBudget | None:
    return _active_budget.get()


@contextmanager
def budget_scope(budget: RequestBudget) -> Iterator[None]:
    token = _active_budget.set(budget)
    try:
        yield
    finally:
        _active_budget.reset(token)
