"""Bounded upstream keepalive without sharing visitor cookie jars."""
from collections import OrderedDict
from contextlib import asynccontextmanager
import asyncio
import logging
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
        self.closing = set()

    @asynccontextmanager
    async def lifespan(self, app):
        self.started = True
        stopping = asyncio.Event()
        async def expire_idle():
            while not stopping.is_set():
                try:
                    await asyncio.wait_for(stopping.wait(), min(10, max(.05, self.idle_seconds / 2)))
                    break
                except asyncio.TimeoutError:
                    pass
                expired = []
                async with self.lock:
                    for key, entry in list(self.entries.items()):
                        if not entry["active"] and time.monotonic() - entry["last"] >= self.idle_seconds:
                            del self.entries[key]
                            expired.append(self._schedule_close(entry["transport"]))
                await self._wait_closed(expired)
        reaper = asyncio.create_task(expire_idle())
        try:
            yield
        finally:
            self.started = False
            stopping.set()
            with anyio.CancelScope(shield=True):
                await reaper
                async with self.lock:
                    for entry in self.entries.values():
                        self._schedule_close(entry["transport"])
                    self.entries.clear()
                await self._wait_closed(list(self.closing))

    async def borrow(self, key, isolated=False):
        # An ASGI caller without lifespan gets an isolated transport with explicit ownership.
        if isolated or not self.started:
            return httpx.AsyncHTTPTransport(trust_env=False, retries=0)
        while True:
            async with self.lock:
                if not self.started:
                    raise httpx.PoolTimeout("upstream_pool_stopping")
                lease, closing = self._borrow(key)
            if lease is not None:
                return lease
            await self._wait_closed(closing)

    def _schedule_close(self, transport):
        # The pool owns detached resources, even if the requesting task is cancelled.
        async def close():
            try:
                await transport.aclose()
            except Exception as exc:
                logging.getLogger(__name__).warning("upstream_transport_close_failed %s", type(exc).__name__)
        task = asyncio.create_task(close())
        self.closing.add(task)
        task.add_done_callback(self.closing.discard)
        return task

    async def _wait_closed(self, tasks):
        if tasks:
            with anyio.CancelScope(shield=True):
                await asyncio.shield(asyncio.gather(*tasks))

    def _borrow(self, key):
        now = time.monotonic()
        closing = []
        for old_key, entry in list(self.entries.items()):
            changed = old_key[0] == key[0] and old_key != key
            if changed:
                entry["retired"] = True
            if not entry["active"] and (entry["retired"] or now - entry["last"] >= self.idle_seconds):
                del self.entries[old_key]
                closing.append(self._schedule_close(entry["transport"]))
        if closing:
            return None, closing
        entry = self.entries.get(key)
        if entry is None:
            # Closing transports still consume capacity until their tasks finish.
            if len(self.entries) + len(self.closing) >= self.maximum:
                idle = next((k for k, v in self.entries.items() if not v["active"]), None)
                if idle is None:
                    raise httpx.PoolTimeout("upstream_pool_capacity")
                old = self.entries.pop(idle)
                return None, [self._schedule_close(old["transport"])]
            entry = {"transport": httpx.AsyncHTTPTransport(trust_env=False, retries=0,
                     limits=httpx.Limits(max_connections=self.connections, max_keepalive_connections=8,
                                         keepalive_expiry=self.idle_seconds)),
                     "active": 0, "last": now, "retired": False}
            self.entries[key] = entry
        if entry["retired"] or entry["active"] >= self.connections:
            raise httpx.PoolTimeout("upstream_connection_capacity")
        entry["active"] += 1
        self.entries.move_to_end(key)
        return BorrowedTransport(self, key, entry), []

    async def release(self, key, entry):
        entry["active"] -= 1
        entry["last"] = time.monotonic()
        if not entry["active"] and entry["retired"]:
            if self.entries.get(key) is entry:
                del self.entries[key]
            await self._wait_closed([self._schedule_close(entry["transport"])])
