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
DB_PATH    = _data_dir / "zarik.db"
PHOTOS_DIR = _data_dir / "photos"
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

TOTAL_DAYS = 77   # Длительность программы
TASKS_PER_DAY = 5 # Количество задач в день


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # безопасная запись из двух потоков
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
                is_virtual               INTEGER DEFAULT 0,
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

            CREATE TABLE IF NOT EXISTS user_photos (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                photo_type   TEXT NOT NULL DEFAULT 'before',
                file_id      TEXT NOT NULL,
                created_at   TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS leads (
                user_id              INTEGER PRIMARY KEY,
                username             TEXT,
                first_name           TEXT,
                subscribed_at        TEXT,
                tracker_sent_at      TEXT,
                tracker_question_at  TEXT,
                tracker_reply_yes    INTEGER,
                intro_sent_at        TEXT,
                pitch_sent_at        TEXT,
                start_clicked_at     TEXT,
                stake_asked_at       TEXT,
                stake_choice         TEXT,
                invoice_sent_at      TEXT,
                purchased_at         TEXT,
                follow_2_sent_at     TEXT,
                follow_3_sent_at     TEXT,
                follow_7_sent_at     TEXT,
                final_sent_at        TEXT,
                lead_status          TEXT DEFAULT 'new',
                created_at           TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS user_sessions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL,
                session_date      TEXT NOT NULL,
                first_open_utc    TEXT NOT NULL,
                last_open_utc     TEXT NOT NULL,
                interaction_count INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                UNIQUE(user_id, session_date)
            );

            CREATE TABLE IF NOT EXISTS tracker_messages (
                user_id    INTEGER NOT NULL,
                day_number INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, day_number)
            );

            -- ── MAX Мессенджер ─────────────────────────────────────
            -- Маппинг MAX user_id → internal_id (отрицательные ≤ -1_000_001)
            CREATE TABLE IF NOT EXISTS max_users (
                max_user_id  INTEGER PRIMARY KEY,
                internal_id  INTEGER NOT NULL UNIQUE,
                username     TEXT,
                first_name   TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );

            -- Воронка лидов из MAX-лид-бота
            CREATE TABLE IF NOT EXISTS max_leads (
                max_user_id          INTEGER PRIMARY KEY,
                username             TEXT,
                first_name           TEXT,
                subscribed_at        TEXT,
                tracker_sent_at      TEXT,
                tracker_question_at  TEXT,
                tracker_reply_yes    INTEGER,
                intro_sent_at        TEXT,
                pitch_sent_at        TEXT,
                start_clicked_at     TEXT,
                invoice_sent_at      TEXT,
                purchased_at         TEXT,
                follow_2_sent_at     TEXT,
                follow_3_sent_at     TEXT,
                follow_7_sent_at     TEXT,
                final_sent_at        TEXT,
                lead_status          TEXT DEFAULT 'new',
                created_at           TEXT DEFAULT (datetime('now'))
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
            "ALTER TABLE users ADD COLUMN use_miniapp INTEGER DEFAULT 1",
            "ALTER TABLE users ADD COLUMN share_photos INTEGER DEFAULT NULL",
            "ALTER TABLE user_photos ADD COLUMN photo_data BLOB DEFAULT NULL",
            "ALTER TABLE user_photos ADD COLUMN photo_path TEXT DEFAULT NULL",
            "ALTER TABLE leads ADD COLUMN phone TEXT DEFAULT NULL",
            # leads — детальная воронка
            "ALTER TABLE leads ADD COLUMN tracker_question_at TEXT",
            "ALTER TABLE leads ADD COLUMN tracker_reply_yes INTEGER",
            "ALTER TABLE leads ADD COLUMN intro_sent_at TEXT",
            "ALTER TABLE leads ADD COLUMN start_clicked_at TEXT",
            "ALTER TABLE leads ADD COLUMN stake_asked_at TEXT",
            "ALTER TABLE leads ADD COLUMN stake_choice TEXT",
            "ALTER TABLE leads ADD COLUMN invoice_sent_at TEXT",
            "ALTER TABLE leads ADD COLUMN purchased_at TEXT",
            "ALTER TABLE users ADD COLUMN is_virtual INTEGER DEFAULT 0",
            # max_leads — stake-флоу и данные покупателя
            "ALTER TABLE max_leads ADD COLUMN start_clicked_at TEXT",
            "ALTER TABLE max_leads ADD COLUMN stake_asked_at TEXT",
            "ALTER TABLE max_leads ADD COLUMN stake_choice TEXT",
            "ALTER TABLE max_leads ADD COLUMN full_name TEXT",
            "ALTER TABLE max_leads ADD COLUMN email TEXT",
            "ALTER TABLE max_leads ADD COLUMN phone TEXT",
            # Реферальные коды блогеров
            "ALTER TABLE leads ADD COLUMN referral_code TEXT DEFAULT NULL",
            "ALTER TABLE max_leads ADD COLUMN referral_code TEXT DEFAULT NULL",
        ]
        for migration in migrations:
            try:
                conn.execute(migration)
            except Exception:
                pass

        # Засеиваем 125 виртуальных участников (если ещё не созданы)
        seed_virtual_users(conn, n=125)


def seed_virtual_users(conn: sqlite3.Connection, n: int = 125):
    """
    Создаёт n виртуальных участников с отрицательными user_id (-1 … -n).
    Они учитываются в счётчике для пользователей (эффект «ты 126-й»),
    но не видны в отчёте администратора.
    Повторный вызов безопасен: INSERT OR IGNORE.
    """
    for i in range(1, n + 1):
        conn.execute("""
            INSERT OR IGNORE INTO users
                (user_id, username, first_name, start_date,
                 onboarding_step, onboarding_complete, is_active, is_virtual)
            VALUES (?, ?, ?, '2000-01-01', 'done', 1, 1, 1)
        """, (-i, f"virtual_{i}", f"Участник {i}"))


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


def save_user_phone(user_id: int, phone: str):
    """Сохраняет номер телефона пользователя (из контакта Telegram)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET phone = ? WHERE user_id = ?",
            (phone, user_id)
        )


def save_lead_phone(user_id: int, phone: str):
    """Сохраняет номер телефона лида."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET phone = ? WHERE user_id = ?",
            (phone, user_id)
        )


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
            "SELECT * FROM users WHERE is_active = 1 AND onboarding_complete = 1 AND (is_virtual = 0 OR is_virtual IS NULL)"
        ).fetchall()


def get_user_count() -> int:
    """Количество завершивших онбординг участников (без виртуальных — для отчёта админа)"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE onboarding_complete = 1 AND (is_virtual = 0 OR is_virtual IS NULL)"
        ).fetchone()
        return row["cnt"] if row else 0


def get_user_count_with_virtual() -> int:
    """Количество участников включая виртуальных — показывается пользователям"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE onboarding_complete = 1"
        ).fetchone()
        return row["cnt"] if row else 0


def get_total_stake() -> int:
    """Суммарная ставка всех участников в копейках (без виртуальных)"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT SUM(stake_amount) as total FROM users WHERE onboarding_complete = 1 AND (is_virtual = 0 OR is_virtual IS NULL)"
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
    """Сохраняет стартовый пресс, переходит к шагу photo."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET abs_start = ?, onboarding_step = 'photo' WHERE user_id = ?",
            (reps, user_id)
        )


def set_share_photos(user_id: int, value: bool):
    """Сохраняет согласие пользователя делиться фото до/после."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET share_photos = ? WHERE user_id = ?",
            (1 if value else 0, user_id)
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


def toggle_task(user_id: int, day_number: int, task_index: int) -> bool:
    """Переключает задачу: если выполнена — снимает отметку, если нет — ставит.
    Возвращает True если задача теперь отмечена выполненной."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM task_completions WHERE user_id=? AND day_number=? AND task_index=?",
            (user_id, day_number, task_index)
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM task_completions WHERE user_id=? AND day_number=? AND task_index=?",
                (user_id, day_number, task_index)
            )
            return False
        else:
            conn.execute(
                "INSERT OR IGNORE INTO task_completions (user_id, day_number, task_index) VALUES (?,?,?)",
                (user_id, day_number, task_index)
            )
            return True


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


# ── Статистика группы ─────────────────────────────────────────

def get_group_stats() -> dict:
    """
    Возвращает живую статистику группы для еженедельного сообщения.
      total    — всего завершили онбординг (включая виртуальных)
      active   — активны (не выбыли: не было 3+ пропущенных дней подряд)
      dropped  — выбывшие (только реальные пользователи)
    Виртуальные участники всегда считаются активными и входят в total.
    """
    with get_conn() as conn:
        all_users = conn.execute(
            "SELECT user_id, is_virtual FROM users WHERE onboarding_complete = 1 AND is_active = 1"
        ).fetchall()

    real_users = [r for r in all_users if not r["is_virtual"]]

    total = len(all_users)
    dropped = 0

    today = date.today()
    for row in real_users:
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


# ── Режим отображения (Mini App / инлайн-клавиатура) ─────────

def get_use_miniapp(user_id: int) -> bool:
    """Вернёт True если пользователь использует Mini App (по умолчанию True)."""
    user = get_user(user_id)
    if user is None:
        return True
    val = user["use_miniapp"]
    return val != 0  # NULL тоже считаем True

def set_miniapp_mode(user_id: int, use_mini: bool):
    """Переключает режим: True = Mini App, False = инлайн-клавиатура."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET use_miniapp = ? WHERE user_id = ?",
            (1 if use_mini else 0, user_id)
        )


# ── Фото пользователей ────────────────────────────────────────

def save_user_photo(
    user_id: int,
    photo_type: str,
    file_id: str,
    photo_data: bytes | None = None,
    photo_path: str | None = None,
):
    """Сохраняет фото.
    Предпочтительный режим: photo_path — путь к файлу на диске (не BLOB).
    photo_data сохраняется для обратной совместимости со старыми записями.
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_photos (user_id, photo_type, file_id, photo_data, photo_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, photo_type, file_id, photo_data, photo_path)
        )


def get_photos_without_data() -> list:
    """Возвращает все записи user_photos где photo_data IS NULL И photo_path IS NULL (нужен бэкфил)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, file_id FROM user_photos "
            "WHERE photo_data IS NULL AND photo_path IS NULL"
        ).fetchall()
    return [dict(r) for r in rows]


def update_photo_data(photo_id: int, photo_data: bytes):
    """Записывает BLOB для существующей записи по её id (legacy)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE user_photos SET photo_data = ? WHERE id = ?",
            (photo_data, photo_id)
        )


def update_photo_path(photo_id: int, photo_path: str):
    """Записывает путь к файлу на диске для существующей записи."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE user_photos SET photo_path = ? WHERE id = ?",
            (photo_path, photo_id)
        )


def get_user_photos(user_id: int, photo_type: str = "before") -> list:
    """Возвращает список фото пользователя (id, file_id, photo_data, created_at)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, file_id, photo_data, created_at FROM user_photos "
            "WHERE user_id = ? AND photo_type = ? ORDER BY id",
            (user_id, photo_type)
        ).fetchall()
    return [dict(r) for r in rows]

def count_user_photos(user_id: int, photo_type: str = "before") -> int:
    """Количество сохранённых фото."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM user_photos WHERE user_id = ? AND photo_type = ?",
            (user_id, photo_type)
        ).fetchone()
    return row["cnt"] if row else 0


# ── CRM: лиды (@shagov77_bot) ────────────────────────────────

def upsert_lead(user_id: int, username: str, first_name: str):
    """Создаёт или обновляет запись лида (не меняет уже выставленные статусы)."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO leads (user_id, username, first_name)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   username   = excluded.username,
                   first_name = excluded.first_name""",
            (user_id, username or "", first_name or "")
        )

def mark_lead_subscribed(user_id: int):
    with get_conn() as conn:
        conn.execute(
            """UPDATE leads SET subscribed_at = datetime('now'), lead_status = 'subscribed'
               WHERE user_id = ? AND subscribed_at IS NULL""",
            (user_id,)
        )

def mark_lead_tracker_sent(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET tracker_sent_at = datetime('now'), lead_status = 'tracker_sent' WHERE user_id = ?",
            (user_id,)
        )

def mark_lead_pitch_sent(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET pitch_sent_at = datetime('now') WHERE user_id = ?",
            (user_id,)
        )

def mark_lead_follow_up(user_id: int, day: int):
    col = {2: "follow_2_sent_at", 3: "follow_3_sent_at", 7: "follow_7_sent_at"}.get(day)
    if not col:
        return
    with get_conn() as conn:
        conn.execute(f"UPDATE leads SET {col} = datetime('now') WHERE user_id = ?", (user_id,))

def mark_lead_final(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET final_sent_at = datetime('now'), lead_status = 'cold' WHERE user_id = ?",
            (user_id,)
        )


# ── Трекер-сообщения (синхронизация Telegram ↔ мини-апп) ─────

def save_tracker_message(user_id: int, day_number: int, message_id: int):
    """Сохраняет message_id трекер-сообщения для последующего редактирования."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tracker_messages (user_id, day_number, message_id, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(user_id, day_number) DO UPDATE SET
                   message_id = excluded.message_id,
                   updated_at = datetime('now')""",
            (user_id, day_number, message_id)
        )


def get_tracker_message_id(user_id: int, day_number: int) -> int | None:
    """Возвращает message_id последнего трекер-сообщения для данного дня, или None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT message_id FROM tracker_messages WHERE user_id = ? AND day_number = ?",
            (user_id, day_number)
        ).fetchone()
    return row["message_id"] if row else None

def set_lead_referral(user_id: int, referral_code: str):
    """Сохраняет реферальный код блогера для Telegram-лида (только если ещё не задан)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET referral_code = ? WHERE user_id = ? AND referral_code IS NULL",
            (referral_code, user_id)
        )


def set_max_lead_referral(max_user_id: int, referral_code: str):
    """Сохраняет реферальный код блогера для MAX-лида (только если ещё не задан)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET referral_code = ? WHERE max_user_id = ? AND referral_code IS NULL",
            (referral_code, max_user_id)
        )


def get_blogger_stats_tg() -> list:
    """Статистика лидов по реферальным кодам (Telegram): переходы → купили."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT
                referral_code,
                COUNT(*)                          AS total,
                SUM(subscribed_at IS NOT NULL)    AS subscribed,
                SUM(lead_status = 'purchased')    AS purchased
            FROM leads
            WHERE referral_code IS NOT NULL
            GROUP BY referral_code
            ORDER BY purchased DESC, total DESC
        """).fetchall()


def get_blogger_stats_max() -> list:
    """Статистика лидов по реферальным кодам (MAX): переходы → купили."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT
                referral_code,
                COUNT(*)                          AS total,
                SUM(subscribed_at IS NOT NULL)    AS subscribed,
                SUM(lead_status = 'purchased')    AS purchased
            FROM max_leads
            WHERE referral_code IS NOT NULL
            GROUP BY referral_code
            ORDER BY purchased DESC, total DESC
        """).fetchall()


def mark_lead_purchased(user_id: int):
    with get_conn() as conn:
        conn.execute(
            """UPDATE leads
               SET lead_status = 'purchased',
                   purchased_at = COALESCE(purchased_at, datetime('now'))
               WHERE user_id = ?""",
            (user_id,)
        )

def get_tg_leads_purchased_today() -> list:
    """Telegram-лиды, оплатившие сегодня (по UTC-дате)."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT l.user_id, l.first_name, l.username, l.purchased_at,
                   COALESCE(u.participation_fee, 0) AS participation_fee,
                   COALESCE(u.stake_amount,      0) AS stake_amount
            FROM leads l
            LEFT JOIN users u ON u.user_id = l.user_id
            WHERE date(l.purchased_at) = date('now')
              AND l.lead_status = 'purchased'
            ORDER BY l.purchased_at
        """).fetchall()


def get_max_leads_purchased_today() -> list:
    """MAX-лиды, оплатившие сегодня (по UTC-дате)."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT ml.max_user_id, ml.first_name, ml.username, ml.purchased_at,
                   COALESCE(u.participation_fee, 0) AS participation_fee,
                   COALESCE(u.stake_amount,      0) AS stake_amount
            FROM max_leads ml
            LEFT JOIN max_users mu ON mu.max_user_id = ml.max_user_id
            LEFT JOIN users u ON u.user_id = mu.internal_id
            WHERE date(ml.purchased_at) = date('now')
              AND ml.lead_status = 'purchased'
            ORDER BY ml.purchased_at
        """).fetchall()


def mark_lead_tracker_question_sent(user_id: int):
    """Зафиксировать момент отправки вопроса «Ну как, получилось с трекером?»."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET tracker_question_at = COALESCE(tracker_question_at, datetime('now')) WHERE user_id = ?",
            (user_id,)
        )

def mark_lead_tracker_reply(user_id: int, yes: bool):
    """Зафиксировать ответ пользователя на вопрос о трекере (Да=1 / Нет=0)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET tracker_reply_yes = ? WHERE user_id = ?",
            (1 if yes else 0, user_id)
        )

def mark_lead_intro_sent(user_id: int):
    """Зафиксировать отправку знакомства с программой."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET intro_sent_at = COALESCE(intro_sent_at, datetime('now')) WHERE user_id = ?",
            (user_id,)
        )

def mark_lead_start_clicked(user_id: int):
    """Зафиксировать клик на кнопку «Начать за 1990 ₽»."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET start_clicked_at = COALESCE(start_clicked_at, datetime('now')) WHERE user_id = ?",
            (user_id,)
        )

def mark_lead_stake_asked(user_id: int):
    """Зафиксировать момент отправки вопроса про ставку."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET stake_asked_at = COALESCE(stake_asked_at, datetime('now')) WHERE user_id = ?",
            (user_id,)
        )

def mark_lead_stake_choice(user_id: int, choice: str):
    """Зафиксировать выбор ставки: 'yes' или 'no'."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET stake_choice = ? WHERE user_id = ?",
            (choice, user_id)
        )

def mark_lead_invoice_sent(user_id: int):
    """Зафиксировать момент отправки счёта на оплату."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET invoice_sent_at = COALESCE(invoice_sent_at, datetime('now')) WHERE user_id = ?",
            (user_id,)
        )

def get_funnel_stats() -> dict:
    """
    Возвращает полную статистику воронки @Shagov77_bot для команды /funnel.
    Каждый счётчик — кол-во лидов, достигших этого шага.
    """
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)                                          AS total,
                SUM(subscribed_at IS NOT NULL)                    AS subscribed,
                SUM(tracker_sent_at IS NOT NULL)                  AS tracker_sent,
                SUM(tracker_question_at IS NOT NULL)              AS question_sent,
                SUM(tracker_reply_yes IS NOT NULL)                AS question_replied,
                SUM(tracker_reply_yes = 1)                        AS replied_yes,
                SUM(tracker_reply_yes = 0)                        AS replied_no,
                SUM(intro_sent_at IS NOT NULL)                    AS intro_sent,
                SUM(pitch_sent_at IS NOT NULL)                    AS offer_sent,
                SUM(start_clicked_at IS NOT NULL)                 AS start_clicked,
                SUM(stake_asked_at IS NOT NULL)                   AS stake_asked,
                SUM(stake_choice = 'yes')                         AS stake_yes,
                SUM(stake_choice = 'no')                          AS stake_no,
                SUM(invoice_sent_at IS NOT NULL)                  AS invoice_sent,
                SUM(lead_status = 'purchased')                    AS purchased
            FROM leads
        """).fetchone()
    return dict(row) if row else {}

def get_leads_for_followup() -> list:
    """Возвращает лидов, которым нужно отправить follow-up (не купили, трекер отправлен)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM leads
               WHERE lead_status NOT IN ('purchased', 'cold')
               AND tracker_sent_at IS NOT NULL""",
        ).fetchall()
    return [dict(r) for r in rows]

def get_all_leads() -> list:
    """Все лиды для экспорта/рассылки."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ── Статистика по задачам ─────────────────────────────────────

def get_task_completion_counts(user_id: int) -> dict:
    """
    Возвращает {task_index: count} — сколько раз каждая задача выполнена пользователем.
    task_index: 0=тренировка, 1=вода, 2=чтение, 3=питание, 4=алкоголь
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT task_index, COUNT(*) as cnt
            FROM task_completions
            WHERE user_id = ?
            GROUP BY task_index
        """, (user_id,)).fetchall()
    return {row["task_index"]: row["cnt"] for row in rows}


def get_all_task_completion_rates() -> dict:
    """
    Возвращает {task_index: [rates]} — список долей выполнения каждой задачи
    по всем активным участникам (cnt_task / days_in_program).
    Используется для расчёта перцентиля пользователя.
    """
    today = date.today()
    with get_conn() as conn:
        users = conn.execute("""
            SELECT user_id, start_date FROM users
            WHERE onboarding_complete = 1 AND is_active = 1
        """).fetchall()
        all_tc = conn.execute("""
            SELECT user_id, task_index, COUNT(*) as cnt
            FROM task_completions
            GROUP BY user_id, task_index
        """).fetchall()

    # {user_id: {task_index: count}}
    user_task_counts: dict = {}
    for row in all_tc:
        uid = row["user_id"]
        user_task_counts.setdefault(uid, {})[row["task_index"]] = row["cnt"]

    result = {i: [] for i in range(5)}
    for u in users:
        try:
            start = date.fromisoformat(u["start_date"])
            days_in = min((today - start).days + 1, TOTAL_DAYS)
        except Exception:
            continue
        if days_in <= 0:
            continue
        counts = user_task_counts.get(u["user_id"], {})
        for idx in range(5):
            rate = counts.get(idx, 0) / days_in
            result[idx].append(rate)

    return result


# ── Тестирование ──────────────────────────────────────────────

def reset_user(user_id: int):
    """Полный сброс участника — удаляет все записи, как будто он никогда не регистрировался"""
    with get_conn() as conn:
        conn.execute("DELETE FROM task_completions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_achievements WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_photos WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))


def reset_user_keep_payment(user_id: int):
    """Сброс истории для «постоянных» тест-пользователей.
    Удаляет прогресс и возвращает в начало онбординга,
    НО сохраняет payment_charge_id — повторная оплата не требуется."""
    with get_conn() as conn:
        conn.execute("DELETE FROM task_completions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_achievements WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_photos WHERE user_id = ?", (user_id,))
        conn.execute("""UPDATE users SET
                onboarding_step     = 'payment',
                onboarding_complete = 0,
                start_date          = '2099-01-01',
                dropout_warning_sent_at = NULL,
                share_photos        = NULL,
                pushup_start        = 10,
                squat_start         = 10,
                abs_start           = 10,
                is_active           = 1
               WHERE user_id = ?""",
            (user_id,)
        )


def reset_to_onboarding(user_id: int):
    """Мягкий сброс для тест-пользователей: обнуляет прогресс и возвращает в начало онбординга.
    Запись в users сохраняется (оплата остаётся подтверждённой)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM task_completions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_achievements WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_photos WHERE user_id = ?", (user_id,))
        conn.execute(
            """UPDATE users SET
                onboarding_step     = 'payment',
                onboarding_complete = 0,
                start_date          = '2099-01-01',
                dropout_warning_sent_at = NULL,
                share_photos        = NULL,
                pushup_start        = 10,
                squat_start         = 10,
                abs_start           = 10,
                is_active           = 1
               WHERE user_id = ?""",
            (user_id,)
        )


# ── Аналитика сессий (@Zarik_Lazy_Bot) ───────────────────────

def log_user_session(user_id: int):
    """
    Фиксирует факт обращения пользователя (любое сообщение / callback).
    На каждый UTC-день создаётся одна запись:
    - first_open_utc — первое обращение за день
    - last_open_utc  — обновляется при каждом обращении
    - interaction_count — счётчик за день
    """
    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    today   = now_str[:10]
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO user_sessions (user_id, session_date, first_open_utc, last_open_utc, interaction_count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(user_id, session_date)
            DO UPDATE SET
                last_open_utc     = excluded.last_open_utc,
                interaction_count = interaction_count + 1
        """, (user_id, today, now_str, now_str))


def get_session_stats() -> dict:
    """
    Агрегированная статистика по сессиям за последние 7 дней.
    Возвращает dict с ключами:
      active_today, avg_interactions, avg_session_min, active_days_avg,
      top_users ([{user_id, username, interactions}]),
      raw_sessions ([{user_id, timezone, first_open_utc, last_open_utc}])
    """
    seven_days_ago = (date.today() - timedelta(days=7)).isoformat()

    with get_conn() as conn:
        # Активные сегодня
        today_str = date.today().isoformat()
        active_today = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS cnt FROM user_sessions WHERE session_date = ?",
            (today_str,)
        ).fetchone()["cnt"]

        # Средние показатели за 7 дней
        agg = conn.execute("""
            SELECT
                AVG(interaction_count)                                   AS avg_interactions,
                AVG((strftime('%s', last_open_utc) - strftime('%s', first_open_utc)) / 60.0) AS avg_session_min,
                COUNT(DISTINCT user_id)                                  AS active_users,
                COUNT(DISTINCT session_date) * 1.0 / MAX(1, COUNT(DISTINCT user_id)) AS avg_active_days
            FROM user_sessions
            WHERE session_date >= ?
        """, (seven_days_ago,)).fetchone()

        # Топ-5 по числу обращений за 7 дней
        top = conn.execute("""
            SELECT s.user_id, u.username, SUM(s.interaction_count) AS total
            FROM user_sessions s
            LEFT JOIN users u ON u.user_id = s.user_id
            WHERE s.session_date >= ?
            GROUP BY s.user_id
            ORDER BY total DESC
            LIMIT 5
        """, (seven_days_ago,)).fetchall()

        # Сырые сессии за 7 дней для расчёта часового пояса в Python
        raw = conn.execute("""
            SELECT s.user_id, u.timezone, s.first_open_utc, s.last_open_utc
            FROM user_sessions s
            LEFT JOIN users u ON u.user_id = s.user_id
            WHERE s.session_date >= ?
        """, (seven_days_ago,)).fetchall()

    return {
        "active_today":    active_today,
        "avg_interactions": round(agg["avg_interactions"] or 0, 1),
        "avg_session_min":  round(agg["avg_session_min"] or 0, 1),
        "active_users_7d":  agg["active_users"] or 0,
        "avg_active_days":  round(agg["avg_active_days"] or 0, 1),
        "top_users":        [dict(r) for r in top],
        "raw_sessions":     [dict(r) for r in raw],
    }


def set_day_for_testing(user_id: int, target_day: int):
    """Сдвигает дату старта так, чтобы сегодня был target_day.
    Прогресс (task_completions) не трогаем — сбрасывается только через /reset_user."""
    new_start = date.today() - timedelta(days=target_day - 1)
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET start_date = ? WHERE user_id = ?",
            (new_start.isoformat(), user_id)
        )


# ── MAX Мессенджер ─────────────────────────────────────────────

_MAX_INTERNAL_ID_START = -1_000_001   # MAX-пользователи: -1_000_001, -1_000_002, …


def get_or_create_max_user(max_user_id: int, username: str, first_name: str) -> int:
    """
    Возвращает internal_id для пользователя MAX.
    При первом вызове создаёт строку в max_users и users (INSERT OR IGNORE).
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT internal_id FROM max_users WHERE max_user_id = ?",
            (max_user_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE max_users SET username=?, first_name=? WHERE max_user_id=?",
                (username or "", first_name or "Участник", max_user_id)
            )
            return row["internal_id"]

        # Генерируем следующий internal_id
        last_row = conn.execute(
            "SELECT MIN(internal_id) as min_id FROM max_users"
        ).fetchone()
        if last_row and last_row["min_id"] is not None:
            internal_id = last_row["min_id"] - 1
        else:
            internal_id = _MAX_INTERNAL_ID_START

        conn.execute(
            "INSERT INTO max_users (max_user_id, internal_id, username, first_name) VALUES (?,?,?,?)",
            (max_user_id, internal_id, username or "", first_name or "Участник")
        )
        conn.execute("""
            INSERT OR IGNORE INTO users
                (user_id, username, first_name, start_date, onboarding_step, onboarding_complete)
            VALUES (?, ?, ?, '2099-01-01', 'payment', 0)
        """, (internal_id, username or "", first_name or "Участник"))

        return internal_id


def get_max_internal_id(max_user_id: int) -> int | None:
    """Возвращает internal_id для MAX-пользователя или None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT internal_id FROM max_users WHERE max_user_id = ?",
            (max_user_id,)
        ).fetchone()
        return row["internal_id"] if row else None


def get_max_user_id_by_internal(internal_id: int) -> int | None:
    """Обратный маппинг: internal_id → max_user_id."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT max_user_id FROM max_users WHERE internal_id = ?",
            (internal_id,)
        ).fetchone()
        return row["max_user_id"] if row else None


# ── MAX-лиды ──────────────────────────────────────────────────

def upsert_max_lead(max_user_id: int, username: str, first_name: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO max_leads (max_user_id, username, first_name)
            VALUES (?, ?, ?)
        """, (max_user_id, username or "", first_name or ""))
        conn.execute(
            "UPDATE max_leads SET username=?, first_name=? WHERE max_user_id=?",
            (username or "", first_name or "", max_user_id)
        )


def get_max_lead(max_user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM max_leads WHERE max_user_id = ?", (max_user_id,)
        ).fetchone()


def mark_max_lead_subscribed(max_user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET subscribed_at=datetime('now'), lead_status='subscribed' WHERE max_user_id=?",
            (max_user_id,)
        )


def mark_max_lead_tracker_sent(max_user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET tracker_sent_at=datetime('now') WHERE max_user_id=?",
            (max_user_id,)
        )


def mark_max_lead_tracker_reply(max_user_id: int, yes: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET tracker_question_at=datetime('now'), tracker_reply_yes=? WHERE max_user_id=?",
            (1 if yes else 0, max_user_id)
        )


def mark_max_lead_pitch_sent(max_user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET pitch_sent_at=datetime('now'), lead_status='pitched' WHERE max_user_id=?",
            (max_user_id,)
        )


def mark_max_lead_start_clicked(max_user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET start_clicked_at=COALESCE(start_clicked_at, datetime('now')) WHERE max_user_id=?",
            (max_user_id,)
        )


def mark_max_lead_stake_asked(max_user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET stake_asked_at=COALESCE(stake_asked_at, datetime('now')) WHERE max_user_id=?",
            (max_user_id,)
        )


def mark_max_lead_stake_choice(max_user_id: int, choice: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET stake_choice=? WHERE max_user_id=?",
            (choice, max_user_id)
        )


def save_max_lead_buyer_info(max_user_id: int, full_name: str, email: str, phone: str):
    """Сохраняет ФИО, email и телефон покупателя — для чека ЮКасса (54-ФЗ)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET full_name=?, email=?, phone=? WHERE max_user_id=?",
            (full_name, email, phone, max_user_id)
        )


def mark_max_lead_invoice_sent(max_user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET invoice_sent_at=COALESCE(invoice_sent_at, datetime('now')) WHERE max_user_id=?",
            (max_user_id,)
        )


def mark_max_lead_purchased(max_user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET purchased_at=datetime('now'), lead_status='purchased' WHERE max_user_id=?",
            (max_user_id,)
        )


def mark_max_lead_follow(max_user_id: int, day: int):
    col = {2: "follow_2_sent_at", 3: "follow_3_sent_at", 7: "follow_7_sent_at"}.get(day)
    if col:
        with get_conn() as conn:
            conn.execute(
                f"UPDATE max_leads SET {col}=datetime('now') WHERE max_user_id=?",
                (max_user_id,)
            )


def get_max_leads_for_followup(day: int):
    """Возвращает лидов, которым нужно отправить follow-up на N-й день."""
    col = {2: "follow_2_sent_at", 3: "follow_3_sent_at", 7: "follow_7_sent_at"}.get(day)
    if not col:
        return []
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT * FROM max_leads
            WHERE purchased_at IS NULL
              AND {col} IS NULL
              AND subscribed_at IS NOT NULL
              AND (julianday('now') - julianday(subscribed_at)) >= ?
        """, (day,)).fetchall()


def mark_max_lead_intro_sent(max_user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET intro_sent_at=COALESCE(intro_sent_at, datetime('now')) WHERE max_user_id=?",
            (max_user_id,)
        )


def mark_max_lead_final(max_user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE max_leads SET final_sent_at=datetime('now'), lead_status='cold' WHERE max_user_id=?",
            (max_user_id,)
        )


def reset_max_lead(max_user_id: int):
    """Полный сброс MAX-лида — удаляет запись из max_leads.
    Позволяет пользователю пройти воронку заново."""
    with get_conn() as conn:
        conn.execute("DELETE FROM max_leads WHERE max_user_id = ?", (max_user_id,))


def reset_max_lead_keep_purchased(max_user_id: int):
    """Мягкий сброс MAX-лида — сбрасывает воронку, но сохраняет статус purchased.
    Для тест-пользователей: они уже заплатили, повторная оплата не нужна."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE max_leads SET
                subscribed_at        = NULL,
                tracker_sent_at      = NULL,
                tracker_question_at  = NULL,
                tracker_reply_yes    = NULL,
                intro_sent_at        = NULL,
                pitch_sent_at        = NULL,
                start_clicked_at     = NULL,
                stake_asked_at       = NULL,
                stake_choice         = NULL,
                invoice_sent_at      = NULL,
                follow_2_sent_at     = NULL,
                follow_3_sent_at     = NULL,
                follow_7_sent_at     = NULL,
                final_sent_at        = NULL,
                lead_status          = 'new'
            WHERE max_user_id = ?
        """, (max_user_id,))


def is_max_lead_purchased(max_user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT purchased_at FROM max_leads WHERE max_user_id=?", (max_user_id,)
        ).fetchone()
    return bool(row and row["purchased_at"])


def get_all_max_pitched_leads() -> list:
    """Возвращает все лиды, получившие оффер и не купившие — для периодической проверки follow-up."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM max_leads
            WHERE purchased_at IS NULL
              AND final_sent_at IS NULL
              AND pitch_sent_at IS NOT NULL
        """).fetchall()
    return [dict(r) for r in rows]


def get_max_funnel_stats() -> dict:
    """Статистика воронки MAX-лид-бота для команд /leads и /funnel."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)                                    AS total,
                SUM(subscribed_at IS NOT NULL)              AS subscribed,
                SUM(tracker_sent_at IS NOT NULL)            AS tracker_sent,
                SUM(tracker_question_at IS NOT NULL)        AS question_sent,
                SUM(tracker_reply_yes IS NOT NULL)          AS question_replied,
                SUM(tracker_reply_yes = 1)                  AS replied_yes,
                SUM(tracker_reply_yes = 0)                  AS replied_no,
                SUM(intro_sent_at IS NOT NULL)              AS intro_sent,
                SUM(pitch_sent_at IS NOT NULL)              AS offer_sent,
                SUM(start_clicked_at IS NOT NULL)           AS start_clicked,
                SUM(stake_asked_at IS NOT NULL)             AS stake_asked,
                SUM(stake_choice = 'yes')                   AS stake_yes,
                SUM(stake_choice = 'no')                    AS stake_no,
                SUM(invoice_sent_at IS NOT NULL)            AS invoice_sent,
                SUM(lead_status = 'purchased')              AS purchased,
                SUM(follow_2_sent_at IS NOT NULL)           AS follow_2,
                SUM(follow_3_sent_at IS NOT NULL)           AS follow_3,
                SUM(follow_7_sent_at IS NOT NULL)           AS follow_7,
                SUM(final_sent_at IS NOT NULL)              AS final_sent
            FROM max_leads
        """).fetchone()
    return dict(row) if row else {}
