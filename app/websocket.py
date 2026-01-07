"""WebSocket manager for real-time updates."""

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info("WebSocket client connected (%d total)", len(self.active_connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info("WebSocket client disconnected (%d remaining)", len(self.active_connections))

    async def broadcast(self, event_type: str, data: Any) -> None:
        """Broadcast an event to all connected clients."""
        if not self.active_connections:
            return

        message = json.dumps({"type": event_type, "data": data})

        async with self._lock:
            disconnected = []
            for connection in self.active_connections:
                try:
                    await connection.send_text(message)
                except Exception as e:
                    logger.debug("Failed to send to client: %s", e)
                    disconnected.append(connection)

            # Clean up disconnected clients
            for conn in disconnected:
                if conn in self.active_connections:
                    self.active_connections.remove(conn)

    async def send_personal(self, websocket: WebSocket, event_type: str, data: Any) -> None:
        """Send an event to a specific client."""
        message = json.dumps({"type": event_type, "data": data})
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.debug("Failed to send to client: %s", e)


# Global instance
ws_manager = WebSocketManager()


def broadcast_event(event_type: str, data: dict) -> None:
    """Schedule a broadcast without blocking.

    Convenience function that creates an asyncio task to broadcast
    an event to all connected WebSocket clients.
    """
    asyncio.create_task(ws_manager.broadcast(event_type, data))


def broadcast_error(message: str, details: str | None = None) -> None:
    """Broadcast an error notification to all connected clients.

    This appears as a toast notification in the frontend.
    """
    data = {"message": message}
    if details:
        data["details"] = details
    asyncio.create_task(ws_manager.broadcast("error", data))


def broadcast_health(radio_connected: bool, serial_port: str | None = None) -> None:
    """Broadcast health status change to all connected clients."""
    asyncio.create_task(ws_manager.broadcast("health", {
        "status": "ok" if radio_connected else "degraded",
        "radio_connected": radio_connected,
        "serial_port": serial_port,
    }))
