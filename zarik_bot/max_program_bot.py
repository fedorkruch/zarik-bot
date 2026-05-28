"""
max_program_bot.py — основной бот 77 Soft Challenge для Мессенджера MAX
                     (аналог @Zarik_Lazy_Bot).

Переменные окружения:
  MAX_PROGRAM_BOT_TOKEN   — токен основного бота в MAX
  MAX_ADMIN_USER_ID       — MAX user_id администратора
  MAX_LEAD_BOT_URL        — ссылка на MAX лид-бот (для сообщения «оплаты нет»)
  WEBAPP_URL              — URL мини-аппа
  PAYMENT_URL             — URL страницы оплаты
  TEST_USER_IDS           — Telegram IDs тест-юзеров (для Telegram-бота)
  MAX_TEST_USER_IDS       — MAX user IDs тест-юзеров (через запятую)
  MAX_PROGRAM_WEBHOOK_PATH — путь вебхука, по умолчанию /webhook/max-program
"""
import asyncio
import logging
import os
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

from max_client import MaxClient, _btn_callback, _btn_link
import database as db
import content as ct

logger = logging.getLogger(__name__)

# ── Конфигурация ──────────────────────────────────────────────
MAX_PROGRAM_TOKEN   = os.environ.get("MAX_PROGRAM_BOT_TOKEN", "")
MAX_ADMIN_USER_ID   = int(os.environ.get("MAX_ADMIN_USER_ID", "0"))
MAX_LEAD_BOT_URL    = os.environ.get("MAX_LEAD_BOT_URL", "")
WEBAPP_URL          = os.environ.get("WEBAPP_URL", "")
PAYMENT_URL         = os.environ.get("PAYMENT_URL", "")
WEBHOOK_PATH        = os.environ.get("MAX_PROGRAM_WEBHOOK_PATH", "/webhook/max-program")

HAPPY_IMG = Path(__file__).parent / "Happy.png"
NORM_IMG  = Path(__file__).parent / "Norm.png"
SAD_IMG   = Path(__file__).parent / "Sad.png"

_test_ids_raw  = os.environ.get("MAX_TEST_USER_IDS", "")
MAX_TEST_USER_IDS = {int(x) for x in _test_ids_raw.split(",") if x.strip().isdigit()}

TOTAL_DAYS   = 77
TASKS_PER_DAY = 5

# ── Глобальный клиент ─────────────────────────────────────────
_client: MaxClient | None = None
_scheduler: AsyncIOScheduler | None = None

def get_client() -> MaxClient:
    global _client
    if _client is None:
        _client = MaxClient(MAX_PROGRAM_TOKEN)
    return _client

# ── Метки задач ───────────────────────────────────────────────
TASK_LABELS = [
    "💪 Тренировка",
    "💧 Вода — 8 стаканов",
    "📚 Чтение — 10 страниц",
    "🥗 Без фастфуда",
    "🚫 Без алкоголя",
]

TIMEZONES = [
    ("UTC+2 · Калининград",      "Europe/Kaliningrad"),
    ("UTC+3 · Москва",           "Europe/Moscow"),
    ("UTC+3 · Минск / Киев",     "Europe/Minsk"),
    ("UTC+4 · Самара / Баку",    "Europe/Samara"),
    ("UTC+5 · Екатеринбург",     "Asia/Yekaterinburg"),
    ("UTC+5 · Ташкент / Астана", "Asia/Tashkent"),
    ("UTC+6 · Омск",             "Asia/Omsk"),
    ("UTC+7 · Красноярск / Нск", "Asia/Krasnoyarsk"),
    ("UTC+8 · Иркутск",          "Asia/Irkutsk"),
    ("UTC+9 · Якутск",           "Asia/Yakutsk"),
    ("UTC+10 · Владивосток",     "Asia/Vladivostok"),
    ("UTC+11 · Магадан",         "Asia/Magadan"),
    ("UTC+12 · Камчатка",        "Asia/Kamchatka"),
]

DIGIT_ROWS = [
    ["1", "2", "3", "4", "5"],
    ["6", "7", "8", "9", "10"],
    ["12", "15", "20", "25", "30"],
]

# ── Тексты ────────────────────────────────────────────────────

WELCOME_TEXT = (
    "🦥 Привет! Я Зарик — твой ленивый наставник на ближайшие 77 дней.\n\n"
    "Вот что мы будем делать каждый день:\n"
    "💪 Тренировка (подобрана под тебя)\n"
    "💧 Вода — восстанавливаем баланс\n"
    "📚 Чтение — качаем мозги\n"
    "🥗 Питание — убираем лишнее\n"
    "🚫 Алкоголь — разбираемся и с этим\n\n"
    "Сейчас задам несколько вопросов для комфортного старта. "
    "Займёт меньше минуты 🦥"
)

NOT_PAID_TEXT = (
    "🦥 Участие в программе не оплачено.\n\n"
    "Чтобы начать — сначала оформи участие:"
)


# ── Клавиатуры MAX ────────────────────────────────────────────

def _tasks_buttons(day: int, completed: set) -> list[list[dict]]:
    buttons = []
    for i, label in enumerate(TASK_LABELS):
        mark = "✅" if i in completed else "☐"
        buttons.append([_btn_callback(f"{mark} {label}", f"task:{day}:{i}")])
    return buttons


def _timezone_buttons() -> list[list[dict]]:
    rows = []
    for label, tz in TIMEZONES:
        rows.append([_btn_callback(label, f"tz:{tz}")])
    return rows


def _digits_buttons(prefix: str) -> list[list[dict]]:
    rows = []
    for row in DIGIT_ROWS:
        rows.append([_btn_callback(v, f"{prefix}:{v}") for v in row])
    return rows


def _main_menu_buttons(uid: int) -> list[list[dict]]:
    buttons = [[_btn_callback("📋 Сегодня", "menu:today")]]
    if WEBAPP_URL:
        buttons.append([_btn_link("📱 Мини-апп", f"{WEBAPP_URL}?uid={uid}")])
    buttons.append([_btn_callback("📊 Статистика", "menu:stats")])
    return buttons


def _pay_buttons() -> list[list[dict]]:
    if PAYMENT_URL:
        return [[_btn_link("💳 Оплатить — 1990 ₽", PAYMENT_URL)]]
    if MAX_LEAD_BOT_URL:
        return [[_btn_link("👉 Перейти для регистрации", MAX_LEAD_BOT_URL)]]
    return []


# ── Вспомогательные функции ───────────────────────────────────

def get_mood_image(completed_count: int) -> Path:
    if completed_count >= 5:
        return HAPPY_IMG
    if completed_count > 0:
        return NORM_IMG
    return SAD_IMG


async def send_mood_message(bot: MaxClient, max_user_id: int, text: str,
                             completed_count: int, buttons: list | None = None):
    img_path = get_mood_image(completed_count)
    if img_path.exists():
        await bot.send_photo(max_user_id, img_path, caption=text, buttons=buttons)
    else:
        await bot.send_message(max_user_id, text, buttons=buttons)


def make_mini_bar(value: int, total: int, width: int = 10) -> str:
    if total == 0:
        return "·" * width
    filled = round(value / total * width)
    return "●" * filled + "·" * (width - filled)


def build_today_text(day: int, completed: set) -> str:
    n = len(completed)
    bar = make_mini_bar(n, TASKS_PER_DAY)
    return (
        f"**День {day} из {TOTAL_DAYS}** [{bar}]\n\n"
        "Отметь что выполнил сегодня 👇"
    )


def build_stats_text(uid: int) -> str:
    stats = db.get_stats(uid)
    days_done = stats["days_completed"]
    streak    = stats["streak"]
    percentile, pct_ctx = ct.get_planet_percentile(days_done)
    bar = make_mini_bar(days_done, TOTAL_DAYS, 15)
    return (
        f"**📊 Твои итоги**\n\n"
        f"Завершённых дней: {days_done} / {TOTAL_DAYS}\n"
        f"Прогресс: [{bar}]\n"
        f"Серия: {streak} 🔥\n\n"
        f"🌍 {pct_ctx}"
    )


# ── Онбординг ─────────────────────────────────────────────────

async def start_onboarding(bot: MaxClient, max_user_id: int, uid: int):
    """Запускает онбординг — первый вопрос: часовой пояс."""
    db.set_onboarding_step(uid, "timezone")
    await bot.send_message(
        max_user_id,
        "🌍 **Выбери свой часовой пояс:**",
        buttons=_timezone_buttons(),
    )


async def handle_onboarding_callback(bot: MaxClient, max_user_id: int, uid: int,
                                     callback_id: str, payload: str):
    """Обрабатывает все шаги онбординга через callback."""
    step = db.get_onboarding_step(uid)

    if step == "timezone" and payload.startswith("tz:"):
        tz = payload[3:]
        db.set_user_timezone(uid, tz)          # → сохраняет tz, ставит step='pushup'
        await bot.answer_callback(callback_id)
        await bot.send_message(
            max_user_id,
            "💪 **Сколько отжиманий ты можешь сделать за один раз?**\n\n"
            "Буду составлять тренировки исходя из твоего уровня.",
            buttons=_digits_buttons("pushup"),
        )

    elif step == "pushup" and payload.startswith("pushup:"):
        val = int(payload.split(":")[1])
        db.save_pushup_start(uid, val)          # → сохраняет pushup, ставит step='squat'
        await bot.answer_callback(callback_id)
        await bot.send_message(
            max_user_id,
            "🦵 **Сколько приседаний?**",
            buttons=_digits_buttons("squat"),
        )

    elif step == "squat" and payload.startswith("squat:"):
        val = int(payload.split(":")[1])
        db.save_squat_start(uid, val)           # → сохраняет squat, ставит step='abs'
        await bot.answer_callback(callback_id)
        await bot.send_message(
            max_user_id,
            "🏋️ **Сколько подъёмов корпуса?**",
            buttons=_digits_buttons("abs"),
        )

    elif step in ("abs", "photo") and payload.startswith("abs:"):
        val = int(payload.split(":")[1])
        db.save_abs_start(uid, val)             # → сохраняет abs, ставит step='photo'
        await bot.answer_callback(callback_id)
        db.complete_onboarding(uid)             # → complete=1, step='done', start=завтра

        user = db.get_user(uid)
        start_str = user["start_date"] if user else "завтра"
        try:
            from datetime import date as _date
            start_fmt = _date.fromisoformat(start_str).strftime("%d.%m.%Y")
        except Exception:
            start_fmt = start_str

        await bot.send_message(
            max_user_id,
            f"🦥 **Всё готово!**\n\n"
            f"Твой первый день — **{start_fmt}**\n\n"
            "Утром в 6:00 получишь первое задание. До встречи! 💪",
            buttons=_main_menu_buttons(max_user_id),
        )


# ── Дневной экран ─────────────────────────────────────────────

async def show_today(bot: MaxClient, max_user_id: int, uid: int):
    day = db.get_current_day(uid)
    if day == 0:
        user = db.get_user(uid)
        start = user["start_date"] if user else "—"
        await bot.send_message(
            max_user_id,
            f"🦥 Программа стартует **{start}**.\n\nЗагляни сюда утром первого дня!",
            buttons=_main_menu_buttons(max_user_id),
        )
        return
    if day > TOTAL_DAYS:
        await bot.send_message(
            max_user_id,
            "🏆 Ты прошёл все 77 дней! Это легенда! 🦥",
            buttons=_main_menu_buttons(max_user_id),
        )
        return
    completed = set(db.get_completed_tasks(uid, day))
    text = build_today_text(day, completed)
    await bot.send_message(max_user_id, text, buttons=_tasks_buttons(day, completed))


# ── Обработчики callback ──────────────────────────────────────

async def on_callback(max_user_id: int, callback_id: str, payload: str,
                      username: str, first_name: str):
    bot = get_client()
    uid = db.get_or_create_max_user(max_user_id, username, first_name)
    step = db.get_onboarding_step(uid)

    # Онбординг
    _u = db.get_user(uid)
    if step not in ("done", "welcome") and not (_u and _u["onboarding_complete"]):
        await handle_onboarding_callback(bot, max_user_id, uid, callback_id, payload)
        return

    await bot.answer_callback(callback_id)

    # Задача
    if payload.startswith("task:"):
        _, day_s, idx_s = payload.split(":")
        day = int(day_s)
        idx = int(idx_s)
        now_done = db.toggle_task(uid, day, idx)
        completed = set(db.get_completed_tasks(uid, day))
        all_done = len(completed) == TASKS_PER_DAY

        text = build_today_text(day, completed)
        buttons = _tasks_buttons(day, completed)

        if all_done:
            # Все задачи выполнены — поздравляем
            morning_text = ct.MORNING[day - 1] if day <= len(ct.MORNING) else ""
            celebrate_text = (
                f"🎉 **День {day} выполнен!**\n\n"
                f"{morning_text}\n\n"
                "Ты молодец! Завтра продолжим 🦥"
            )
            await send_mood_message(bot, max_user_id, celebrate_text, 5)
        else:
            await bot.send_message(max_user_id, text, buttons=buttons)

    # Главное меню
    elif payload == "menu:today":
        if not db.is_program_started(uid):
            await show_today(bot, max_user_id, uid)
        else:
            await show_today(bot, max_user_id, uid)

    elif payload == "menu:stats":
        text = build_stats_text(uid)
        await bot.send_message(max_user_id, text, buttons=_main_menu_buttons(max_user_id))

    elif payload == "onboard:start":
        await start_onboarding(bot, max_user_id, uid)

    # Онбординг из callback (для уже начатых шагов)
    elif (payload.startswith("tz:") or payload.startswith("pushup:") or
          payload.startswith("squat:") or payload.startswith("abs:")):
        await handle_onboarding_callback(bot, max_user_id, uid, "", payload)


# ── Обработчик текстовых сообщений ───────────────────────────

async def on_message(max_user_id: int, text: str, username: str, first_name: str):
    bot = get_client()
    uid = db.get_or_create_max_user(max_user_id, username, first_name)
    text = (text or "").strip()

    # ── Команды администратора ────────────────────────────────
    if max_user_id == MAX_ADMIN_USER_ID:
        if text.startswith("/reset_user"):
            parts = text.split()
            target_max_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else max_user_id
            target_uid = db.get_max_internal_id(target_max_id)
            if target_uid is None:
                await bot.send_message(max_user_id, f"❌ Пользователь {target_max_id} не найден")
                return
            if target_max_id in MAX_TEST_USER_IDS:
                db.register_user(target_uid, username, first_name)
                db.reset_user_keep_payment(target_uid)
                db.save_payment(user_id=target_uid, charge_id=f"max_test_{target_max_id}",
                                participation_fee=0, stake_amount=0)
                db.set_onboarding_step(target_uid, "welcome")
                await _send_welcome_to_max_user(bot, target_max_id, target_uid)
                await bot.send_message(max_user_id, f"✅ MAX {target_max_id} сброшен (оплата сохранена)")
            else:
                db.reset_user(target_uid)
                await bot.send_message(max_user_id, f"✅ MAX {target_max_id} полностью сброшен")
            return

        if text.startswith("/grant"):
            parts = text.split()
            target_max_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else max_user_id
            target_uid = db.get_or_create_max_user(target_max_id, "", "")
            db.save_payment(user_id=target_uid, charge_id=f"max_grant_{target_max_id}",
                            participation_fee=0, stake_amount=0)
            await bot.send_message(max_user_id, f"✅ Оплата подтверждена для MAX {target_max_id}")
            return

        if text.startswith("/stats"):
            count = db.get_user_count()
            count_v = db.get_user_count_with_virtual()
            await bot.send_message(
                max_user_id,
                f"📊 **Статистика MAX бота**\n\n"
                f"Участников: {count}\n"
                f"С виртуальными: {count_v}"
            )
            return

        if text.startswith("/setday"):
            parts = text.split()
            if len(parts) >= 3:
                target_max_id = int(parts[1]) if parts[1].isdigit() else max_user_id
                target_day = int(parts[2])
                target_uid = db.get_max_internal_id(target_max_id)
                if target_uid:
                    db.set_day_for_testing(target_uid, target_day)
                    await bot.send_message(max_user_id, f"✅ День {target_day} установлен для {target_max_id}")
            return

    # ── Обычные пользователи ──────────────────────────────────
    cmd = text.lower().split()[0] if text else ""

    if cmd in ("/start", "start", "старт"):
        await _handle_start(bot, max_user_id, uid, username, first_name)
        return

    if cmd in ("/myid", "myid"):
        await bot.send_message(max_user_id, f"Твой MAX user\\_id: `{max_user_id}`")
        return

    if cmd in ("/help", "помощь", "помощь"):
        await bot.send_message(
            max_user_id,
            "🦥 **Помощь**\n\n"
            "Напиши **старт** или **сегодня** — покажу твои задачи.\n"
            "Напиши **итоги** или **статистика** — покажу прогресс.\n\n"
            "Если возникли вопросы — пиши администратору.",
            buttons=_main_menu_buttons(max_user_id),
        )
        return

    if cmd in ("сегодня", "задачи"):
        _u2 = db.get_user(uid)
        if db.is_payment_confirmed(uid) and (_u2 and _u2["onboarding_complete"]):
            await show_today(bot, max_user_id, uid)
        return

    if cmd in ("итоги", "статистика", "прогресс"):
        if db.is_payment_confirmed(uid):
            text_out = build_stats_text(uid)
            await bot.send_message(max_user_id, text_out, buttons=_main_menu_buttons(max_user_id))
        return


async def _handle_start(bot: MaxClient, max_user_id: int, uid: int,
                         username: str, first_name: str):
    """Обработка команды /start."""
    # Тест-пользователи: авто-грант
    if max_user_id in MAX_TEST_USER_IDS:
        db.register_user(uid, username, first_name)
        db.reset_user_keep_payment(uid)
        db.save_payment(user_id=uid, charge_id=f"max_test_{max_user_id}",
                        participation_fee=0, stake_amount=0)
        db.set_onboarding_step(uid, "welcome")

    if not db.is_payment_confirmed(uid):
        await bot.send_message(max_user_id, NOT_PAID_TEXT, buttons=_pay_buttons())
        return

    await _send_welcome_to_max_user(bot, max_user_id, uid)


async def _send_welcome_to_max_user(bot: MaxClient, max_user_id: int, uid: int):
    user = db.get_user(uid)
    onboarding_done = bool(user and user["onboarding_complete"])
    if onboarding_done and db.is_program_started(uid):
        await show_today(bot, max_user_id, uid)
    elif onboarding_done:
        await show_today(bot, max_user_id, uid)
    else:
        await bot.send_message(
            max_user_id, WELCOME_TEXT,
            buttons=[[_btn_callback("Начать ▶️", "onboard:start")]]
        )
        db.set_onboarding_step(uid, "welcome")


# ── bot_started событие ───────────────────────────────────────

async def on_bot_started(max_user_id: int, username: str, first_name: str):
    bot = get_client()
    uid = db.get_or_create_max_user(max_user_id, username, first_name)
    await _handle_start(bot, max_user_id, uid, username, first_name)


# ── Диспетчер (точка входа вебхука) ──────────────────────────

async def process_update(data: dict):
    update_type = data.get("update_type", "")
    try:
        if update_type == "bot_started":
            user = data.get("user", {})
            await on_bot_started(
                max_user_id=user.get("user_id", 0),
                username=user.get("username", ""),
                first_name=user.get("name", ""),
            )

        elif update_type == "message_created":
            msg = data.get("message", {})
            sender = msg.get("sender", {})
            text = msg.get("body", {}).get("text", "") or ""
            await on_message(
                max_user_id=sender.get("user_id", 0),
                text=text,
                username=sender.get("username", ""),
                first_name=sender.get("name", ""),
            )

        elif update_type == "message_callback":
            cb = data.get("callback", {})
            user = cb.get("user", {})
            await on_callback(
                max_user_id=user.get("user_id", 0),
                callback_id=cb.get("callback_id", ""),
                payload=cb.get("payload", ""),
                username=user.get("username", ""),
                first_name=user.get("name", ""),
            )

    except Exception:
        logger.exception(f"Error processing MAX program update: {update_type}")


# ── Планировщик (утренние / дневные / вечерние сообщения) ─────

async def _job_morning():
    """6:00 — утреннее сообщение с задачами."""
    bot = get_client()
    users = db.get_all_active_users()
    now_utc = datetime.utcnow()
    for user in users:
        uid = user["user_id"]
        max_id = db.get_max_user_id_by_internal(uid)
        if max_id is None:
            continue
        try:
            tz = pytz.timezone(user["timezone"] or "Europe/Moscow")
            now_local = datetime.now(tz)
            if now_local.hour != 6:
                continue
            day = db.get_current_day(uid)
            if day < 1 or day > TOTAL_DAYS:
                continue
            msg = ct.MORNING[day - 1] if day <= len(ct.MORNING) else f"День {day}!"
            completed = set(db.get_completed_tasks(uid, day))
            await bot.send_message(
                max_id,
                f"☀️ {msg}\n\n" + build_today_text(day, completed),
                buttons=_tasks_buttons(day, completed),
            )
        except Exception:
            logger.exception(f"Morning job error for MAX user {max_id}")


async def _job_afternoon():
    """14:00 — дневная проверка с mood-картинкой."""
    bot = get_client()
    users = db.get_all_active_users()
    for user in users:
        uid = user["user_id"]
        max_id = db.get_max_user_id_by_internal(uid)
        if max_id is None:
            continue
        try:
            tz = pytz.timezone(user["timezone"] or "Europe/Moscow")
            now_local = datetime.now(tz)
            if now_local.hour != 14:
                continue
            day = db.get_current_day(uid)
            if day < 1 or day > TOTAL_DAYS:
                continue
            completed = set(db.get_completed_tasks(uid, day))
            n = len(completed)
            text = (
                f"🕑 **Середина дня — {day} день**\n\n"
                f"Выполнено: {n} / {TASKS_PER_DAY}\n\n"
                + ("Уже всё? Ты монстр 🦥" if n == TASKS_PER_DAY else
                   "Ещё есть время добрать! 💪" if n > 0 else
                   "Пока ноль? Не беда — ещё день впереди 🦥")
            )
            await send_mood_message(bot, max_id, text, n,
                                    buttons=_tasks_buttons(day, completed))
        except Exception:
            logger.exception(f"Afternoon job error for MAX user {max_id}")


async def _job_evening():
    """21:00 — вечерний итог с mood-картинкой."""
    bot = get_client()
    users = db.get_all_active_users()
    for user in users:
        uid = user["user_id"]
        max_id = db.get_max_user_id_by_internal(uid)
        if max_id is None:
            continue
        try:
            tz = pytz.timezone(user["timezone"] or "Europe/Moscow")
            now_local = datetime.now(tz)
            if now_local.hour != 21:
                continue
            day = db.get_current_day(uid)
            if day < 1 or day > TOTAL_DAYS:
                continue
            completed = set(db.get_completed_tasks(uid, day))
            n = len(completed)
            if n == TASKS_PER_DAY:
                continue  # уже поздравили при нажатии
            text = (
                f"🌙 **Вечер — день {day}**\n\n"
                f"Выполнено: {n} / {TASKS_PER_DAY}\n\n"
                + ("Не сдавайся — осталось немного!" if n > 0 else
                   "Завтра новый шанс. Ты всё равно молодец 🦥")
            )
            await send_mood_message(bot, max_id, text, n,
                                    buttons=_tasks_buttons(day, completed))
        except Exception:
            logger.exception(f"Evening job error for MAX user {max_id}")


def setup_scheduler():
    """Инициализирует планировщик с задачами MAX."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    # Запускаем каждый час — внутри джоб сам проверяет час пользователя
    _scheduler.add_job(_job_morning,   "cron", hour="*", minute=0,  id="max_morning")
    _scheduler.add_job(_job_afternoon, "cron", hour="*", minute=5,  id="max_afternoon")
    _scheduler.add_job(_job_evening,   "cron", hour="*", minute=10, id="max_evening")
    _scheduler.start()
    logger.info("MAX планировщик запущен")


# ── Инициализация вебхука ─────────────────────────────────────

async def setup(webapp_base_url: str):
    """Регистрирует вебхук. Вызывается при старте webapp_server."""
    if not MAX_PROGRAM_TOKEN:
        logger.warning("MAX_PROGRAM_BOT_TOKEN не задан — MAX основной бот не запущен")
        return
    bot = get_client()
    me = await bot.get_me()
    logger.info(f"MAX программный бот: {me.get('name', '?')} (@{me.get('username', '?')})")
    webhook_url = f"{webapp_base_url.rstrip('/')}{WEBHOOK_PATH}"
    await bot.setup_webhook(webhook_url)
    logger.info(f"MAX программный бот вебхук: {webhook_url}")
    setup_scheduler()
