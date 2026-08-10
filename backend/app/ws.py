from __future__ import annotations

import asyncio
from typing import Set

from fastapi import WebSocket


class WSManager:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        await ws.send_json({"type": "connection", "status": "connected"})

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        async with self._lock:
            clients = list(self._clients)
        dead = []
        for ws in clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    async def client_count(self) -> int:
        async with self._lock:
            return len(self._clients)

    async def status(self) -> str:
        count = await self.client_count()
        return "connected" if count > 0 else "idle"


WS = WSManager()
