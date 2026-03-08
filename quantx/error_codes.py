"""Shared error codes for operational readiness and live execution paths."""

from __future__ import annotations

QX_READY_BLOCKED = "QX-READY-001"
QX_EXEC_CYCLE_LIMIT = "QX-EXEC-001"
QX_EXEC_PLACE_ORDER_EMPTY = "QX-EXEC-002"


def with_code(code: str, detail: str) -> str:
    """Build a stable error message prefix that is easy to alert/search on."""

    return f"{code}:{detail}"
