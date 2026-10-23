"""Small, rate-limited Wikimedia HTTP client with bounded retries."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ALLOWED_HOSTS = {"ko.wikipedia.org", "www.wikidata.org"}
USER_AGENT = "MovieLab/1.0 (contact required)"


def retry_delay(value: str, fallback: float) -> float:
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        date = parsedate_to_datetime(value)
        return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return fallback


class WikimediaClient:
    def __init__(self, *, interval: float = 1.0, attempts: int = 3, opener=urlopen, sleep=time.sleep, clock=time.monotonic):
        self.interval = interval
        self.attempts = attempts
        self.opener = opener
        self.sleep = sleep
        self.clock = clock
        self.last_request = -float("inf")

    def get_json(self, host: str, params: dict[str, str | int]) -> dict:
        if host not in ALLOWED_HOSTS:
            raise ValueError("unsupported Wikimedia host")
        url = f"https://{host}/w/api.php?{urlencode({'maxlag': 5, **params})}"
        for attempt in range(self.attempts):
            delay = self.last_request + self.interval - self.clock()
            if delay > 0:
                self.sleep(delay)
            self.last_request = self.clock()
            agent = os.getenv("MOVIELAB_WIKIMEDIA_USER_AGENT", "").strip()
            if not agent:
                raise RuntimeError("Set MOVIELAB_WIKIMEDIA_USER_AGENT to include a contact URL or email")
            request = Request(url, headers={
                "User-Agent": agent,
                "Accept": "application/json",
            })
            try:
                with self.opener(request, timeout=15) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise ValueError("Wikimedia response must be an object")
                error = payload.get("error")
                if error:
                    if error.get("code") in {"maxlag", "ratelimited"} and attempt + 1 < self.attempts:
                        self.sleep(2 ** attempt)
                        continue
                    raise RuntimeError("Wikimedia API returned an error")
                return payload
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 == self.attempts:
                    raise
                wait = retry_delay(exc.headers.get("Retry-After", ""), 2 ** attempt)
                if wait > 30:
                    raise RuntimeError("Wikimedia requested a retry delay over 30 seconds") from exc
                self.sleep(wait)
            except URLError:
                if attempt + 1 == self.attempts:
                    raise
                self.sleep(2 ** attempt)
        raise RuntimeError("Wikimedia request failed")
