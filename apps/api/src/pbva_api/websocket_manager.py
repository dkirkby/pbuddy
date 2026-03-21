"""WebSocket connection manager and event broadcaster."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, project_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[project_id].append(ws)
        logger.debug("WS connected: project=%s total=%d", project_id, len(self._connections[project_id]))

    def disconnect(self, project_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(project_id, [])
        if ws in conns:
            conns.remove(ws)

    async def broadcast(self, project_id: str, message: dict) -> None:
        dead = []
        for ws in list(self._connections.get(project_id, [])):
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(project_id, ws)


manager = ConnectionManager()
