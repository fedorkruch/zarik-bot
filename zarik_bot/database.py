"""
database.py — все операции с SQLite для бота Зарик (77-дневный челлендж)
"""
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

# DATA_DIR задаётся через переменную окружения (Railway Volume → /data)
# По умолчанию — рядом с bot.py
_data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
_data_dir.mkdir(parents=True, exist_ok=True)
DB_PATH = _data_dir / "zarik.db"

TOTAL_DAYS = 77   # Длительность программы
TASKS_PER_DAY = 5 # Количество задач в день


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
                onboarding_step     TEXT DEFAULT 'payment',
                onboarding_complete INTEGER DEFAULT 0,
                participation_fee   INTEGER DEFAULT 0,
                stake_amount        INTEGER DEFAULT 0,
                payment_charge_id   TEXT,
                full_name           TEXT,
                phone               TEXT,
                email               TEXT,
                pushup_start             INTEGER DEFAULT 10,
                squat_start              INTEGER DEFAULT 10,
                abs_start                INTEGER DEFAULT 10,
                is_active                INTEGER DEFAULT 1,
                dropout_warning_sent_at  TEXT DEFAULT NULL,
                created_at               TEXT DEFAULT (datetime('now'))
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

            CREATE TABLE IF NOT EXISTS user_achievements (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                achievement_id TEXT NOT NULL,
                earned_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, achievement_id)
            );
        """)

        # Миграции для существующих баз данных
        migrations = [
            "ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'Europe/Moscow'",
            "ALTER TABLE users ADD COLUMN onboarding_complete INTEGER DEFAULT 1",
            "ALTER TABLE users ADD COLUMN onboarding_step TEXT DEFAULT 'done'",
            "ALTER TABLE users ADD COLUMN participation_fee INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN stake_amount INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN payment_charge_id TEXT",
            "ALTER TABLE users ADD COLUMN full_name TEXT",
            "ALTER TABLE users ADD COLUMN phone TEXT",
            "ALTER TABLE users ADD COLUMN email TEXT",
            "ALTER TABLE users ADD COLUMN pushup_start INTEGER DEFAULT 10",
            "ALTER TABLE users ADD COLUMN squat_start INTEGER DEFAULT 10",
            "ALTER TABLE users ADD COLUMN abs_start INTEGER DEFAULT 10",
            "ALTER TABLE users ADD COLUMN dropout_warning_sent_at TEXT DEFAULT NULL",
        ]
        for migration in migrations:
            try:
                conn.execute(migration)
            except Exception:
                pass


# ── Пользователи ──────────────────────────────────────────────

def register_user(user_id: int, username: str, first_name: str):
    """Регистрирует нового участника — онбординг ещё не пройден"""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO users
                (user_id, username, first_name, start_date,
                 onboarding_step, onboarding_complete)
            VALUES (?, ?, ?, '2099-01-01', 'payment', 0)
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
    """Сохраняет данные об оплате и переводит на следующий шаг онбординга"""
    with get_conn() as conn:
        conn.execute("""
            UPDATE users
            SET payment_charge_id = ?,
                participation_fee = ?,
                stake_amount      = ?,
                full_name         = ?,
                phone             = ?,
                email             = ?,
                onboarding_step   = 'timezone'
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
    """Количество завершивших онбординг участников"""
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


def is_payment_confirmed(user_id: int) -> bool:
    """True если пользователь оплатил участие (payment_charge_id заполнен)."""
    user = get_user(user_id)
    return bool(user and user["payment_charge_id"])


def is_onboarding_complete(user_id: int) -> bool:
    user = get_user(user_id)
    if not user:
        return False
    return bool(user["onboarding_complete"])


# ── Онбординг (пошаговый) ────────────────────────────────────

def get_onboarding_step(user_id: int) -> str:
    """
    Возвращает текущий шаг онбординга:
      'payment' → 'timezone' → 'pushup' → 'squat' → 'abs' → 'done'
    """
    user = get_user(user_id)
    if not user:
        return "payment"
    return user["onboarding_step"] or "payment"


def set_onboarding_step(user_id: int, step: str):
    """Переключает шаг онбординга"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET onboarding_step = ? WHERE user_id = ?",
            (step, user_id)
        )


def set_user_timezone(user_id: int, timezone: str):
    """Сохраняет часовой пояс и переходит к вопросу про отжимания"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET timezone = ?, onboarding_step = 'pushup' WHERE user_id = ?",
            (timezone, user_id)
        )


def save_pushup_start(user_id: int, reps: int):
    """Сохраняет стартовые отжимания"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET pushup_start = ?, onboarding_step = 'squat' WHERE user_id = ?",
            (reps, user_id)
        )


def save_squat_start(user_id: int, reps: int):
    """Сохраняет стартовые приседания"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET squat_start = ?, onboarding_step = 'abs' WHERE user_id = ?",
            (reps, user_id)
        )


def save_abs_start(user_id: int, reps: int):
    """Сохраняет стартовый пресс"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET abs_start = ?, onboarding_step = 'done' WHERE user_id = ?",
            (reps, user_id)
        )


def complete_onboarding(user_id: int):
    """
    Завершает онбординг: ставит старт на завтра.
    Вызывать ПОСЛЕ save_abs_start.
    """
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with get_conn() as conn:
        conn.execute(
            """UPDATE users
               SET onboarding_complete = 1,
                   onboarding_step     = 'done',
                   start_date          = ?
               WHERE user_id = ?""",
            (tomorrow, user_id)
        )


# ── Прогресс ──────────────────────────────────────────────────

def get_current_day(user_id: int) -> int:
    """Возвращает текущий день программы (1–77)"""
    user = get_user(user_id)
    if not user:
        return 0
    start = date.fromisoformat(user["start_date"])
    delta = (date.today() - start).days + 1
    return min(max(delta, 1), TOTAL_DAYS)


def get_completed_tasks(user_id: int, day_number: int) -> set:
    """Возвращает индексы выполненных задач за день (0–4)"""
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
    """День засчитывается если отмечены все 5 задач"""
    return len(get_completed_tasks(user_id, day_number)) >= TASKS_PER_DAY


def get_completed_days_set(user_id: int) -> set:
    """Дни, в которые отмечены все 5 задач"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT day_number FROM task_completions
            WHERE user_id = ?
            GROUP BY day_number
            HAVING COUNT(DISTINCT task_index) >= ?
        """, (user_id, TASKS_PER_DAY)).fetchall()
        return {row[0] for row in rows}


def get_streak(user_id: int) -> int:
    """Текущая серия последовательно выполненных дней"""
    current_day = get_current_day(user_id)
    completed = get_completed_days_set(user_id)
    streak = 0
    for d in range(current_day, 0, -1):
        if d in completed:
            streak += 1
        else:
            break
    return streak


def get_stats(user_id: int) -> dict:
    current_day = get_current_day(user_id)
    completed = get_completed_days_set(user_id)
    return {
        "current_day": current_day,
        "days_completed": len(completed),
        "days_remaining": TOTAL_DAYS - current_day,
        "streak": get_streak(user_id),
    }


def get_completed_tasks_for_days(user_id: int, days: list) -> dict:
    """
    Возвращает {day_number: set_of_task_indices} для указанных дней.
    """
    if not days:
        return {}
    placeholders = ",".join("?" * len(days))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT day_number, task_index FROM task_completions "
            f"WHERE user_id = ? AND day_number IN ({placeholders})",
            [user_id] + list(days)
        ).fetchall()
    result = {d: set() for d in days}
    for row in rows:
        result[row["day_number"]].add(row["task_index"])
    return result


# ── Статистика группы ─────────────────────────────────────────

def get_group_stats() -> dict:
    """
    Возвращает живую статистику группы для еженедельного сообщения.
      total    — всего завершили онбординг
      active   — активны (не выбыли: не было 3+ пропущенных дней подряд)
      dropped  — выбывшие
    """
    with get_conn() as conn:
        users = conn.execute(
            "SELECT user_id FROM users WHERE onboarding_complete = 1 AND is_active = 1"
        ).fetchall()

    total = len(users)
    dropped = 0

    today = date.today()
    for row in users:
        uid = row["user_id"]
        user = get_user(uid)
        if not user:
            continue
        try:
            start = date.fromisoformat(user["start_date"])
        except Exception:
            continue
        days_in = (today - start).days + 1
        if days_in <= 0:
            continue  # ещё не начали

        completed = get_completed_days_set(uid)

        # Считаем максимальную серию пропусков
        max_miss = 0
        cur_miss = 0
        for d in range(1, min(days_in, TOTAL_DAYS) + 1):
            if d not in completed:
                cur_miss += 1
                max_miss = max(max_miss, cur_miss)
            else:
                cur_miss = 0

        if max_miss >= 3:
            dropped += 1

    return {
        "total":   total,
        "active":  total - dropped,
        "dropped": dropped,
    }


# ── Механика удержания при пропусках ─────────────────────────

def get_missed_streak(user_id: int) -> int:
    """
    Считает сколько дней подряд пропущено до сегодня (включая вчера).
    День считается пропущенным если прошёл и не все 5 задач отмечены.
    Сегодняшний день не считается пропущенным (ещё идёт).
    """
    current_day = get_current_day(user_id)
    completed = get_completed_days_set(user_id)
    missed = 0
    # Проверяем дни в обратном порядке, начиная со вчера (current_day - 1)
    for d in range(current_day - 1, 0, -1):
        if d not in completed:
            missed += 1
        else:
            break
    return missed


def get_last_completed_day(user_id: int) -> int:
    """Возвращает номер последнего засчитанного дня (0 если ни одного)."""
    completed = get_completed_days_set(user_id)
    return max(completed) if completed else 0


def set_dropout_warning_sent(user_id: int):
    """Фиксирует момент отправки предупреждения о выбытии."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET dropout_warning_sent_at = datetime('now') WHERE user_id = ?",
            (user_id,)
        )


def clear_dropout_warning(user_id: int):
    """Сбрасывает предупреждение о выбытии (участник вернулся)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET dropout_warning_sent_at = NULL WHERE user_id = ?",
            (user_id,)
        )


def should_dropout(user_id: int) -> bool:
    """
    Возвращает True если:
    - было отправлено предупреждение о выбытии (dropout_warning_sent_at не NULL)
    - прошло ≥ 24 часа
    - участник всё ещё не вернулся (missed_streak ≥ 3)
    """
    user = get_user(user_id)
    if not user or not user["dropout_warning_sent_at"]:
        return False
    if get_missed_streak(user_id) < 3:
        return False
    try:
        warned_at = datetime.fromisoformat(user["dropout_warning_sent_at"])
        hours_passed = (datetime.utcnow() - warned_at).total_seconds() / 3600
        return hours_passed >= 24
    except Exception:
        return False


def deactivate_user(user_id: int):
    """Выбывает участника из программы."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET is_active = 0 WHERE user_id = ?",
            (user_id,)
        )


# ── Ачивки ────────────────────────────────────────────────────

def has_achievement(user_id: int, achievement_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM user_achievements WHERE user_id = ? AND achievement_id = ?",
            (user_id, achievement_id)
        ).fetchone()
        return row is not None


def award_achievement(user_id: int, achievement_id: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_achievements (user_id, achievement_id) VALUES (?, ?)",
            (user_id, achievement_id)
        )


def has_dropout_warning(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user and user["dropout_warning_sent_at"])


# ── Тестирование ──────────────────────────────────────────────

def reset_user(user_id: int):
    """Полный сброс участника — удаляет все записи, как будто он никогда не регистрировался"""
    with get_conn() as conn:
        conn.execute("DELETE FROM task_completions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_achievements WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))


def set_day_for_testing(user_id: int, target_day: int):
    """Сдвигает дату старта для тестирования"""
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
