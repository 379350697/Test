"""Audit trail with hash chaining for critical actions (P2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AuditEvent:
    ts: str
    actor: str
    action: str
    payload: dict[str, Any]
    prev_hash: str
    event_hash: str


class AuditTrail:
    """Append-only audit log with deterministic hash-chain integrity."""

    def __init__(self):
        self.events: list[AuditEvent] = []

    def append(self, actor: str, action: str, payload: dict[str, Any]) -> AuditEvent:
        prev = self.events[-1].event_hash if self.events else "GENESIS"
        ts = datetime.utcnow().isoformat()
        event_hash = _hash_event(ts=ts, actor=actor, action=action, payload=payload, prev_hash=prev)
        ev = AuditEvent(ts=ts, actor=actor, action=action, payload=payload, prev_hash=prev, event_hash=event_hash)
        self.events.append(ev)
        return ev

    def verify(self) -> bool:
        prev = "GENESIS"
        for ev in self.events:
            expected = _hash_event(ts=ev.ts, actor=ev.actor, action=ev.action, payload=ev.payload, prev_hash=prev)
            if ev.prev_hash != prev or ev.event_hash != expected:
                return False
            prev = ev.event_hash
        return True


class JsonlAuditStore:
    """Persistent append-only audit store backed by jsonl file."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: AuditEvent) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":")) + "\n")

    def load(self) -> list[AuditEvent]:
        if not self.path.exists():
            return []
        rows: list[AuditEvent] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                rows.append(
                    AuditEvent(
                        ts=str(d["ts"]),
                        actor=str(d["actor"]),
                        action=str(d["action"]),
                        payload=dict(d["payload"]),
                        prev_hash=str(d["prev_hash"]),
                        event_hash=str(d["event_hash"]),
                    )
                )
        return rows

    def verify(self) -> bool:
        trail = AuditTrail()
        trail.events = self.load()
        return trail.verify()


def _hash_event(ts: str, actor: str, action: str, payload: dict[str, Any], prev_hash: str) -> str:
    blob = json.dumps(
        {
            "ts": ts,
            "actor": actor,
            "action": action,
            "payload": payload,
            "prev_hash": prev_hash,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
