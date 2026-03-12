from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class LiveRuntimeStore:
    status_path: Path

    def write_status(self, payload: dict[str, Any]) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.status_path.with_suffix(self.status_path.suffix + '.tmp')
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
        os.replace(tmp_path, self.status_path)

    def read_status(self) -> dict[str, Any]:
        if not self.status_path.exists():
            return {}
        payload = json.loads(self.status_path.read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else {}
