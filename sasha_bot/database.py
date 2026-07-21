"""
database.py — SQLite-хранилище для бота ТЕО.
Все таблицы с префиксом teo_ чтобы не конфликтовать с zarik_bot.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Optional

DB_PATH = os.getenv("TEO_DB_PATH", "teo.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS teo_users (
                user_id         INTEGER PRIMARY KEY,
                username        TEXT    DEFAULT '',
                first_name      TEXT    DEFAULT '',
                gender          TEXT    DEFAULT 'female',
                preferred_name  TEXT    DEFAULT '',
                preferred_time  TEXT    DEFAULT '09:00',
                timezone        TEXT    DEFAULT 'Europe/Moscow',
                onboarding_step TEXT    DEFAULT 'start',
                last_active     DATETIME,
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS teo_goals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                area        TEXT    DEFAULT '',
                goal_text   TEXT    NOT NULL,
                true_goal   TEXT    DEFAULT '',
                active      INTEGER DEFAULT 1,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS teo_tasks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                goal_id         INTEGER,
                task_text       TEXT    NOT NULL,
                scheduled_date  TEXT    NOT NULL,
                completed       INTEGER DEFAULT 0,
                completed_at    DATETIME,
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS teo_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                role        TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS teo_memory (
                user_id             INTEGER PRIMARY KEY,
                permanent_context   TEXT DEFAULT '',
                weekly_summary      TEXT DEFAULT '',
                updated_at          DATETIME
            );
        """)


# ── Users ─────────────────────────────────────────────────────────────────────

def upsert_user(user_id: int, username: str, first_name: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO teo_users (user_id, username, first_name, last_active)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                username     = excluded.username,
                first_name   = excluded.first_name,
                last_active  = CURRENT_TIMESTAMP
        """, (user_id, username, first_name))


def get_user(user_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM teo_users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def set_gender(user_id: int, gender: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE teo_users SET gender = ? WHERE user_id = ?",
            (gender, user_id),
        )


def set_preferred_name(user_id: int, name: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE teo_users SET preferred_name = ? WHERE user_id = ?",
            (name, user_id),
        )


def set_onboarding_step(user_id: int, step: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE teo_users SET onboarding_step = ? WHERE user_id = ?",
            (step, user_id),
        )


def set_time_and_timezone(user_id: int, preferred_time: str, timezone: str):
    with get_conn() as conn:
        conn.execute(
            """UPDATE teo_users
               SET preferred_time = ?, timezone = ?, onboarding_step = 'complete'
               WHERE user_id = ?""",
            (preferred_time, timezone, user_id),
        )


def touch_last_active(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE teo_users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?",
            (user_id,),
        )


# ── Goals ──────────────────────────────────────────────────────────────────────

def save_goals(user_id: int, goals: list[dict]):
    """Деактивирует старые цели и сохраняет новые."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE teo_goals SET active = 0 WHERE user_id = ?", (user_id,)
        )
        for goal in goals:
            conn.execute(
                """INSERT INTO teo_goals (user_id, area, goal_text, true_goal)
                   VALUES (?, ?, ?, ?)""",
                (
                    user_id,
                    goal.get("area", ""),
                    goal.get("goal_text", ""),
                    goal.get("true_goal", ""),
                ),
            )


def get_goals(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM teo_goals WHERE user_id = ? AND active = 1 ORDER BY id",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Tasks ──────────────────────────────────────────────────────────────────────

def save_tasks(user_id: int, tasks: list[dict]):
    """
    tasks: список dict с полями task_text, day_offset, goal_index (опц).
    day_offset=0 → сегодня, 1 → завтра и т.д.
    """
    goals = get_goals(user_id)
    goal_ids = [g["id"] for g in goals]

    with get_conn() as conn:
        for task in tasks:
            goal_id = None
            idx = task.get("goal_index", 0)
            if isinstance(idx, int) and 0 <= idx < len(goal_ids):
                goal_id = goal_ids[idx]

            day_offset = task.get("day_offset", 0)
            scheduled = (date.today() + timedelta(days=day_offset)).isoformat()

            conn.execute(
                """INSERT INTO teo_tasks (user_id, goal_id, task_text, scheduled_date)
                   VALUES (?, ?, ?, ?)""",
                (user_id, goal_id, task["task_text"], scheduled),
            )


def get_today_tasks(user_id: int) -> list[dict]:
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM teo_tasks
               WHERE user_id = ? AND scheduled_date = ?
               ORDER BY id""",
            (user_id, today),
        ).fetchall()
    return [dict(r) for r in rows]


def get_week_tasks(user_id: int) -> list[dict]:
    today = date.today()
    week_end = (today + timedelta(days=6)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM teo_tasks
               WHERE user_id = ? AND scheduled_date BETWEEN ? AND ?
               ORDER BY scheduled_date, id""",
            (user_id, today.isoformat(), week_end),
        ).fetchall()
    return [dict(r) for r in rows]


def complete_task(task_id: int):
    with get_conn() as conn:
        conn.execute(
            """UPDATE teo_tasks
               SET completed = 1, completed_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (task_id,),
        )


# ── Messages ───────────────────────────────────────────────────────────────────

def save_message(user_id: int, role: str, content: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO teo_messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        conn.execute(
            "UPDATE teo_users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?",
            (user_id,),
        )


def get_recent_messages(user_id: int, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT role, content FROM teo_messages
               WHERE user_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (user_id, limit),
        ).fetchall()
    # Возвращаем в хронологическом порядке
    return [dict(r) for r in reversed(rows)]


def clear_messages(user_id: int):
    """Для отладки / reset."""
    with get_conn() as conn:
        conn.execute("DELETE FROM teo_messages WHERE user_id = ?", (user_id,))


# ── Memory ─────────────────────────────────────────────────────────────────────

def get_memory(user_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM teo_memory WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def update_memory(user_id: int, permanent_context: str = None, weekly_summary: str = None):
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT user_id FROM teo_memory WHERE user_id = ?", (user_id,)
        ).fetchone()

        if existing:
            if permanent_context is not None:
                conn.execute(
                    "UPDATE teo_memory SET permanent_context = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (permanent_context, user_id),
                )
            if weekly_summary is not None:
                conn.execute(
                    "UPDATE teo_memory SET weekly_summary = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (weekly_summary, user_id),
                )
        else:
            conn.execute(
                """INSERT INTO teo_memory (user_id, permanent_context, weekly_summary, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
                (user_id, permanent_context or "", weekly_summary or ""),
            )
