# boardroom_history.py

import uuid

from datetime import datetime

from app.database import get_db


def create_boardroom_session(
    prompt
):

    session_id = str(
        uuid.uuid4()
    )

    conn = get_db()

    conn.execute(
        """
        INSERT INTO
        boardroom_sessions
        (
            id,
            prompt,
            created_at
        )
        VALUES
        (
            ?, ?, ?
        )
        """,
        (
            session_id,
            prompt,
            datetime.utcnow().isoformat()
        )
    )

    conn.commit()

    conn.close()

    return session_id


def save_report(
    session_id,
    agent_name,
    report
):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO
        boardroom_reports
        (
            session_id,
            agent_name,
            report,
            created_at
        )
        VALUES
        (
            ?, ?, ?, ?
        )
        """,
        (
            session_id,
            agent_name,
            report,
            datetime.utcnow().isoformat()
        )
    )

    conn.commit()

    conn.close()


def get_session_reports(
    session_id
):

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM boardroom_reports
        WHERE session_id = ?
        """,
        (
            session_id,
        )
    ).fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]