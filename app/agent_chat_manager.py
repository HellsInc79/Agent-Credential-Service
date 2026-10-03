"""Room chat storage and boardroom orchestration helpers."""

from datetime import datetime, timezone

from app.database import get_db


def _ensure_room_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS room_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_name TEXT NOT NULL,
            sender TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )"""
    )


def create_room(room_name):
    room_name = (room_name or "").strip()
    if not room_name:
        raise ValueError("Room name cannot be empty")
    conn = get_db()
    try:
        _ensure_room_table(conn)
        conn.commit()
    finally:
        conn.close()
    return {"room_name": room_name, "messages": get_messages(room_name)}


def send_message(room_name, sender, content):
    room_name = (room_name or "").strip()
    sender = (sender or "").strip() or "User"
    content = (content or "").strip()
    if not room_name:
        raise ValueError("Room name cannot be empty")
    if not content:
        raise ValueError("Message cannot be empty")
    conn = get_db()
    try:
        _ensure_room_table(conn)
        conn.execute(
            "INSERT INTO room_messages (room_name, sender, content, timestamp) VALUES (?, ?, ?, ?)",
            (room_name, sender, content, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def get_messages(room_name):
    conn = get_db()
    try:
        _ensure_room_table(conn)
        rows = conn.execute(
            "SELECT sender, content, timestamp FROM room_messages WHERE room_name = ? ORDER BY id",
            (room_name,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def boardroom_discussion(room_name, prompt):
    """Record a user prompt, each specialist response, and the final answer."""
    from app.room_manager import run_boardroom

    send_message(room_name, "You", prompt)
    result = run_boardroom(
        prompt,
        on_agent_reply=lambda agent, content: send_message(room_name, agent, content),
    )
    return {"room_name": room_name, "messages": get_messages(room_name), "result": result}
