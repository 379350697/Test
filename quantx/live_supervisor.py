from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LiveSupervisor:
    state: str = 'bootstrap_pending'
    required_healthy_cycles: int = 3
    consecutive_healthy_cycles: int = 0
    last_degrade_reason: str | None = None

    def mark_bootstrap_ready(self) -> None:
        if self.state in {'bootstrap_pending', 'readiness_blocked'}:
            self.state = 'warming'
            self.consecutive_healthy_cycles = 0

    def mark_live_active(self) -> None:
        if self.state in {'warming', 'reduce_only'}:
            self.state = 'live_active'
            self.consecutive_healthy_cycles = 0

    def mark_read_only(self) -> None:
        if self.state != 'blocked':
            self.state = 'read_only'
            self.consecutive_healthy_cycles = 0

    def on_stream_gap_detected(self, *, reason: str = 'stream_gap') -> None:
        if self.state not in {'blocked', 'read_only'}:
            self.state = 'reduce_only'
            self.consecutive_healthy_cycles = 0
            self.last_degrade_reason = reason

    def on_position_mismatch_detected(self) -> None:
        self.state = 'blocked'
        self.consecutive_healthy_cycles = 0
        self.last_degrade_reason = 'position_mismatch'

    def execution_mode(self) -> str:
        if self.state == 'live_active':
            return 'live'
        if self.state == 'reduce_only':
            return 'reduce_only'
        if self.state in {'warming', 'read_only'}:
            return 'read_only'
        return 'blocked'

    def record_health_cycle(self, *, healthy: bool, cycle_boundary: bool) -> None:
        if self.state != 'reduce_only':
            return
        if not healthy:
            self.consecutive_healthy_cycles = 0
            return
        if not cycle_boundary:
            return
        self.consecutive_healthy_cycles += 1
        if self.consecutive_healthy_cycles >= self.required_healthy_cycles:
            self.mark_live_active()

    def allow_order(self, *, reduce_only: bool) -> bool:
        mode = self.execution_mode()
        if mode == 'live':
            return True
        if mode == 'reduce_only':
            return bool(reduce_only)
        return False
