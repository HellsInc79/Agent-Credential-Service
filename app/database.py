# database.py
import uuid
import sqlite3
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATABASE_SETTING = Path(os.getenv("DATABASE_PATH", "agents.db")).expanduser()
DB_PATH = str(_DATABASE_SETTING if _DATABASE_SETTING.is_absolute() else (_PROJECT_ROOT / _DATABASE_SETTING).resolve())
# app/persistent_agent_registry.py
def disable_agent(
    agent_id
):

    conn = get_db()

    conn.execute(
        """
        UPDATE
        registered_agents
        SET enabled = 0
        WHERE agent_id = ?
        """,
        (
            agent_id,
        )
    )

    conn.commit()

    conn.close()

    return True

def get_agent(
    agent_id
):

    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM registered_agents
        WHERE agent_id = ?
        """,
        (
            agent_id,
        )
    ).fetchone()

    conn.close()

    if not row:
        return None

    return dict(row)

def get_agents():

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM registered_agents
        """
    ).fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]

def create_agent(
    name,
    role,
    model,
    system_prompt
):

    agent_id = str(
        uuid.uuid4()
    )

    conn = get_db()

    conn.execute(
        """
        INSERT INTO
        registered_agents
        (
            agent_id,
            name,
            role,
            model,
            system_prompt,
            enabled
        )
        VALUES
        (
            ?,?,?,?,?,1
        )
        """,
        (
            agent_id,
            name,
            role,
            model,
            system_prompt
        )
    )

    conn.commit()

    conn.close()

    return agent_id

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():

    conn = get_db()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            model TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            permissions TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            key_hash TEXT NOT NULL UNIQUE,
            token_limit INTEGER NOT NULL,
            tokens_issued INTEGER NOT NULL DEFAULT 0,
            revoked INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            memory TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS boardroom_sessions (
            id TEXT PRIMARY KEY,
            prompt TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS boardroom_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            report TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS registered_agents (

            agent_id TEXT PRIMARY KEY,

            name TEXT NOT NULL,

            role TEXT NOT NULL,

            model TEXT NOT NULL,

            system_prompt TEXT NOT NULL,

            enabled INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            agent_id TEXT,
            role TEXT,
            content TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS room_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_name TEXT NOT NULL,
            sender TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        """
    )

    conn.commit()
    conn.close()
