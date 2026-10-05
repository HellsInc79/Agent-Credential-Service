# memory_manager.py

from datetime import datetime

from app.database import get_db


def add_memory(
    agent_name,
    memory
):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO agent_memory
        (
            agent_name,
            memory,
            created_at
        )
        VALUES
        (
            ?, ?, ?
        )
        """,
        (
            agent_name,
            memory,
            datetime.utcnow().isoformat()
        )
    )

    conn.commit()

    conn.close()


def get_memories(
    agent_name,
    limit=10
):

    conn = get_db()

    rows = conn.execute(
        """
        SELECT memory
        FROM agent_memory
        WHERE agent_name = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            agent_name,
            limit
        )
    ).fetchall()

    conn.close()

    return [
        row["memory"]
        for row in rows
    ]


def build_memory_context(
    agent_name
):

    memories = get_memories(
        agent_name
    )

    if not memories:

        return ""

    return "\n".join(
        memories
    )