"""Bounded upstream keepalive without sharing visitor cookie jars."""
from collections import OrderedDict
from contextlib import asynccontextmanager
import asyncio
import time

import anyio
import httpx


class BorrowedTransport(httpx.AsyncBaseTransport):
    def __init__(self, owner, key, entry):
        self.owner, self.key, self.entry = owner, key, entry
        self.closed = False

    async def handle_async_request(self, request):
        return await self.entry["transport"].handle_async_request(request)

    async def aclose(self):
        if not self.closed:
            self.closed = True
            await self.owner.release(self.key, self.entry)


class UpstreamPools:
    def __init__(self, maximum=32, connections=16, idle_seconds=30):
        self.maximum, self.connections, self.idle_seconds = maximum, connections, idle_seconds
        self.entries = OrderedDict()
        self.started = False
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(self, app):
        self.started = True
        async def expire_idle():
            while True:
                await asyncio.sleep(min(10, max(.05, self.idle_seconds / 2)))
                async with self.lock:
                    for key, entry in list(self.entries.items()):
                        if not entry["active"] and time.monotonic() - entry["last"] >= self.idle_seconds:
                            del self.entries[key]
                            await entry["transport"].aclose()
        reaper = asyncio.create_task(expire_idle())
        try:
            yield
        finally:
            self.started = False
            reaper.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(reaper, return_exceptions=True)
                for entry in list(self.entries.values()):
                    await entry["transport"].aclose()
                self.entries.clear()

    async def borrow(self, key, isolated=False):
        # An ASGI caller without lifespan gets an isolated transport with explicit ownership.
        if isolated or not self.started:
            return httpx.AsyncHTTPTransport(trust_env=False, retries=0)
        async with self.lock:
            return await self._borrow(key)

    async def _borrow(self, key):
        now = time.monotonic()
        for old_key, entry in list(self.entries.items()):
            changed = old_key[0] == key[0] and old_key != key
            if changed:
                entry["retired"] = True
            if not entry["active"] and (entry["retired"] or now - entry["last"] >= self.idle_seconds):
                del self.entries[old_key]
                with anyio.CancelScope(shield=True):
                    await entry["transport"].aclose()
        entry = self.entries.get(key)
        if entry is None:
            if len(self.entries) >= self.maximum:
                idle = next((k for k, v in self.entries.items() if not v["active"]), None)
                if idle is None:
                    raise httpx.PoolTimeout("upstream_pool_capacity")
                old = self.entries.pop(idle)
                with anyio.CancelScope(shield=True):
                    await old["transport"].aclose()
            entry = {"transport": httpx.AsyncHTTPTransport(trust_env=False, retries=0,
                     limits=httpx.Limits(max_connections=self.connections, max_keepalive_connections=8,
                                         keepalive_expiry=self.idle_seconds)),
                     "active": 0, "last": now, "retired": False}
            self.entries[key] = entry
        if entry["retired"] or entry["active"] >= self.connections:
            raise httpx.PoolTimeout("upstream_connection_capacity")
        entry["active"] += 1
        self.entries.move_to_end(key)
        return BorrowedTransport(self, key, entry)

    async def release(self, key, entry):
        entry["active"] -= 1
        entry["last"] = time.monotonic()
        if not entry["active"] and entry["retired"]:
            if self.entries.get(key) is entry:
                del self.entries[key]
            await entry["transport"].aclose()
