"""Per-room WebSocket connections for the local Uvicorn server."""

import asyncio
from collections import defaultdict

from fastapi import WebSocket


class RoomConnections:
    def __init__(self):
        self._rooms = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, room_name: str, websocket: WebSocket, subprotocol: str | None = None):
        await websocket.accept(subprotocol=subprotocol)
        async with self._lock:
            self._rooms[room_name].add(websocket)

    async def disconnect(self, room_name: str, websocket: WebSocket):
        async with self._lock:
            connections = self._rooms.get(room_name)
            if connections:
                connections.discard(websocket)
                if not connections:
                    self._rooms.pop(room_name, None)

    async def broadcast(self, room_name: str, message: dict):
        async with self._lock:
            connections = tuple(self._rooms.get(room_name, ()))
        if not connections:
            return
        results = await asyncio.gather(
            *(connection.send_json(message) for connection in connections),
            return_exceptions=True,
        )
        stale = [connection for connection, result in zip(connections, results) if isinstance(result, Exception)]
        for connection in stale:
            await self.disconnect(room_name, connection)


room_connections = RoomConnections()
