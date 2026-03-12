from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LiveSupervisor:
    state: str = 'bootstrap_pending'

    def mark_bootstrap_ready(self) -> None:
        if self.state in {'bootstrap_pending', 'readiness_blocked'}:
            self.state = 'warming'

    def mark_live_active(self) -> None:
        if self.state in {'warming', 'reduce_only'}:
            self.state = 'live_active'

    def mark_read_only(self) -> None:
        if self.state != 'blocked':
            self.state = 'read_only'

    def on_stream_gap_detected(self) -> None:
        if self.state not in {'blocked', 'read_only'}:
            self.state = 'reduce_only'

    def on_position_mismatch_detected(self) -> None:
        self.state = 'blocked'
