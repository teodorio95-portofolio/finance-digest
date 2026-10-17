"""One polite HTTP client for every collector: ~1 request/second, two retries."""

from __future__ import annotations

import time

import httpx

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


class Fetcher:
    def __init__(self, delay: float = 1.0, timeout: float = 30.0) -> None:
        self.delay = delay
        self._last = 0.0
        self.client = httpx.Client(
            headers={"User-Agent": UA, "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
        )

    def get(self, url: str) -> httpx.Response:
        """GET with throttling; raises on transport errors and non-2xx after retries."""
        for attempt in range(3):
            wait = self._last + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.client.get(url)
            except httpx.TransportError:
                if attempt == 2:
                    raise
                continue
            finally:
                self._last = time.monotonic()
            if resp.status_code >= 500 and attempt < 2:
                continue
            resp.raise_for_status()
            return resp
        raise AssertionError("unreachable")

    def close(self) -> None:
        self.client.close()
