"""PumpPortal WebSocket: every new pump.fun token (free) + live trades (needs a funded API key).

Docs: https://pumpportal.fun/data-api/real-time  — one connection, subscribe/unsubscribe via JSON.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import aiohttp

log = logging.getLogger(__name__)
WS_URL = "wss://pumpportal.fun/api/data"

OnEvent = Callable[[dict], Awaitable[None]]


class PumpPortal:
    def __init__(self, on_new_token: OnEvent, on_trade: OnEvent, api_key: str = "") -> None:
        self.api_key = api_key
        self.on_new_token = on_new_token
        self.on_trade = on_trade
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._subscribed: set[str] = set()

    async def subscribe_trades(self, mints: list[str]) -> None:
        if not self.api_key:
            return  # trade stream is no longer free; launches still are
        mints = [m for m in mints if m not in self._subscribed]
        if mints and self._ws is not None and not self._ws.closed:
            await self._ws.send_json({"method": "subscribeTokenTrade", "keys": mints})
            self._subscribed.update(mints)

    async def unsubscribe_trades(self, mints: list[str]) -> None:
        mints = [m for m in mints if m in self._subscribed]
        if mints and self._ws is not None and not self._ws.closed:
            await self._ws.send_json({"method": "unsubscribeTokenTrade", "keys": mints})
        self._subscribed.difference_update(mints)

    async def run(self) -> None:
        backoff = 2
        while True:
            try:
                async with aiohttp.ClientSession() as sess, sess.ws_connect(
                        WS_URL + (f"?api-key={self.api_key}" if self.api_key else ""), heartbeat=30) as ws:
                    self._ws = ws
                    resub = list(self._subscribed)
                    self._subscribed.clear()
                    await ws.send_json({"method": "subscribeNewToken"})
                    await self.subscribe_trades(resub)
                    log.info("pumpportal connected")
                    backoff = 2
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            data = json.loads(msg.data)
                        except ValueError:
                            continue
                        if not isinstance(data, dict) or "mint" not in data:
                            continue  # subscription acks etc.
                        if data.get("txType") == "create":
                            await self.on_new_token(data)
                        if data.get("txType") in ("create", "buy", "sell"):
                            await self.on_trade(data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network hiccups: reconnect
                log.warning("pumpportal disconnected: %s", exc)
            self._ws = None
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)
