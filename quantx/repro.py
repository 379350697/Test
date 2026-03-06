from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from typing import Any


def stable_hash(payload: Any) -> str:
    dumped = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]


def python_fingerprint() -> str:
    return f"{platform.python_implementation()}-{platform.python_version()}-{platform.platform()}"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
