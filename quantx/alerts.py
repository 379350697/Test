"""Alert routing abstraction for monitoring pipelines (P1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import time
import urllib.error
import urllib.request

from .system_log import EventLogger, LogEvent


@dataclass(slots=True)
class AlertMessage:
    level: str
    title: str
    body: str
    ts: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class WebhookAlertChannel:
    """Webhook sender with retry/backoff for operational alerts."""

    def __init__(self, url: str, timeout_s: float = 5.0, max_retries: int = 2, retry_backoff_ms: int = 100):
        self.url = url
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_ms = retry_backoff_ms

    def send(self, message: AlertMessage) -> dict[str, str]:
        payload = {
            "level": message.level,
            "title": message.title,
            "body": message.body,
            "ts": message.ts,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "quantx/0.1"},
            method="POST",
        )

        attempts = max(1, self.max_retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s):
                    return {"status": "sent", "channel": "webhook", "url": self.url}
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                time.sleep(self.retry_backoff_ms / 1000)

        assert last_error is not None
        return {"status": "failed", "channel": "webhook", "url": self.url, "error": str(last_error)}


class AlertRouter:
    """Alert sink with optional structured event logging and channel dispatch."""

    def __init__(self, event_logger: EventLogger | None = None):
        self.sent: list[dict[str, str]] = []
        self.event_logger = event_logger
        self.channels: dict[str, WebhookAlertChannel] = {}

    def register_webhook(self, channel: str, url: str, *, timeout_s: float = 5.0, max_retries: int = 2, retry_backoff_ms: int = 100) -> None:
        self.channels[channel] = WebhookAlertChannel(
            url=url,
            timeout_s=timeout_s,
            max_retries=max_retries,
            retry_backoff_ms=retry_backoff_ms,
        )

    def send(self, channel: str, message: AlertMessage) -> dict[str, str]:
        rec = {
            "channel": channel,
            "level": message.level,
            "title": message.title,
            "body": message.body,
            "ts": message.ts,
        }

        channel_sender = self.channels.get(channel)
        if channel_sender is not None:
            send_result = channel_sender.send(message)
            rec["delivery"] = send_result.get("status", "unknown")

        self.sent.append(rec)
        if self.event_logger is not None:
            self.event_logger.log(
                LogEvent(
                    category="alert",
                    event="alert_sent",
                    level="WARN" if message.level.upper() in {"WARN", "ERROR"} else "INFO",
                    stage="notify",
                    payload=rec,
                )
            )
        return rec
