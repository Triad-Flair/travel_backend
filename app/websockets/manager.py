import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self) -> None:
        # user_id -> set of websockets
        self._user_connections: dict[str, set[WebSocket]] = defaultdict(set)
        # group_id -> set of websockets
        self._group_connections: dict[str, set[WebSocket]] = defaultdict(set)
        # conv_id -> set of websockets
        self._conv_connections: dict[str, set[WebSocket]] = defaultdict(set)
        # websocket -> user_id
        self._ws_user: dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, user_id: str) -> None:
        await websocket.accept()
        self._user_connections[user_id].add(websocket)
        self._ws_user[websocket] = user_id
        logger.debug("WebSocket connected: user %s", user_id)

    def disconnect(self, websocket: WebSocket) -> None:
        user_id = self._ws_user.pop(websocket, None)
        if user_id:
            self._user_connections[user_id].discard(websocket)
            for conns in self._group_connections.values():
                conns.discard(websocket)
            for conns in self._conv_connections.values():
                conns.discard(websocket)
        logger.debug("WebSocket disconnected: user %s", user_id)

    def subscribe_group(self, websocket: WebSocket, group_id: str) -> None:
        self._group_connections[group_id].add(websocket)

    def unsubscribe_group(self, websocket: WebSocket, group_id: str) -> None:
        self._group_connections[group_id].discard(websocket)

    def subscribe_conversation(self, websocket: WebSocket, conv_id: str) -> None:
        self._conv_connections[conv_id].add(websocket)

    def unsubscribe_conversation(self, websocket: WebSocket, conv_id: str) -> None:
        self._conv_connections[conv_id].discard(websocket)

    async def broadcast_to_group(self, group_id: str, data: Any) -> None:
        message = json.dumps(data, default=str)
        dead: list[WebSocket] = []
        for ws in list(self._group_connections.get(group_id, set())):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_to_user(self, user_id: str, data: Any) -> None:
        message = json.dumps(data, default=str)
        dead: list[WebSocket] = []
        for ws in list(self._user_connections.get(user_id, set())):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_to_conversation(self, conv_id: str, data: Any) -> None:
        message = json.dumps(data, default=str)
        dead: list[WebSocket] = []
        for ws in list(self._conv_connections.get(conv_id, set())):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = WebSocketManager()
