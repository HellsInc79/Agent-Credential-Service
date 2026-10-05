# app/websocket_manager.py

from fastapi import WebSocket

ACTIVE_CONNECTIONS = []


async def connect(
    websocket: WebSocket
):

    await websocket.accept()

    ACTIVE_CONNECTIONS.append(
        websocket
    )


def disconnect(
    websocket: WebSocket
):

    if websocket in ACTIVE_CONNECTIONS:

        ACTIVE_CONNECTIONS.remove(
            websocket
        )


async def broadcast(
    message
):

    stale = []

    for connection in ACTIVE_CONNECTIONS:

        try:

            await connection.send_text(
                message
            )

        except Exception:

            stale.append(
                connection
            )

    for connection in stale:

        disconnect(
            connection
        )

