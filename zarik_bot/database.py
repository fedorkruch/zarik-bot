"""
database.py — все операции с SQLite для бота Зарик
"""
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

# DATA_DIR задаётся через переменную окружения (Railway Volume → /data)
# По умолчанию — рядом с bot.py
_data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
_data_dir.mkdir(parents=True, exist_ok=True)
DB_PATH = _data_dir / "zarik.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Создаёт таблицы при первом запуске и применяет миграции"""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id             INTEGER PRIMARY KEY,
                username            TEXT,
                first_name          TEXT,
                start_date          TEXT NOT NULL DEFAULT '2099-01-01',
                timezone            TEXT DEFAULT 'Europe/Moscow',
                onboarding_complete INTEGER DEFAULT 0,
                participation_fee   INTEGER DEFAULT 0,
                stake_amount        INTEGER DEFAULT 0,
                payment_charge_id   TEXT,
                full_name           TEXT,
                phone               TEXT,
                email               TEXT,
                is_active           INTEGER DEFAULT 1,
                created_at          TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS task_completions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                day_number   INTEGER NOT NULL,
                task_index   INTEGER NOT NULL,
                completed_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                UNIQUE(user_id, day_number, task_index)
            );
        """)

        # Миграции для существующих баз данных
        for migration in [
            "ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'Europe/Moscow'",
            "ALTER TABLE users ADD COLUMN onboarding_complete INTEGER DEFAULT 1",
            "ALTER TABLE users ADD COLUMN participation_fee INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN stake_amount INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN payment_charge_id TEXT",
            "ALTER TABLE users ADD COLUMN full_name TEXT",
            "ALTER TABLE users ADD COLUMN phone TEXT",
            "ALTER TABLE users ADD COLUMN email TEXT",
        ]:
            try:
                conn.execute(migration)
            except Exception:
                pass


# ── Пользователи ──────────────────────────────────────────────

def register_user(user_id: int, username: str, first_name: str):
    """Регистрирует нового участника после оплаты — онбординг ещё не пройден"""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO users
                (user_id, username, first_name, start_date, onboarding_complete)
            VALUES (?, ?, ?, '2099-01-01', 0)
        """, (user_id, username or "", first_name or "Участник"))


def save_payment(
    user_id: int,
    charge_id: str,
    participation_fee: int,
    stake_amount: int,
    full_name: str = "",
    phone: str = "",
    email: str = "",
):
    """Сохраняет данные об оплате и контакты участника"""
    with get_conn() as conn:
        conn.execute("""
            UPDATE users
            SET payment_charge_id = ?,
                participation_fee = ?,
                stake_amount      = ?,
                full_name         = ?,
                phone             = ?,
                email             = ?
            WHERE user_id = ?
        """, (charge_id, participation_fee, stake_amount, full_name, phone, email, user_id))


def get_user(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


def get_all_active_users():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE is_active = 1 AND onboarding_complete = 1"
        ).fetchall()


def get_all_users():
    """Все пользователи включая незавершивших онбординг"""
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()


def get_user_count() -> int:
    """Количество оплативших участников"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE onboarding_complete = 1"
        ).fetchone()
        return row["cnt"] if row else 0


def get_total_stake() -> int:
    """Суммарная ставка всех участников в копейках"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT SUM(stake_amount) as total FROM users WHERE onboarding_complete = 1"
        ).fetchone()
        return row["total"] or 0


def is_program_started(user_id: int) -> bool:
    """Вернёт True если start_date уже наступил (программа идёт)"""
    user = get_user(user_id)
    if not user:
        return False
    try:
        start = date.fromisoformat(user["start_date"])
        return date.today() >= start
    except Exception:
        return False


def set_user_timezone(user_id: int, timezone: str):
    """Сохраняет выбранный часовой пояс"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET timezone = ? WHERE user_id = ?",
            (timezone, user_id)
        )


def complete_onboarding(user_id: int):
    """Завершает онбординг: ставит старт на завтра"""
    from datetime import timedelta
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET onboarding_complete = 1, start_date = ? WHERE user_id = ?",
            (tomorrow, user_id)
        )


def is_onboarding_complete(user_id: int) -> bool:
    user = get_user(user_id)
    if not user:
        return False
    return bool(user["onboarding_complete"])


# ── Прогресс ──────────────────────────────────────────────────

def get_current_day(user_id: int) -> int:
    """Возвращает текущий день программы (1–91)"""
    user = get_user(user_id)
    if not user:
        return 0
    start = date.fromisoformat(user["start_date"])
    delta = (date.today() - start).days + 1
    return min(max(delta, 1), 91)


def get_completed_tasks(user_id: int, day_number: int) -> set:
    """Возвращает индексы выполненных задач за день (0–3)"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT task_index FROM task_completions
            WHERE user_id = ? AND day_number = ?
        """, (user_id, day_number)).fetchall()
        return {row["task_index"] for row in rows}


def complete_task(user_id: int, day_number: int, task_index: int):
    """Отмечает задачу выполненной"""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO task_completions (user_id, day_number, task_index)
            VALUES (?, ?, ?)
        """, (user_id, day_number, task_index))


def is_day_complete(user_id: int, day_number: int) -> bool:
    return len(get_completed_tasks(user_id, day_number)) >= 4


def set_day_for_testing(user_id: int, target_day: int):
    """Сдвигает дату старта для тестирования"""
    from datetime import timedelta
    new_start = date.today() - timedelta(days=target_day - 1)
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET start_date = ? WHERE user_id = ?",
            (new_start.isoformat(), user_id)
        )
        conn.execute(
            "DELETE FROM task_completions WHERE user_id = ? AND day_number >= ?",
            (user_id, target_day)
        )


def get_completed_days_set(user_id: int) -> set:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT day_number FROM task_completions
            WHERE user_id = ?
            GROUP BY day_number
            HAVING COUNT(DISTINCT task_index) >= 4
        """, (user_id,)).fetchall()
        return {row[0] for row in rows}


def get_streak(user_id: int) -> int:
    current_day = get_current_day(user_id)
    completed = get_completed_days_set(user_id)
    streak = 0
    for d in range(current_day, 0, -1):
        if d in completed:
            streak += 1
        else:
            break
    return streak


def get_module_stats(user_id: int) -> list:
    completed = get_completed_days_set(user_id)
    current_day = get_current_day(user_id)

    modules = [
        {"name": "Запуск",          "start": 1,  "end": 21},
        {"name": "Строительство",   "start": 22, "end": 42},
        {"name": "Испытание",       "start": 43, "end": 63},
        {"name": "Трансформация",   "start": 64, "end": 84},
        {"name": "Финал",           "start": 85, "end": 91},
    ]

    result = []
    for i, m in enumerate(modules):
        total = m["end"] - m["start"] + 1
        done = sum(1 for d in range(m["start"], m["end"] + 1) if d in completed)
        started = current_day >= m["start"]
        result.append({
            "num": i + 1,
            "name": m["name"],
            "start": m["start"],
            "end": m["end"],
            "total": total,
            "done": done,
            "started": started,
        })
    return result


def get_stats(user_id: int) -> dict:
    current_day = get_current_day(user_id)
    completed = get_completed_days_set(user_id)
    return {
        "current_day": current_day,
        "days_completed": len(completed),
        "days_remaining": 91 - current_day,
        "streak": get_streak(user_id),
    }
