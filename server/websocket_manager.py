# ============================================================
# WEBSOCKET CONNECTION MANAGER
# ============================================================
#
# Replaces the MQTT topic -> device mapping with an explicit
# IMEI -> WebSocket connection registry.
#
# MQTT delivered messages to whatever client was subscribed to a
# topic; with WebSocket the server must track each live connection
# itself, keyed by the IMEI the device sends in its "start" frame.

import asyncio
import logging
import time

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from . import config

logger = logging.getLogger("WS_AI_SERVER")

# Close code used when a device re-connects with an IMEI that is
# still registered (stale/duplicate connection replaced).
CLOSE_DUPLICATE_IMEI = 4009


class WebSocketConnection:
    """One identified (IMEI-bound) Soundbox connection."""

    __slots__ = ("websocket", "imei", "connected_at", "last_seen")

    def __init__(self, websocket: WebSocket, imei: str):
        self.websocket = websocket
        self.imei = imei
        self.connected_at = time.time()
        self.last_seen = time.time()

    def touch(self):
        self.last_seen = time.time()

    @property
    def is_open(self) -> bool:
        return (
            self.websocket.client_state == WebSocketState.CONNECTED
        )


class ConnectionManager:
    """
    Tracks active Soundbox connections by IMEI.

    Handles: connection, identification, duplicate IMEI replacement,
    disconnect/cleanup, and stale (dead) connection removal.
    """

    def __init__(self):
        self.active_connections: dict[str, WebSocketConnection] = {}

    async def register(self, websocket: WebSocket, imei: str) -> WebSocketConnection:
        """
        Bind a WebSocket to an IMEI.

        If the IMEI is already registered (device reconnected after a
        dropped cellular link), the old, stale connection is closed and
        replaced - exactly one live connection per IMEI.
        """

        existing = self.active_connections.get(imei)

        if existing is not None and existing.websocket is not websocket:

            logger.warning(
                "IMEI %s reconnected, replacing stale connection "
                "(connected %.0f sec ago)",
                imei,
                time.time() - existing.connected_at
            )

            try:
                await existing.websocket.close(
                    code=CLOSE_DUPLICATE_IMEI,
                    reason="IMEI reconnected on a new connection"
                )
            except Exception:
                pass

        connection = WebSocketConnection(websocket, imei)
        self.active_connections[imei] = connection

        return connection

    async def disconnect(self, connection: WebSocketConnection):
        """Remove a connection from the registry (idempotent)."""

        imei = connection.imei
        current = self.active_connections.get(imei)

        # Only remove if this connection is still the registered one -
        # a newer reconnect must not be dropped by the old socket's
        # cleanup.
        if current is connection:
            self.active_connections.pop(imei, None)

        logger.info(
            "Unregistered IMEI %s (%d active connection(s))",
            imei,
            len(self.active_connections)
        )

    async def send_json(self, connection: WebSocketConnection, payload: dict):
        """
        Send a JSON control frame; drop the connection if it is dead.

        Note: does NOT touch last_seen - liveness is measured by frames
        RECEIVED from the device (audio, pong, start), not by frames we
        send, otherwise our own heartbeats would keep dead links alive.
        """

        try:
            await connection.websocket.send_json(payload)
            return True
        except (WebSocketDisconnect, RuntimeError) as e:
            logger.warning(
                "Send failed for IMEI %s: %s",
                connection.imei,
                e
            )
            await self.disconnect(connection)
            return False

    async def send_bytes(self, connection: WebSocketConnection, data: bytes):
        """Send one binary frame; drop the connection if it is dead."""

        try:
            await connection.websocket.send_bytes(data)
            return True
        except (WebSocketDisconnect, RuntimeError) as e:
            logger.warning(
                "Binary send failed for IMEI %s: %s",
                connection.imei,
                e
            )
            await self.disconnect(connection)
            return False

    async def send_audio(
        self,
        connection: WebSocketConnection,
        audio_bytes: bytes,
        audio_format: str,
        chunk_size: int = None
    ) -> bool:
        """
        Stream response audio using the protocol:

            {"type": "response_start", "format": ..., "size": ...}
            <binary frame> x N
            {"type": "response_end"}
        """

        chunk_size = chunk_size or config.RESPONSE_CHUNK_SIZE

        start_ok = await self.send_json(connection, {
            "type": "response_start",
            "format": audio_format,
            "size": len(audio_bytes),
        })

        if not start_ok:
            return False

        for offset in range(0, len(audio_bytes), chunk_size):

            ok = await self.send_bytes(
                connection,
                audio_bytes[offset:offset + chunk_size]
            )

            if not ok:
                return False

        return await self.send_json(connection, {"type": "response_end"})

    async def sweep_stale(self, heartbeat_interval: int = None):
        """
        Best-effort liveness sweep: ping every registered connection
        and remove the ones that are gone.
        """

        heartbeat_interval = heartbeat_interval or config.HEARTBEAT_INTERVAL_SECONDS

        for connection in list(self.active_connections.values()):

            if not connection.is_open:
                await self.disconnect(connection)
                continue

            # Idle longer than 3 heartbeat windows with no traffic and
            # no response to our pings -> treat as stale.
            idle = time.time() - connection.last_seen

            if idle > heartbeat_interval * 3:
                logger.warning(
                    "Stale connection for IMEI %s (idle %.0f sec), closing",
                    connection.imei,
                    idle
                )
                try:
                    await connection.websocket.close(code=1001)
                except Exception:
                    pass
                await self.disconnect(connection)
                continue

            ok = await self.send_json(connection, {"type": "ping"})

            if not ok:
                logger.warning(
                    "Heartbeat failed for IMEI %s, connection removed",
                    connection.imei
                )

    async def run_heartbeat(self):
        """Background task: periodic stale-connection sweep."""

        while True:
            await asyncio.sleep(config.HEARTBEAT_INTERVAL_SECONDS)
            try:
                await self.sweep_stale()
            except Exception:
                logger.exception("Heartbeat sweep failed")


manager = ConnectionManager()
