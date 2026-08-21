"""WebSocket manager for real-time updates."""

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

from app.events import dump_ws_event

logger = logging.getLogger(__name__)

# Timeout for individual WebSocket send operations (seconds)
# Prevents a slow client from blocking broadcasts to other clients
SEND_TIMEOUT_SECONDS = 5.0


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
        """Broadcast an event to all connected clients.

        Uses a copy-then-send pattern to avoid holding the lock during I/O:
        1. Copy connection list while holding lock
        2. Release lock before sending
        3. Send to all clients concurrently with timeout
        4. Re-acquire lock to clean up disconnected clients
        """
        message = dump_ws_event(event_type, data)

        # Copy connection list under lock to avoid holding lock during I/O
        async with self._lock:
            connections = list(self.active_connections)

        if not connections:
            return

        # Send to all clients concurrently, collect failures
        disconnected: list[WebSocket] = []

        async def send_to_client(connection: WebSocket) -> None:
            try:
                # Timeout prevents blocking on slow/unresponsive clients
                await asyncio.wait_for(connection.send_text(message), timeout=SEND_TIMEOUT_SECONDS)
            except TimeoutError:
                logger.debug("Timeout sending to WebSocket client, marking disconnected")
                disconnected.append(connection)
            except Exception as e:
                logger.debug("Failed to send to client: %s", e)
                disconnected.append(connection)

        # Send to all clients concurrently
        await asyncio.gather(*[send_to_client(conn) for conn in connections])

        # Clean up disconnected clients (re-acquire lock)
        if disconnected:
            async with self._lock:
                for conn in disconnected:
                    if conn in self.active_connections:
                        self.active_connections.remove(conn)
            logger.debug("Removed %d disconnected WebSocket clients", len(disconnected))

    async def send_personal(self, websocket: WebSocket, event_type: str, data: Any) -> None:
        """Send an event to a specific client."""
        message = dump_ws_event(event_type, data)
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.debug("Failed to send to client: %s", e)


# Global instance
ws_manager = WebSocketManager()


def broadcast_event(event_type: str, data: dict, *, realtime: bool = True) -> None:
    """Schedule a broadcast without blocking.

    Convenience function that creates an asyncio task to broadcast
    an event to all connected WebSocket clients and forward to fanout modules.

    Args:
        event_type: Event type string (e.g. "message", "raw_packet")
        data: Event payload dict
        realtime: If False, skip fanout dispatch (used for historical decryption)
    """
    asyncio.create_task(ws_manager.broadcast(event_type, data))

    if realtime:
        from app.bots.engine import bot_engine
        from app.fanout.manager import fanout_manager

        if event_type == "message":
            asyncio.create_task(fanout_manager.broadcast_message(data))
            bot_engine.handle_message_event(data)

            from app.push.manager import push_manager

            asyncio.create_task(push_manager.dispatch_message(data))
        elif event_type == "raw_packet":
            asyncio.create_task(fanout_manager.broadcast_raw(data))
        elif event_type == "contact":
            asyncio.create_task(fanout_manager.broadcast_contact(data))
            bot_engine.handle_contact_event(data)


def broadcast_error(message: str, details: str | None = None) -> None:
    """Broadcast an error notification to all connected clients.

    This appears as a toast notification in the frontend.
    """
    data = {"message": message}
    if details:
        data["details"] = details
    asyncio.create_task(ws_manager.broadcast("error", data))


def broadcast_success(message: str, details: str | None = None) -> None:
    """Broadcast a success notification to all connected clients.

    This appears as a toast notification in the frontend.
    """
    data = {"message": message}
    if details:
        data["details"] = details
    asyncio.create_task(ws_manager.broadcast("success", data))


def broadcast_health(radio_connected: bool, connection_info: str | None = None) -> None:
    """Broadcast health status change to all connected clients."""

    async def _broadcast():
        from app.routers.health import build_health_data

        data = await build_health_data(radio_connected, connection_info)
        await ws_manager.broadcast("health", data)

    asyncio.create_task(_broadcast())
