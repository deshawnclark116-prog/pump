"""Tiny polite HTTP client: per-host spacing + backoff on 429/5xx so free tiers aren't abused."""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger(__name__)

# minimum seconds between requests to a host (free-tier limits)
HOST_SPACING = {
    "api.dexscreener.com": 0.25,     # 300 req/min on pair endpoints, 60/min on profiles
    "api.geckoterminal.com": 2.5,    # ~30 req/min
    "api.rugcheck.xyz": 1.0,
    "api.gopluslabs.io": 2.0,
    "www.reddit.com": 3.0,
}
USER_AGENT = "memeradar/0.1 (+https://github.com/deshawnclark116-prog/pump)"


class Http:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _wait_turn(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            gap = HOST_SPACING.get(host, 0.2)
            delay = self._last.get(host, 0) + gap - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[host] = time.monotonic()

    async def request(self, method: str, url: str, *, as_text: bool = False,
                      retries: int = 3, **kwargs):
        host = urlparse(url).netloc
        sess = await self.session()
        for attempt in range(retries + 1):
            await self._wait_turn(host)
            try:
                async with sess.request(method, url, **kwargs) as resp:
                    if resp.status == 429 or resp.status >= 500:
                        backoff = min(60, 2 ** (attempt + 2))
                        log.warning("%s %s -> %s, backing off %ss", method, host, resp.status, backoff)
                        self._last[host] = time.monotonic() + backoff
                        continue
                    if resp.status >= 400:
                        log.debug("%s %s -> %s", method, url, resp.status)
                        return None
                    if as_text:
                        return await resp.text()
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                log.debug("%s %s failed: %s", method, url, exc)
                await asyncio.sleep(1 + attempt)
        return None

    async def get(self, url: str, **kwargs):
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs):
        return await self.request("POST", url, **kwargs)
