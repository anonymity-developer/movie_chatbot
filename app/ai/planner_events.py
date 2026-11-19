"""Request-scoped event publisher with failure-isolated subscribers."""

from datetime import datetime, timezone
import logging
import os
import re


class EventStream:
    def __init__(self, *subscribers):
        self.subscribers = subscribers
        self.events = []
        self.failed = False
        self.secrets = tuple(value for name in (
            "GMS_API_KEY", "KOBIS_API_KEY", "KAKAO_REST_API_KEY", "OPENAI_API_KEY"
        ) if (value := os.getenv(name)))

    def redact(self, value):
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[redacted]")
            return re.sub(r"(?i)([?&](?:key|api_key|token)=)[^&\s]+", r"\1[redacted]", value)
        if isinstance(value, dict):
            return {key: self.redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        return value

    def emit(self, event, **data):
        payload = {
            "sequence": len(self.events) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "data": self.redact(data),
        }
        self.events.append(payload)
        for subscriber in self.subscribers:
            try:
                subscriber(payload)
            except Exception:
                if not self.failed:
                    logging.getLogger(__name__).warning("Planner event subscriber failed")
                self.failed = True
