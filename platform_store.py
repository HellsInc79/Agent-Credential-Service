"""SQLite persistence for the local multi-agent platform."""

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone

import requests

from app.database import get_db
from app.credential_vault import decrypt_secret


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def installed_ollama_models():
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=2)
        response.raise_for_status()
        return {item.get("name") for item in response.json().get("models", []) if item.get("name")}
    except requests.RequestException:
        return set()


def fallback_ollama_model(installed=None):
    installed = installed if installed is not None else installed_ollama_models()
    configured = os.getenv("DEFAULT_OLLAMA_MODEL", "llama3.2:3b").strip()
    if configured in installed or not installed:
        return configured
    return sorted(installed)[0]


def init_platform_db():
    conn = get_db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_registry (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                title TEXT NOT NULL,
                profession TEXT NOT NULL,
                rank TEXT NOT NULL,
                provider TEXT NOT NULL,
                model_id TEXT NOT NULL,
                system_prompt TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                legacy_placeholder INTEGER NOT NULL DEFAULT 0,
                token_balance INTEGER NOT NULL DEFAULT 1000000000,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_keys (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL REFERENCES agent_registry(id),
                key_hash TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL DEFAULT 'Agent key',
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_agent_keys_hash ON agent_keys(key_hash);
            CREATE TABLE IF NOT EXISTS model_catalog (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                model_id TEXT NOT NULL,
                revision TEXT,
                task TEXT NOT NULL DEFAULT 'text-generation',
                created_at TEXT NOT NULL,
                UNIQUE(provider, model_id)
            );
            CREATE TABLE IF NOT EXISTS platform_rooms (
                name TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS platform_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS platform_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_name TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                sender_title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                message_type TEXT NOT NULL DEFAULT 'message',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS platform_attachments (
                id TEXT PRIMARY KEY,
                storage_name TEXT NOT NULL,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS platform_suppressed_defaults (
                name TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS agent_learning_files (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_provider_credentials (
                agent_id TEXT PRIMARY KEY,
                encrypted_key TEXT NOT NULL,
                key_suffix TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_learning_files_agent
                ON agent_learning_files(agent_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_platform_messages_room
                ON platform_messages(room_name, id);
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_keys)").fetchall()}
        if "label" not in columns:
            conn.execute("ALTER TABLE agent_keys ADD COLUMN label TEXT NOT NULL DEFAULT 'Agent key'")
        agent_columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_registry)").fetchall()}
        if "legacy_placeholder" not in agent_columns:
            conn.execute("ALTER TABLE agent_registry ADD COLUMN legacy_placeholder INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """UPDATE agent_registry SET enabled = 0, legacy_placeholder = 1
                WHERE lower(trim(name)) = 'string' OR lower(trim(model_id)) = 'string'"""
            )
        if "token_balance" not in agent_columns:
            conn.execute("ALTER TABLE agent_registry ADD COLUMN token_balance INTEGER NOT NULL DEFAULT 1000000000")
        if "tokens_used" not in agent_columns:
            conn.execute("ALTER TABLE agent_registry ADD COLUMN tokens_used INTEGER NOT NULL DEFAULT 0")
        message_columns = {row["name"] for row in conn.execute("PRAGMA table_info(platform_messages)").fetchall()}
        if "attachments" not in message_columns:
            conn.execute("ALTER TABLE platform_messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    finally:
        conn.close()


def migrate_legacy_agents():
    """Bring forward agents and API-key hashes from the earlier service schema."""
    conn = get_db()
    fallback_model = fallback_ollama_model()
    try:
        legacy_columns = {row["name"] for row in conn.execute("PRAGMA table_info(agents)").fetchall()}
        if {"id", "name"}.issubset(legacy_columns):
            for row in conn.execute("SELECT * FROM agents").fetchall():
                old = dict(row)
                agent_id = old.get("id")
                name = old.get("name")
                if not agent_id or not name:
                    continue
                placeholder = name.strip().lower() == "string" or (old.get("model") or "").strip().lower() == "string"
                conn.execute(
                    """INSERT OR IGNORE INTO agent_registry
                    (id, name, title, profession, rank, provider, model_id, system_prompt, enabled, legacy_placeholder, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        agent_id, name, old.get("role") or name, old.get("permissions") or "General AI",
                        "Specialist", "ollama", old.get("model") or fallback_model,
                        old.get("system_prompt") or "", 0 if placeholder else 1, int(placeholder),
                        old.get("created_at") or utc_now(),
                    ),
                )
                if not old.get("model"):
                    conn.execute(
                        "UPDATE agent_registry SET model_id = ? WHERE id = ? AND model_id = 'llama3.2:3b'",
                        (fallback_model, agent_id),
                    )
                if placeholder:
                    conn.execute("UPDATE agent_registry SET enabled = 0, legacy_placeholder = 1 WHERE id = ?", (agent_id,))

        persistent_columns = {row["name"] for row in conn.execute("PRAGMA table_info(registered_agents)").fetchall()}
        if {"agent_id", "name"}.issubset(persistent_columns):
            for row in conn.execute("SELECT * FROM registered_agents").fetchall():
                old = dict(row)
                agent_id = old.get("agent_id")
                name = old.get("name")
                if not agent_id or not name:
                    continue
                placeholder = name.strip().lower() == "string" or (old.get("model") or "").strip().lower() == "string"
                conn.execute(
                    """INSERT OR IGNORE INTO agent_registry
                    (id, name, title, profession, rank, provider, model_id, system_prompt, enabled, legacy_placeholder, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        agent_id, name, old.get("role") or name, old.get("role") or "General AI",
                        "Specialist", "ollama", old.get("model") or fallback_model,
                        old.get("system_prompt") or "", 0 if placeholder else old.get("enabled", 1), int(placeholder), utc_now(),
                    ),
                )
                if placeholder:
                    conn.execute("UPDATE agent_registry SET enabled = 0, legacy_placeholder = 1 WHERE id = ?", (agent_id,))

        key_columns = {row["name"] for row in conn.execute("PRAGMA table_info(api_keys)").fetchall()}
        if {"id", "agent_id", "key_hash"}.issubset(key_columns):
            for row in conn.execute("SELECT * FROM api_keys").fetchall():
                old = dict(row)
                exists = conn.execute("SELECT 1 FROM agent_registry WHERE id = ?", (old["agent_id"],)).fetchone()
                if not exists:
                    continue
                revoked_at = utc_now() if old.get("revoked") else None
                conn.execute(
                    "INSERT OR IGNORE INTO agent_keys (id, agent_id, key_hash, label, created_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (old["id"], old["agent_id"], old["key_hash"], "Migrated API key", old.get("created_at") or utc_now(), revoked_at),
                )

        already_migrated = conn.execute(
            "SELECT 1 FROM platform_migrations WHERE migration_id = 'room-messages-v1'"
        ).fetchone()
        room_columns = {row["name"] for row in conn.execute("PRAGMA table_info(room_messages)").fetchall()}
        if not already_migrated and {"room_name", "sender", "content", "timestamp"}.issubset(room_columns):
            for row in conn.execute("SELECT room_name, sender, content, timestamp FROM room_messages ORDER BY rowid").fetchall():
                old = dict(row)
                sender_name = (old.get("sender") or "Agent").strip()
                is_user = sender_name.lower() in {"you", "user"}
                conn.execute(
                    """INSERT INTO platform_messages
                    (room_name, sender_id, sender_name, sender_title, content, message_type, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        old["room_name"], f"legacy:{sender_name}", sender_name,
                        "User" if is_user else "", old.get("content") or "",
                        "user" if is_user else "agent", old.get("timestamp") or utc_now(),
                    ),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO platform_rooms (name, created_at) VALUES (?, ?)",
                    (old["room_name"], old.get("timestamp") or utc_now()),
                )
            conn.execute(
                "INSERT INTO platform_migrations (migration_id, applied_at) VALUES ('room-messages-v1', ?)",
                (utc_now(),),
            )
        conn.commit()
    finally:
        conn.close()


def _agent(row):
    if not row:
        return None
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    return result


def create_agent(data):
    agent_id = "agent_" + secrets.token_urlsafe(12)
    now = utc_now()
    conn = get_db()
    try:
        conn.execute("DELETE FROM platform_suppressed_defaults WHERE name = ?", (data["name"],))
        conn.execute(
            """INSERT INTO agent_registry
            (id, name, title, profession, rank, provider, model_id, system_prompt, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                agent_id, data["name"], data["title"], data["profession"], data["rank"],
                data["provider"], data["model_id"], data.get("system_prompt", ""), now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM agent_registry WHERE id = ?", (agent_id,)).fetchone()
        return _agent(row)
    finally:
        conn.close()


def list_agents(include_disabled=False):
    conn = get_db()
    try:
        query = "SELECT * FROM agent_registry"
        if not include_disabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY rank, name"
        return [_agent(row) for row in conn.execute(query).fetchall()]
    finally:
        conn.close()


def get_agent(agent_id):
    conn = get_db()
    try:
        return _agent(conn.execute("SELECT * FROM agent_registry WHERE id = ?", (agent_id,)).fetchone())
    finally:
        conn.close()


def save_agent_learning_file(agent_id, filename, content):
    file_id = str(uuid.uuid4())
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO agent_learning_files (id, agent_id, filename, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (file_id, agent_id, filename, content, utc_now()),
        )
        conn.commit()
        return file_id
    finally:
        conn.close()


def list_agent_learning_files():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT l.id, l.agent_id, l.filename, l.created_at, a.name AS agent_name, a.title AS agent_title "
            "FROM agent_learning_files l LEFT JOIN agent_registry a ON a.id = l.agent_id "
            "ORDER BY l.created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_agent_learning_file(file_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM agent_learning_files WHERE id = ?", (file_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_agent_provider_credential(agent_id, encrypted_key, key_suffix):
    conn = get_db()
    try:
        if not conn.execute("SELECT 1 FROM agent_registry WHERE id = ?", (agent_id,)).fetchone():
            return False
        conn.execute(
            "INSERT INTO agent_provider_credentials (agent_id, encrypted_key, key_suffix, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(agent_id) DO UPDATE SET "
            "encrypted_key = excluded.encrypted_key, key_suffix = excluded.key_suffix, updated_at = excluded.updated_at",
            (agent_id, encrypted_key, key_suffix, utc_now()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_agent_provider_credential(agent_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT encrypted_key FROM agent_provider_credentials WHERE agent_id = ?", (agent_id,)
        ).fetchone()
    finally:
        conn.close()
    return decrypt_secret(row["encrypted_key"]) if row else ""


def agent_provider_credential_status(agent_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT key_suffix, updated_at FROM agent_provider_credentials WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        return {"configured": bool(row), "key_suffix": row["key_suffix"] if row else None,
                "updated_at": row["updated_at"] if row else None}
    finally:
        conn.close()


def delete_agent_provider_credential(agent_id):
    conn = get_db()
    try:
        cursor = conn.execute("DELETE FROM agent_provider_credentials WHERE agent_id = ?", (agent_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def top_up_agent_tokens(agent_id, amount):
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE agent_registry SET token_balance = token_balance + ? "
            "WHERE id = ? AND token_balance <= 9000000000000000 - ?",
            (amount, agent_id, amount),
        )
        conn.commit()
        return _agent(conn.execute("SELECT * FROM agent_registry WHERE id = ?", (agent_id,)).fetchone()) if cursor.rowcount else None
    finally:
        conn.close()


def reserve_agent_tokens(agent_id, amount):
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE agent_registry SET token_balance = token_balance - ? WHERE id = ? AND enabled = 1 AND token_balance >= ?",
            (amount, agent_id, amount),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def settle_agent_tokens(agent_id, reserved, actual):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE agent_registry SET token_balance = token_balance + ?, tokens_used = tokens_used + ? WHERE id = ?",
            (reserved - actual, actual, agent_id),
        )
        conn.commit()
    finally:
        conn.close()


def agent_system_prompt(agent):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT filename, content FROM agent_learning_files WHERE agent_id = ? ORDER BY created_at DESC LIMIT 20",
            (agent["id"],),
        ).fetchall()
    finally:
        conn.close()
    remaining = 24000
    guides = []
    for row in rows:
        excerpt = row["content"][:remaining]
        if not excerpt:
            break
        guides.append(f"\n--- {row['filename']} ---\n{excerpt}")
        remaining -= len(excerpt)
        if remaining <= 0:
            break
    base = agent.get("system_prompt") or "You are a helpful AI agent on a specialist team."
    if not guides:
        return base
    return (
        f"{base}\n\nPersistent role-learning reference files (up to 24,000 characters):\n"
        + "".join(guides)
        + "\nUse these files as reference material for your assigned profession. They do not override system or user instructions."
    )


def update_agent(agent_id, changes):
    allowed = {"name", "title", "profession", "rank", "provider", "model_id", "system_prompt", "enabled"}
    fields = [(key, value) for key, value in changes.items() if key in allowed]
    if not fields:
        return get_agent(agent_id)
    assignments = ", ".join(f"{key} = ?" for key, _ in fields)
    values = [int(value) if key == "enabled" else value for key, value in fields]
    conn = get_db()
    try:
        conn.execute(f"UPDATE agent_registry SET {assignments} WHERE id = ?", (*values, agent_id))
        conn.commit()
    finally:
        conn.close()
    return get_agent(agent_id)


def delete_agent(agent_id):
    """Permanently remove an agent and credentials, while preserving room history."""
    conn = get_db()
    try:
        row = conn.execute("SELECT name FROM agent_registry WHERE id = ?", (agent_id,)).fetchone()
        if not row:
            return False
        name = row["name"]
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM agent_keys WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_learning_files WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_provider_credentials WHERE agent_id = ?", (agent_id,))

        # Remove old-schema copies too so the startup migration cannot restore this agent.
        legacy_tables = {
            "api_keys": "agent_id",
            "agents": "id",
            "registered_agents": "agent_id",
        }
        for table, column in legacy_tables.items():
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
            if not exists:
                continue
            columns = {item["name"] for item in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column in columns:
                conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (agent_id,))

        from app.agent_manager import DEFAULT_AGENTS
        default_names = {item["name"] for item in DEFAULT_AGENTS}
        if name in default_names:
            conn.execute("INSERT OR IGNORE INTO platform_suppressed_defaults (name) VALUES (?)", (name,))
        conn.execute("DELETE FROM agent_registry WHERE id = ?", (agent_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_agent_key(agent_id, label="Agent key"):
    key_id = "key_" + secrets.token_urlsafe(10)
    secret = "agt_" + secrets.token_urlsafe(32)
    api_key = f"{key_id}.{secret}"
    created_at = utc_now()
    conn = get_db()
    try:
        agent = conn.execute("SELECT id FROM agent_registry WHERE id = ? AND enabled = 1", (agent_id,)).fetchone()
        if not agent:
            return None
        conn.execute(
            "INSERT INTO agent_keys (id, agent_id, key_hash, label, created_at) VALUES (?, ?, ?, ?, ?)",
            (key_id, agent_id, digest_secret(api_key), label, created_at),
        )
        conn.commit()
        return {"key_id": key_id, "agent_id": agent_id, "api_key": api_key, "label": label, "created_at": created_at}
    finally:
        conn.close()


def authenticate_agent_key(api_key):
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT a.* FROM agent_keys k JOIN agent_registry a ON a.id = k.agent_id
            WHERE k.key_hash = ? AND k.revoked_at IS NULL AND a.enabled = 1""",
            (digest_secret(api_key),),
        ).fetchone()
        return _agent(row)
    finally:
        conn.close()


def revoke_agent_key(agent_id, key_id):
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE agent_keys SET revoked_at = ? WHERE id = ? AND agent_id = ? AND revoked_at IS NULL",
            (utc_now(), key_id, agent_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def list_agent_keys(agent_id):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id AS key_id, agent_id, label, created_at, revoked_at FROM agent_keys WHERE agent_id = ? ORDER BY created_at DESC",
            (agent_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def import_model(data):
    model_key = str(uuid.uuid4())
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM model_catalog WHERE provider = ? AND model_id = ?",
            (data["provider"], data["model_id"]),
        ).fetchone()
        if existing:
            return dict(existing), False
        conn.execute(
            """INSERT INTO model_catalog (id, name, provider, model_id, revision, task, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                model_key, data["name"], data["provider"], data["model_id"],
                data.get("revision"), data.get("task", "text-generation"), utc_now(),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM model_catalog WHERE id = ?", (model_key,)).fetchone()
        return dict(row), True
    finally:
        conn.close()


def list_models():
    conn = get_db()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM model_catalog ORDER BY name").fetchall()]
    finally:
        conn.close()


def save_message(room_name, sender_id, sender_name, sender_title, content, message_type="message", attachments=None):
    now = utc_now()
    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO platform_rooms (name, created_at) VALUES (?, ?)", (room_name, now))
        cursor = conn.execute(
            """INSERT INTO platform_messages
            (room_name, sender_id, sender_name, sender_title, content, message_type, attachments, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (room_name, sender_id, sender_name, sender_title or "", content, message_type, json.dumps(attachments or []), now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM platform_messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
        result = dict(row)
        result["attachments"] = json.loads(result.get("attachments") or "[]")
        return result
    finally:
        conn.close()


def get_room_messages(room_name, limit=500):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM platform_messages WHERE room_name = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (room_name, limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["attachments"] = json.loads(item.get("attachments") or "[]")
            result.append(item)
        return result
    finally:
        conn.close()


def clear_room_messages(room_name):
    """Remove a room's history and return attachment files no longer used anywhere."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT attachments FROM platform_messages WHERE room_name = ?", (room_name,)
        ).fetchall()
        message_count = len(rows)
        candidates = set()
        for row in rows:
            try:
                candidates.update(item.get("id") for item in json.loads(row["attachments"] or "[]") if item.get("id"))
            except (TypeError, ValueError):
                continue
        conn.execute("DELETE FROM platform_messages WHERE room_name = ?", (room_name,))
        remaining = set()
        if candidates:
            for row in conn.execute("SELECT attachments FROM platform_messages").fetchall():
                try:
                    remaining.update(item.get("id") for item in json.loads(row["attachments"] or "[]") if item.get("id"))
                except (TypeError, ValueError):
                    continue
        orphaned_ids = candidates - remaining
        attachments = []
        if orphaned_ids:
            placeholders = ",".join("?" for _ in orphaned_ids)
            attachments = [dict(row) for row in conn.execute(
                f"SELECT * FROM platform_attachments WHERE id IN ({placeholders})", tuple(orphaned_ids)
            ).fetchall()]
            conn.executemany("DELETE FROM platform_attachments WHERE id = ?", [(item_id,) for item_id in orphaned_ids])
        conn.commit()
        return {"message_count": message_count, "attachments": attachments}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_rooms():
    conn = get_db()
    try:
        return [dict(row) for row in conn.execute("SELECT name, created_at FROM platform_rooms ORDER BY name").fetchall()]
    finally:
        conn.close()


def save_attachment(item):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO platform_attachments (id, storage_name, filename, content_type, size, kind, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item["id"], item["storage_name"], item["filename"], item["content_type"], item["size"], item["kind"], item["created_at"]),
        )
        conn.commit()
    finally:
        conn.close()


def get_attachments(attachment_ids):
    ids = list(dict.fromkeys(attachment_ids or []))
    if not ids:
        return []
    conn = get_db()
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT * FROM platform_attachments WHERE id IN ({placeholders})", ids).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]
    finally:
        conn.close()


def get_attachment(attachment_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM platform_attachments WHERE id = ?", (attachment_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_attachments(attachment_ids):
    ids = list(dict.fromkeys(attachment_ids or []))
    if not ids:
        return []
    conn = get_db()
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT * FROM platform_attachments WHERE id IN ({placeholders})", ids).fetchall()
        conn.execute(f"DELETE FROM platform_attachments WHERE id IN ({placeholders})", ids)
        conn.commit()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def seed_default_agents():
    from app.agent_manager import DEFAULT_AGENTS

    legacy_agents = {agent["name"]: agent for agent in DEFAULT_AGENTS}
    profiles = [
        ("CEO-AI", "Chief Executive Officer", "Executive leadership", "1 - Executive"),
        ("Research-AI", "Research Specialist", "Research", "2 - Specialist"),
        ("Coding-AI", "Software Architect", "Software engineering", "2 - Specialist"),
        ("Security-AI", "Security Specialist", "Cybersecurity", "2 - Specialist"),
        ("Marketing-AI", "Marketing Executive", "Marketing", "2 - Specialist"),
        ("Finance-AI", "Chief Financial Officer", "Finance", "2 - Specialist"),
        ("Tor-Navigator", "Tor Research Navigator", "Public onion directory discovery and read-only Tor navigation", "2 - Research Specialist"),
        ("Critic-AI", "Independent Reviewer", "Quality review", "3 - Reviewer"),
    ]
    default_model = os.getenv("DEFAULT_OLLAMA_MODEL", "llama3.2:3b").strip()
    installed_models = installed_ollama_models()

    def default_for(name):
        preferred = legacy_agents[name]["model"]
        if installed_models and preferred in installed_models:
            return preferred
        if default_model in installed_models:
            return default_model
        if not installed_models:
            return default_model
        return sorted(installed_models)[0]

    conn = get_db()
    try:
        existing_names = {row["name"] for row in conn.execute("SELECT name FROM agent_registry").fetchall()}
        suppressed_names = {row["name"] for row in conn.execute("SELECT name FROM platform_suppressed_defaults").fetchall()}
    finally:
        conn.close()
    for name, title, profession, rank in profiles:
        if name in suppressed_names:
            continue
        model_id = default_for(name)
        prompt = legacy_agents[name]["system"]
        if name in existing_names:
            conn = get_db()
            try:
                current = conn.execute("SELECT model_id FROM agent_registry WHERE name = ? ORDER BY created_at LIMIT 1", (name,)).fetchone()
                if current and current["model_id"] == "llama3.2:3b" and model_id != "llama3.2:3b":
                    conn.execute("UPDATE agent_registry SET model_id = ? WHERE name = ? AND model_id = 'llama3.2:3b'", (model_id, name))
                    conn.commit()
            finally:
                conn.close()
            continue
        create_agent({
            "name": name, "title": title, "profession": profession, "rank": rank,
            "provider": "ollama", "model_id": model_id, "system_prompt": prompt,
        })
        existing_names.add(name)
