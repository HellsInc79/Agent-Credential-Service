# conversation_manager.py

import uuid
from datetime import datetime
from app.database import get_db


def create_conversation(title="New Conversation"):

    conversation_id = str(uuid.uuid4())

    conn = get_db()

    conn.execute(
        """
        INSERT INTO conversations
        (
            id,
            title,
            created_at
        )
        VALUES
        (
            ?, ?, ?
        )
        """,
        (
            conversation_id,
            title,
            datetime.utcnow().isoformat()
        )
    )

    conn.commit()
    conn.close()

    return conversation_id


def add_message(
    conversation_id,
    agent_id,
    role,
    content
):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO messages
        (
            conversation_id,
            agent_id,
            role,
            content,
            created_at
        )
        VALUES
        (
            ?, ?, ?, ?, ?
        )
        """,
        (
            conversation_id,
            agent_id,
            role,
            content,
            datetime.utcnow().isoformat()
        )
    )

    conn.commit()
    conn.close()


def get_conversation_messages(
    conversation_id
):

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM messages
        WHERE conversation_id = ?
        ORDER BY created_at
        """,
        (conversation_id,)
    ).fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]
