"""
max_program_bot.py — основной бот 77 Soft Challenge для Мессенджера MAX
                     (полный аналог @Zarik_Lazy_Bot).

Переменные окружения:
  MAX_PROGRAM_BOT_TOKEN    — токен основного бота в MAX
  MAX_ADMIN_USER_ID        — MAX user_id администратора
  MAX_LEAD_BOT_URL         — ссылка на MAX лид-бот (для сообщения «оплаты нет»)
  WEBAPP_URL               — URL мини-аппа
  PAYMENT_URL              — URL страницы оплаты
  MAX_TEST_USER_IDS        — MAX user IDs тест-юзеров (через запятую)
  MAX_PROGRAM_WEBHOOK_PATH — путь вебхука, по умолчанию /webhook/max-program
"""
import asyncio
import hashlib
import hmac as _hmac
import logging
import os
import re
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

from max_client import MaxClient, _btn_callback, _btn_link
import database as db
import content as ct
from workout import get_workout

logger = logging.getLogger(__name__)

# ── Конфигурация ──────────────────────────────────────────────
MAX_PROGRAM_TOKEN   = os.environ.get("MAX_PROGRAM_BOT_TOKEN", "")
MAX_ADMIN_USER_ID   = int(os.environ.get("MAX_ADMIN_USER_ID", "0"))
MAX_LEAD_BOT_URL    = os.environ.get("MAX_LEAD_BOT_URL", "")
WEBAPP_URL          = os.environ.get("WEBAPP_URL", "")
PAYMENT_URL         = os.environ.get("PAYMENT_URL", "")
WEBHOOK_PATH        = os.environ.get("MAX_PROGRAM_WEBHOOK_PATH", "/webhook/max-program")

HAPPY_IMG      = Path(__file__).parent / "Happy.png"
NORM_IMG       = Path(__file__).parent / "Norm.png"
SAD_IMG        = Path(__file__).parent / "Sad.png"
BEFORE_EXAMPLE = Path(__file__).parent / "before_example.jpg"

_test_ids_raw = os.environ.get("MAX_TEST_USER_IDS", "")
MAX_TEST_USER_IDS = {int(x) for x in _test_ids_raw.split(",") if x.strip().isdigit()}

TOTAL_DAYS    = 77
TASKS_PER_DAY = 5
WEEKLY_MILESTONE_DAYS = {7, 14, 21, 28, 35, 42, 49, 56, 63, 70, 77}

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
    "💧 Вода — будем восстанавливать и удерживать баланс\n"
    "📚 Чтение — качнем мозги\n"
    "🥗 Питание — уберем лишнее\n"
    "🚫 Алкоголь — разберемся и с этим)\n\n"
    "Каждый выполненный день поднимает тебя в топ планеты. "
    "77 дней — и ты в другой жизни.\n\n"
    "Сейчас задам несколько вопросов, чтобы собрать нужную информацию "
    "для комфортного старта. Займёт меньше минуты 🦥"
)

NOT_PAID_TEXT = (
    "🦥 Участие в программе не оплачено.\n\n"
    "Чтобы начать — сначала оформи участие:"
)


# ── Утилиты ───────────────────────────────────────────────────

def _md(text: str) -> str:
    """Конвертирует TG markdown (*bold*) → MAX markdown (**bold**)."""
    return re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'**\1**', text)


def _make_miniapp_url(internal_uid: int) -> str:
    """Генерирует подписанный URL для мини-аппа (MAX-версия, подпись MAX токеном)."""
    if not WEBAPP_URL or not MAX_PROGRAM_TOKEN:
        return f"{WEBAPP_URL}?uid={internal_uid}" if WEBAPP_URL else ""
    ts  = int(_time.time())
    sig = _hmac.new(
        MAX_PROGRAM_TOKEN.encode(),
        f"{internal_uid}:{ts}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{WEBAPP_URL}?uid={internal_uid}&ts={ts}&sig={sig}&platform=max"


def get_rank(days_done: int) -> str:
    if days_done >= 63: return "🏆 Легенда"
    if days_done >= 49: return "💎 Мастер"
    if days_done >= 42: return "🦅 В полёте"
    if days_done >= 35: return "🎯 На полпути"
    if days_done >= 28: return "🔥 В огне"
    if days_done >= 21: return "⚡ В потоке"
    if days_done >= 14: return "💪 Входит в ритм"
    if days_done >= 7:  return "🚀 Стартовал"
    return "🌱 Новичок"


def day_word(n: int) -> str:
    if n == 1: return "день"
    if 2 <= n <= 4: return "дня"
    return "дней"


def make_mini_bar(value: int, total: int, width: int = 15) -> str:
    if total == 0:
        return "·" * width
    filled = round(value / total * width)
    return "●" * filled + "·" * (width - filled)


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


def _main_menu_buttons(max_user_id: int, uid: int = 0) -> list[list[dict]]:
    buttons = [[_btn_callback("📋 Сегодня", "menu:today")]]
    if WEBAPP_URL:
        url = _make_miniapp_url(uid) if uid else f"{WEBAPP_URL}?uid={max_user_id}"
        buttons.append([_btn_link("📱 Мини-апп", url)])
    buttons.append([_btn_callback("📊 Прогресс", "menu:stats")])
    buttons.append([_btn_callback("📅 Неделя", "menu:week")])
    buttons.append([_btn_callback("🏆 Ачивки", "menu:achievements")])
    return buttons


def _photo_buttons() -> list[list[dict]]:
    return [
        [_btn_callback("📸 Да, хочу!", "photo_yes")],
        [_btn_callback("➡️ Нет, пропустить", "photo_no")],
    ]


def _photos_done_buttons() -> list[list[dict]]:
    return [
        [_btn_callback("✅ Готово, все фото отправил(а)", "photos_done")],
        [_btn_callback("➡️ Пропустить этот шаг", "photo_no")],
    ]


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


def build_today_text(day: int, completed: set) -> str:
    """Краткий заголовок трекера — только призыв к действию."""
    return f"**День {day} из {TOTAL_DAYS} · 📋 Отметь что выполнил сегодня 👇**"


def build_tasks_list_max(uid: int, day: int) -> str:
    """Задания на день с описанием тренировки."""
    user_row = db.get_user(uid)
    if not user_row:
        return f"**📋 Задания на день {day}**"
    try:
        workout = get_workout(dict(user_row), day)
        workout_desc = workout.get("description", "Тренировка по плану")
    except Exception:
        workout_desc = "Тренировка по плану"
    lines = [
        f"**📋 Задания на день {day}:**",
        "",
        f"💪 **Тренировка на сегодня:**\n{workout_desc}",
        "",
        "💧 **Цель по воде:** 2 литра / 8 стаканов",
        "📚 **Цель по чтению:** 10 страниц",
        "🥗 Без фастфуда сегодня",
        "🚫 Без алкоголя",
    ]
    return "\n".join(lines)


def build_stats_text(uid: int) -> str:
    """Экран прогресса — полный аналог Telegram-версии."""
    stats     = db.get_stats(uid)
    day       = stats["current_day"]
    done      = stats["days_completed"]
    streak    = stats["streak"]
    percentile, pct_ctx = ct.get_planet_percentile(done)
    next_m    = ct.get_next_percentile_milestone(done)

    bar_width = 17
    filled    = round(done / TOTAL_DAYS * bar_width)
    pct       = round(done / TOTAL_DAYS * 100)
    bar       = "●" * filled + "·" * (bar_width - filled)

    week_now = (day - 1) // 7 + 1
    weeks = ""
    for w in range(1, 12):
        if w < week_now:
            weeks += "✅"
        elif w == week_now:
            weeks += "🔥"
        else:
            weeks += "⬜"

    if next_m:
        d, pct_next = next_m
        goal_line = f"Ещё **{d}** {day_word(d)} → {pct_next} 🌍"
    else:
        goal_line = "Ты достиг максимального рейтинга! 🏆"

    if streak >= 7:
        streak_label = f"**{streak}** {day_word(streak)} подряд 🔥"
    elif streak >= 3:
        streak_label = f"**{streak}** {day_word(streak)} подряд 💪"
    elif streak == 0:
        streak_label = "**0** — начни сегодня!"
    else:
        streak_label = f"**{streak}** {day_word(streak)} подряд"

    rank = get_rank(done)

    lines = [
        f"**{rank}**",
        "",
        f"День **{day}** из {TOTAL_DAYS}",
        f"`{bar}`  {pct}%",
        "",
        f"✅ Засчитано: **{done}** {day_word(done)}",
        f"🔥 Серия: {streak_label}",
        f"🌍 Рейтинг: **{percentile}** планеты",
        "",
        f"🎯 {goal_line}",
        "",
        "📅 Путь по неделям:",
        weeks,
        "",
        f"_{pct_ctx}_",
    ]
    return "\n".join(lines)


def build_week_screen_max(uid: int) -> str:
    """Экран «Неделя» — итоги текущей недели и группы."""
    import re as _re
    stats = db.get_stats(uid)
    day = stats["current_day"]
    done = stats["days_completed"]
    week_num = (day - 1) // 7 + 1
    week_start = (week_num - 1) * 7 + 1
    all_compl = db.get_completed_days_set(uid)
    week_done = len({d for d in all_compl if week_start <= d <= day})
    percentile, ctx = ct.get_planet_percentile(done)
    week_bar = make_mini_bar(week_done, 7)

    raw_header = ct.get_weekly_header(week_num)
    header = _re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'**\1**', raw_header)

    group = db.get_group_stats()

    lines = [
        f"**📅  Неделя {week_num} · {week_done} из 7 дней**",
        "",
        header,
        "",
        f"Эта неделя:  {week_bar}  {week_done} / 7",
        f"Всего засчитано:  **{done}** из {day} дней",
        f"🌍  **{percentile}** планеты",
    ]

    next_m = ct.get_next_percentile_milestone(done)
    if next_m:
        d, pct = next_m
        lines.append(f"      _ещё {d} {day_word(d)} → {pct}_")

    if group and group.get("total", 0) > 0:
        lines += [
            "",
            f"👥  Группа:           {group['total']} участников",
            f"🏃  Продолжают:   **{group['active']}**",
        ]

    return "\n".join(lines)


def build_achievements_text(uid: int) -> str:
    """Экран ачивок."""
    lines = ["**🏆  Твои ачивки**", ""]
    for ach_id, threshold in ct.ACHIEVEMENT_ORDER:
        ach      = ct.ACHIEVEMENTS[ach_id]
        unlocked = db.has_achievement(uid, ach_id)
        mark     = "✅" if unlocked else "⬜"
        name     = ach["name"]
        if unlocked:
            lines.append(f"{mark}  {ach['icon']}  **День {threshold} — {name}**")
        else:
            lines.append(f"{mark}  {ach['icon']}  _День {threshold} — {name}_")
    return "\n".join(lines)


def build_weekly_milestone_text(uid: int) -> str:
    """Итог недели для milestone-дней (7, 14, 21 ...)."""
    stats     = db.get_stats(uid)
    day       = stats["current_day"]
    week_num  = (day - 1) // 7 + 1
    user_row  = db.get_user(uid)
    all_compl = db.get_completed_days_set(uid)
    week_start = (week_num - 1) * 7 + 1
    week_end   = week_num * 7

    total_pushups = total_squats = total_abs = 0
    week_pushups  = week_squats  = week_abs  = w_train = 0
    for d in all_compl:
        tasks = db.get_completed_tasks(uid, d)
        if 0 in tasks and user_row:
            try:
                w = get_workout(dict(user_row), d)
                total_pushups += w["pushup"]["total"]
                total_squats  += w["squat"]["total"]
                total_abs     += w["abs"]["total"]
                if week_start <= d <= week_end:
                    week_pushups += w["pushup"]["total"]
                    week_squats  += w["squat"]["total"]
                    week_abs     += w["abs"]["total"]
                    w_train      += 1
            except Exception:
                pass

    weekly_task_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    for d in all_compl:
        if week_start <= d <= week_end:
            for t in db.get_completed_tasks(uid, d):
                if t in weekly_task_counts:
                    weekly_task_counts[t] += 1

    user_counts = db.get_task_completion_counts(uid)

    try:
        header_text = ct.format_weekly_header(
            week_num,
            train=w_train,
            pushups=week_pushups,
            abs=week_abs,
            squats=week_squats,
            water=weekly_task_counts[1] * 2,
            pages=weekly_task_counts[2] * 20,
            nojunk=weekly_task_counts[3],
            noalc=weekly_task_counts[4],
            total_pushups=total_pushups,
            total_abs=total_abs,
            total_squats=total_squats,
            total_water=user_counts.get(1, 0) * 2,
            total_pages=user_counts.get(2, 0) * 20,
            total_nojunk=user_counts.get(3, 0),
            total_noalc=user_counts.get(4, 0),
        )
        # Конвертируем Telegram markdown (*bold*) → MAX markdown (**bold**)
        import re
        header_text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'**\1**', header_text)
    except Exception:
        week_done   = len({d for d in all_compl if week_start <= d <= week_end})
        header_text = f"**Неделя {week_num} завершена!** {week_done} из 7 дней ✅"

    group = db.get_group_stats()
    lines = [header_text]
    if group and group.get("total", 0) > 0:
        lines += [
            "",
            f"👥  Группа:      {group['total']} участников",
            f"🏃  Продолжают:  **{group['active']}**",
        ]
    return "\n".join(lines)


# ── Онбординг ─────────────────────────────────────────────────

async def _send_completion_message(bot: MaxClient, max_user_id: int, uid: int):
    """Финальное сообщение после завершения онбординга."""
    await bot.send_message(
        max_user_id,
        "Отлично, твоя программа сформирована под тебя 🎯\n\n"
        "Я пока пошёл дальше висеть на ветке, а с тобой свяжусь завтра утром 🦥\n"
        "А пока — отдыхай)",
        buttons=_main_menu_buttons(max_user_id, uid),
    )


async def start_onboarding(bot: MaxClient, max_user_id: int, uid: int):
    db.set_onboarding_step(uid, "timezone")
    await bot.send_message(
        max_user_id,
        "🦥 Шаг 1 из 4 · Часовой пояс\n\n"
        "**Выбери свой часовой пояс** — буду присылать задания в 6:00 по твоему времени 👇",
        buttons=_timezone_buttons(),
    )


async def handle_onboarding_callback(bot: MaxClient, max_user_id: int, uid: int,
                                     callback_id: str, payload: str):
    step = db.get_onboarding_step(uid)

    # ── Шаг 1: Часовой пояс ──────────────────────────────────
    if step == "timezone" and payload.startswith("tz:"):
        tz = payload[3:]
        db.set_user_timezone(uid, tz)
        await bot.answer_callback(callback_id)
        tz_label = next((l for l, t in TIMEZONES if t == tz), tz)
        await bot.send_message(max_user_id, f"✅ Часовой пояс: {tz_label}")
        await bot.send_message(
            max_user_id,
            "🦥 Теперь давай подберём тренировку под тебя.\n\n"
            "Сколько отжиманий можешь сделать прямо сейчас?",
            buttons=_digits_buttons("pushup"),
        )

    # ── Шаг 2: Отжимания ─────────────────────────────────────
    elif step == "pushup" and payload.startswith("pushup:"):
        val = int(payload.split(":")[1])
        db.save_pushup_start(uid, val)
        await bot.answer_callback(callback_id)
        await bot.send_message(
            max_user_id,
            f"💪 Отжимания: {val} — записал!\n\nСколько приседаний?",
            buttons=_digits_buttons("squat"),
        )

    # ── Шаг 2: Приседания ────────────────────────────────────
    elif step == "squat" and payload.startswith("squat:"):
        val = int(payload.split(":")[1])
        db.save_squat_start(uid, val)
        await bot.answer_callback(callback_id)
        await bot.send_message(
            max_user_id,
            f"🦵 Приседания: {val} — отлично!\n\nСколько раз пресс?",
            buttons=_digits_buttons("abs"),
        )

    # ── Шаг 3: Пресс → переход к фото ───────────────────────
    elif step in ("abs", "photo") and payload.startswith("abs:"):
        val = int(payload.split(":")[1])
        db.save_abs_start(uid, val)
        await bot.answer_callback(callback_id)
        await bot.send_message(
            max_user_id,
            f"🔥 Пресс: {val} — красава!\n\n"
            "🦥 Шаг 4 из 4 · Фото до/после\n\n"
            "Хочешь делиться результатами до/после? "
            "Это поможет увидеть свой прогресс за 77 дней.",
            buttons=_photo_buttons(),
        )

    # ── Шаг 4: Фото — да ────────────────────────────────────
    elif payload == "photo_yes":
        await bot.answer_callback(callback_id)
        db.set_share_photos(uid, True)
        db.set_onboarding_step(uid, "awaiting_photos")
        await bot.send_message(max_user_id, "📸 Отлично! Сейчас объясню что нужно сделать 👇")
        instruction = (
            "Сделай 2 фото «до»:\n"
            "• Фронтальное (анфас)\n"
            "• Боковое (профиль)\n\n"
            "На фото закрой лицо листом бумаги с датой старта в формате число/месяц/год "
            "и надписью «Для Зарика» ✍️\n\n"
            "👗 Девушки — купальник или нижнее бельё\n"
            "🩳 Парни — шорты или трусы\n\n"
            "Надевайте что вам комфортнее, но в рамках приличия "
            "(чтобы я, Зарик, не поплыл — я чувствительный 🦥)\n\n"
            "Отправляй фото сюда — я сохраню 👇\n"
            "Когда закончишь — нажми кнопку ниже.\n\n"
            "🔒 Мы не делимся твоими фото, не выкладываем их никуда — это только для тебя."
        )
        if BEFORE_EXAMPLE.exists():
            await bot.send_photo(
                max_user_id, BEFORE_EXAMPLE,
                caption=instruction,
                buttons=_photos_done_buttons(),
            )
        else:
            await bot.send_message(max_user_id, instruction, buttons=_photos_done_buttons())

    # ── Шаг 4: Фото — нет ───────────────────────────────────
    elif payload == "photo_no":
        await bot.answer_callback(callback_id)
        db.set_share_photos(uid, False)
        db.complete_onboarding(uid)
        await bot.send_message(max_user_id, "👌 Понял, без фото — тоже отлично!")
        await _send_completion_message(bot, max_user_id, uid)

    # ── Шаг 4: Фото отправлены ──────────────────────────────
    elif payload == "photos_done":
        await bot.answer_callback(callback_id)
        count = db.count_user_photos(uid, "before")
        if count < 1:
            await bot.send_message(
                max_user_id,
                "📸 Фото пока не получено.\n\n"
                "Отправь 2 фото в чат (анфас и профиль) "
                "или пропусти этот шаг, если не хочешь делиться фото 👇",
                buttons=_photos_done_buttons(),
            )
            return
        db.complete_onboarding(uid)
        noun = "фото" if count in (2, 3, 4) else "фото"
        await bot.send_message(max_user_id, f"✅ Сохранил {count} {noun} 📸")
        await _send_completion_message(bot, max_user_id, uid)


# ── Дневной экран ─────────────────────────────────────────────

async def show_today(bot: MaxClient, max_user_id: int, uid: int):
    day = db.get_current_day(uid)
    if day == 0:
        user = db.get_user(uid)
        start = user["start_date"] if user else "завтра"
        await bot.send_message(
            max_user_id,
            f"🦥 Программа стартует **{start}**.\n\nЗагляни сюда утром первого дня!",
            buttons=_main_menu_buttons(max_user_id, uid),
        )
        return
    if day > TOTAL_DAYS:
        await bot.send_message(
            max_user_id,
            "🏆 Ты прошёл все 77 дней! Это легенда! 🦥",
            buttons=_main_menu_buttons(max_user_id, uid),
        )
        return
    completed = set(db.get_completed_tasks(uid, day))
    text = build_today_text(day, completed)
    await bot.send_message(max_user_id, text, buttons=_tasks_buttons(day, completed))


# ── Обработчики callback ──────────────────────────────────────

async def on_callback(max_user_id: int, callback_id: str, payload: str,
                      username: str, first_name: str, message_id: str = ""):
    bot = get_client()
    uid = db.get_or_create_max_user(max_user_id, username, first_name)
    db.log_user_session(uid)
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
        db.toggle_task(uid, day, idx)
        if db.has_dropout_warning(uid):
            db.clear_dropout_warning(uid)
        completed = set(db.get_completed_tasks(uid, day))
        all_done  = len(completed) == TASKS_PER_DAY

        text    = build_today_text(day, completed)
        buttons = _tasks_buttons(day, completed)

        # Редактируем трекер на месте (или шлём новый если message_id нет)
        if message_id:
            await bot.edit_message(message_id, text, buttons=buttons)
        else:
            await bot.send_message(max_user_id, text, buttons=buttons)

        if all_done:
            evening_text = _md(ct.get_evening(day, all_done=True))
            celebrate_text = f"{evening_text}\n\n_День {day} засчитан! 🎉_"
            await send_mood_message(bot, max_user_id, celebrate_text, 5)

            # Ачивки
            stats = db.get_stats(uid)
            new_achievements = ct.check_achievements(stats["days_completed"])
            for ach_id in new_achievements:
                if not db.has_achievement(uid, ach_id):
                    db.award_achievement(uid, ach_id)
                    await bot.send_message(max_user_id, _md(ct.get_achievement_text(ach_id)))

            # Финальное сообщение
            if day == TOTAL_DAYS:
                await bot.send_message(max_user_id, _md(ct.FINAL_MESSAGE))

    # Меню
    elif payload == "menu:today":
        if not db.is_program_started(uid):
            await bot.send_message(
                max_user_id,
                "🦥 Программа ещё не началась — ждём завтра в 6:00!\n\n"
                "Загляни сюда утром первого дня 👋",
                buttons=_main_menu_buttons(max_user_id, uid),
            )
            return
        await show_today(bot, max_user_id, uid)

    elif payload == "menu:stats":
        if not db.is_program_started(uid):
            await bot.send_message(
                max_user_id,
                "Ишь хитрюга)) Вот завтра начнём, тогда и прогресс появится 😄",
                buttons=_main_menu_buttons(max_user_id, uid),
            )
            return
        text = build_stats_text(uid)
        await bot.send_message(max_user_id, text, buttons=_main_menu_buttons(max_user_id))

    elif payload == "menu:week":
        if not db.is_program_started(uid):
            await bot.send_message(
                max_user_id,
                "Ишь хитрюга)) Вот завтра начнём, тогда и прогресс появится 😄",
                buttons=_main_menu_buttons(max_user_id, uid),
            )
            return
        text = build_week_screen_max(uid)
        await bot.send_message(max_user_id, text, buttons=_main_menu_buttons(max_user_id))

    elif payload == "menu:achievements":
        text = build_achievements_text(uid)
        await bot.send_message(max_user_id, text, buttons=_main_menu_buttons(max_user_id))

    elif payload == "onboard:start":
        await start_onboarding(bot, max_user_id, uid)

    elif (payload.startswith("tz:") or payload.startswith("pushup:") or
          payload.startswith("squat:") or payload.startswith("abs:") or
          payload in ("photo_yes", "photo_no", "photos_done")):
        await handle_onboarding_callback(bot, max_user_id, uid, callback_id, payload)


# ── Обработчик текстовых сообщений ───────────────────────────

async def on_message(max_user_id: int, text: str, username: str, first_name: str,
                     has_photo: bool = False, photo_token: str = ""):
    bot = get_client()
    uid = db.get_or_create_max_user(max_user_id, username, first_name)
    db.log_user_session(uid)
    text = (text or "").strip()

    # ── Фото во время онбординга ──────────────────────────────
    if has_photo:
        step = db.get_onboarding_step(uid)
        if step == "awaiting_photos":
            db.save_user_photo(uid, "before", photo_token or f"max_{max_user_id}")
            count = db.count_user_photos(uid, "before")
            await bot.send_message(
                max_user_id,
                f"📸 Фото {count} сохранено!\n\nКогда отправишь все — нажми кнопку 👇",
                buttons=_photos_done_buttons(),
            )
            return

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

        if text.startswith("/admin"):
            count = db.get_user_count()
            total_stake = db.get_total_stake() // 100
            active = db.get_all_active_users()
            lines = [
                "🦥 Админ · Сводка", "",
                f"👥 Участников: {count}",
                f"🏃 Активных: {len(active)}",
                f"💰 Ставки: {total_stake:,} ₽".replace(",", " "),
            ]
            await bot.send_message(max_user_id, "\n".join(lines))
            return

        if text.startswith("/stats"):
            stats = db.get_session_stats()

            first_hours, last_hours = [], []
            for s in stats["raw_sessions"]:
                tz_name = s.get("timezone") or "Europe/Moscow"
                try:
                    import pytz as _pytz
                    tz = _pytz.timezone(tz_name)
                    for field, bucket in (
                        (s["first_open_utc"], first_hours),
                        (s["last_open_utc"], last_hours),
                    ):
                        if field:
                            utc_dt = datetime.fromisoformat(field).replace(tzinfo=_pytz.utc)
                            local_dt = utc_dt.astimezone(tz)
                            bucket.append(local_dt.hour + local_dt.minute / 60)
                except Exception:
                    pass

            def fmt_hour(hours):
                if not hours:
                    return "—"
                avg = sum(hours) / len(hours)
                h_part, m_part = int(avg), int((avg % 1) * 60)
                return f"{h_part:02d}:{m_part:02d}"

            top_lines = ""
            for i, u in enumerate(stats["top_users"], 1):
                name = f"@{u['username']}" if u.get("username") else f"id{u['user_id']}"
                top_lines += f"\n  {i}. {name} — {u['total']} обращений"

            msg = (
                "📊 **Активность · MAX бот**\n\n"
                f"👥 Активных сегодня: **{stats['active_today']}**\n"
                f"👥 Активных за 7 дней: **{stats['active_users_7d']}**\n\n"
                "📅 **Средние показатели за 7 дней:**\n"
                f"• Обращений в день: **{stats['avg_interactions']}**\n"
                f"• Продолжительность сессии: **{stats['avg_session_min']} мин**\n"
                f"• Активных дней из 7: **{stats['avg_active_days']}**\n\n"
                "⏰ **Время по местному часовому поясу:**\n"
                f"• Первое открытие: **{fmt_hour(first_hours)}**\n"
                f"• Последнее открытие: **{fmt_hour(last_hours)}**\n"
            )
            if top_lines:
                msg += f"\n🔥 **Топ-5 активных (7 дней):**{top_lines}"

            await bot.send_message(max_user_id, msg)
            return

        if text.startswith("/screen"):
            if not db.is_payment_confirmed(uid):
                await bot.send_message(max_user_id, "Сначала /grant")
                return
            if not db.is_program_started(uid):
                await bot.send_message(max_user_id, "Программа не началась. Используй /setday 1")
                return
            day = db.get_current_day(uid)
            completed = set(db.get_completed_tasks(uid, day))
            await bot.send_message(
                max_user_id,
                build_today_text(day, completed),
                buttons=_tasks_buttons(day, completed),
            )
            return

        if text.startswith("/setday"):
            parts = text.split()
            # Форматы: /setday N (для себя) или /setday max_user_id N (для другого)
            if len(parts) == 2 and parts[1].isdigit():
                target_max_id = max_user_id
                target_day    = int(parts[1])
                target_uid    = uid
            elif len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                target_max_id = int(parts[1])
                target_day    = int(parts[2])
                target_uid    = db.get_max_internal_id(target_max_id)
            else:
                await bot.send_message(max_user_id, "Использование: /setday 7  или  /setday max_id 7")
                return

            if not target_uid:
                await bot.send_message(max_user_id, f"❌ MAX-пользователь {target_max_id} не найден")
                return

            db.set_day_for_testing(target_uid, target_day)
            completed = set(db.get_completed_tasks(target_uid, target_day))
            partial   = {0}   # только тренировка — для примера частичного выполнения

            await bot.send_message(max_user_id,
                f"🛠 **DEV · День {target_day} из {TOTAL_DAYS}**")

            # ☀️ 6:00 — мотивация + задания + трекер
            await bot.send_message(target_max_id,
                _md(ct.get_morning(target_day)))
            await bot.send_message(target_max_id,
                build_tasks_list_max(target_uid, target_day))
            await bot.send_message(target_max_id,
                build_today_text(target_day, completed),
                buttons=_tasks_buttons(target_day, completed))

            # 🌤 14:00 — 0 галочек
            await bot.send_message(target_max_id,
                f"_— 14:00 · ноль галочек —_\n\n"
                + _md(ct.get_afternoon_smart(target_day, set())))
            # 🌤 14:00 — частично
            await bot.send_message(target_max_id,
                f"_— 14:00 · частично (только тренировка) —_\n\n"
                + _md(ct.get_afternoon_smart(target_day, partial)))
            # 🌤 14:00 — все выполнено
            await bot.send_message(target_max_id,
                f"_— 14:00 · все галочки —_\n\n"
                + _md(ct.get_afternoon_smart(target_day, {0, 1, 2, 3, 4})))

            # 🌙 21:00 — 0 галочек
            await bot.send_message(target_max_id,
                f"_— 21:00 · ноль галочек —_\n\n"
                + _md(ct.get_evening_smart(target_day, set())))
            # 🌙 21:00 — частично
            await bot.send_message(target_max_id,
                f"_— 21:00 · частично (только тренировка) —_\n\n"
                + _md(ct.get_evening_smart(target_day, partial)))
            # 🌙 21:00 — все выполнено
            await bot.send_message(target_max_id,
                f"_— 21:00 · все галочки —_\n\n"
                + _md(ct.get_evening_smart(target_day, {0, 1, 2, 3, 4})))

            if target_day in WEEKLY_MILESTONE_DAYS:
                await bot.send_message(target_max_id,
                    build_weekly_milestone_text(target_uid))

            await bot.send_message(max_user_id,
                f"✅ День {target_day} установлен для MAX {target_max_id}")
            return

        if text.startswith("/debug"):
            parts = text.split()
            target_max_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else max_user_id
            target_uid = db.get_max_internal_id(target_max_id) or uid
            user_row = db.get_user(target_uid)
            step     = db.get_onboarding_step(target_uid)
            paid     = db.is_payment_confirmed(target_uid)
            started  = db.is_program_started(target_uid)
            day      = db.get_current_day(target_uid) if started else "—"
            await bot.send_message(
                max_user_id,
                f"🛠 **Debug MAX**\n\n"
                f"max_user_id: `{target_max_id}`\n"
                f"internal_uid: `{target_uid}`\n"
                f"paid: {paid}\n"
                f"onboarding: {step}\n"
                f"started: {started}\n"
                f"day: {day}"
            )
            return

    # ── Обычные пользователи ──────────────────────────────────
    cmd = text.lower().split()[0] if text else ""

    if cmd in ("/start", "start", "старт"):
        await _handle_start(bot, max_user_id, uid, username, first_name)
        return

    if cmd in ("/myid", "myid"):
        await bot.send_message(max_user_id, f"Твой MAX user\\_id: `{max_user_id}`")
        return

    if cmd in ("/help", "помощь"):
        await bot.send_message(
            max_user_id,
            "🦥 **Помощь**\n\n"
            "Напиши **старт** или **сегодня** — покажу твои задачи.\n"
            "Напиши **прогресс** или **статистика** — покажу прогресс.\n"
            "Напиши **ачивки** — покажу достижения.\n\n"
            "Используй кнопки меню 👇",
            buttons=_main_menu_buttons(max_user_id, uid),
        )
        return

    if cmd in ("сегодня", "задачи"):
        _u2 = db.get_user(uid)
        if db.is_payment_confirmed(uid) and (_u2 and _u2["onboarding_complete"]):
            if not db.is_program_started(uid):
                await bot.send_message(
                    max_user_id,
                    "🦥 Программа ещё не началась — ждём завтра в 6:00!\n\n"
                    "Загляни сюда утром первого дня 👋",
                    buttons=_main_menu_buttons(max_user_id, uid),
                )
                return
            await show_today(bot, max_user_id, uid)
        return

    if cmd in ("прогресс", "статистика", "итоги"):
        if db.is_payment_confirmed(uid):
            if not db.is_program_started(uid):
                await bot.send_message(
                    max_user_id,
                    "Ишь хитрюга)) Вот завтра начнём, тогда и прогресс появится 😄",
                    buttons=_main_menu_buttons(max_user_id, uid),
                )
                return
            text_out = build_stats_text(uid)
            await bot.send_message(max_user_id, text_out, buttons=_main_menu_buttons(max_user_id))
        return

    if cmd == "ачивки":
        if db.is_payment_confirmed(uid):
            await bot.send_message(max_user_id, build_achievements_text(uid),
                                   buttons=_main_menu_buttons(max_user_id))
        return


async def _handle_start(bot: MaxClient, max_user_id: int, uid: int,
                         username: str, first_name: str):
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
    if onboarding_done:
        await show_today(bot, max_user_id, uid)
    else:
        step = db.get_onboarding_step(uid)
        if step == "welcome":
            await bot.send_message(
                max_user_id, WELCOME_TEXT,
                buttons=[[_btn_callback("Поехали ▶️", "onboard:start")]]
            )
        elif step == "timezone":
            await bot.send_message(
                max_user_id,
                "🦥 Шаг 1 из 3 · Продолжаем\n\n**Выбери свой часовой пояс 👇**",
                buttons=_timezone_buttons()
            )
        elif step == "pushup":
            await bot.send_message(
                max_user_id,
                "🦥 Шаг 2 из 3 · Продолжаем\n\n**Сколько отжиманий?**",
                buttons=_digits_buttons("pushup")
            )
        elif step == "squat":
            await bot.send_message(
                max_user_id,
                "🦥 Продолжаем\n\n**Сколько приседаний?**",
                buttons=_digits_buttons("squat")
            )
        elif step == "abs":
            await bot.send_message(
                max_user_id,
                "🦥 Продолжаем\n\n**Сколько подъёмов корпуса?**",
                buttons=_digits_buttons("abs")
            )
        elif step == "photo":
            await bot.send_message(
                max_user_id,
                "🦥 Шаг 4 из 4 · Фото до/после\n\n"
                "**Хочешь делиться результатами до/после?** "
                "Это поможет увидеть прогресс за 77 дней.",
                buttons=_photo_buttons()
            )
        elif step == "awaiting_photos":
            count = db.count_user_photos(uid, "before")
            saved = f" Уже сохранено: {count} фото." if count else ""
            await bot.send_message(
                max_user_id,
                f"📸 Жду твои фото «до».{saved}\n\nКогда всё отправишь — нажми кнопку 👇",
                buttons=_photos_done_buttons()
            )
        else:
            await bot.send_message(
                max_user_id, WELCOME_TEXT,
                buttons=[[_btn_callback("Поехали ▶️", "onboard:start")]]
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
            msg         = data.get("message", {})
            sender      = msg.get("sender", {})
            text        = msg.get("body", {}).get("text", "") or ""
            attachments = msg.get("body", {}).get("attachments", []) or []
            has_photo   = any(a.get("type") == "image" for a in attachments)
            photo_token = ""
            if has_photo:
                for att in attachments:
                    if att.get("type") == "image":
                        photo_token = att.get("payload", {}).get("token", "")
                        break
            await on_message(
                max_user_id=sender.get("user_id", 0),
                text=text,
                username=sender.get("username", ""),
                first_name=sender.get("name", ""),
                has_photo=has_photo,
                photo_token=photo_token,
            )

        elif update_type == "message_callback":
            cb         = data.get("callback", {})
            user       = cb.get("user", {})
            message_id = cb.get("message", {}).get("message_id", "")
            await on_callback(
                max_user_id=user.get("user_id", 0),
                callback_id=cb.get("callback_id", ""),
                payload=cb.get("payload", ""),
                username=user.get("username", ""),
                first_name=user.get("name", ""),
                message_id=message_id,
            )

    except Exception:
        logger.exception(f"Error processing MAX program update: {update_type}")


# ── Планировщик ───────────────────────────────────────────────

async def _job_morning():
    """6:00 — утреннее сообщение: мотивация + задания + трекер."""
    bot   = get_client()
    users = db.get_all_active_users()
    for user in users:
        uid    = user["user_id"]
        max_id = db.get_max_user_id_by_internal(uid)
        if max_id is None:
            continue
        try:
            tz        = pytz.timezone(user["timezone"] or "Europe/Moscow")
            now_local = datetime.now(tz)
            if now_local.hour != 6:
                continue
            if not db.is_program_started(uid):
                continue
            day = db.get_current_day(uid)
            if day < 1 or day > TOTAL_DAYS:
                continue
            completed = set(db.get_completed_tasks(uid, day))
            if len(completed) >= 5:
                continue

            missed = db.get_missed_streak(uid)

            # Выбывание
            if db.should_dropout(uid):
                last_day = db.get_last_completed_day(uid)
                await bot.send_message(max_id, ct.get_dropout_message(last_day))
                db.deactivate_user(uid)
                logger.info(f"MAX участник {max_id} выбыл")
                continue

            # Предупреждение о пропуске
            if missed >= 3 and not db.has_dropout_warning(uid):
                last_day = db.get_last_completed_day(uid)
                await bot.send_message(max_id, ct.get_miss_message(3, last_day))
                db.set_dropout_warning_sent(uid)
                continue

            # 1. Мотивационное послание
            morning_msg = _md(ct.get_morning(day))
            if 1 <= missed <= 2:
                last_day = db.get_last_completed_day(uid)
                morning_msg += f"\n\n{_md(ct.get_miss_message(missed, last_day))}"
            await bot.send_message(max_id, morning_msg)

            # 2. Задания на день с описанием тренировки
            await bot.send_message(max_id, build_tasks_list_max(uid, day))

            # 3. Трекер с галочками
            await bot.send_message(
                max_id,
                build_today_text(day, completed),
                buttons=_tasks_buttons(day, completed),
            )
        except Exception:
            logger.exception(f"Morning job error for MAX user {max_id}")


async def _job_afternoon():
    """14:00 — дневная проверка с умным сообщением и mood-картинкой."""
    bot   = get_client()
    users = db.get_all_active_users()
    for user in users:
        uid    = user["user_id"]
        max_id = db.get_max_user_id_by_internal(uid)
        if max_id is None:
            continue
        try:
            tz        = pytz.timezone(user["timezone"] or "Europe/Moscow")
            now_local = datetime.now(tz)
            if now_local.hour != 14:
                continue
            if not db.is_program_started(uid):
                continue
            day = db.get_current_day(uid)
            if day < 1 or day > TOTAL_DAYS:
                continue
            completed = set(db.get_completed_tasks(uid, day))
            all_done  = len(completed) >= 5

            # Умное дневное послание с картинкой настроения (без кнопок)
            await send_mood_message(
                bot, max_id,
                _md(ct.get_afternoon_smart(day, completed)),
                len(completed),
            )
            # Трекер отдельным сообщением если не все выполнены
            if not all_done:
                await bot.send_message(
                    max_id,
                    build_today_text(day, completed),
                    buttons=_tasks_buttons(day, completed),
                )
        except Exception:
            logger.exception(f"Afternoon job error for MAX user {max_id}")


async def _job_evening():
    """21:00 — вечерний итог с умным сообщением и mood-картинкой."""
    bot   = get_client()
    users = db.get_all_active_users()
    for user in users:
        uid    = user["user_id"]
        max_id = db.get_max_user_id_by_internal(uid)
        if max_id is None:
            continue
        try:
            tz        = pytz.timezone(user["timezone"] or "Europe/Moscow")
            now_local = datetime.now(tz)
            if now_local.hour != 21:
                continue
            if not db.is_program_started(uid):
                continue
            day = db.get_current_day(uid)
            if day < 1 or day > TOTAL_DAYS:
                continue
            completed = set(db.get_completed_tasks(uid, day))
            all_done  = len(completed) >= 5

            # Умное вечернее послание — шлём всегда (как в TG)
            await send_mood_message(
                bot, max_id,
                _md(ct.get_evening_smart(day, completed)),
                len(completed),
            )
            # Трекер отдельным сообщением если не все выполнены
            if not all_done:
                await bot.send_message(
                    max_id,
                    build_today_text(day, completed),
                    buttons=_tasks_buttons(day, completed),
                )
        except Exception:
            logger.exception(f"Evening job error for MAX user {max_id}")


async def _job_weekly():
    """20:00 — недельный итог на milestone-днях (7, 14, 21 ...)."""
    bot   = get_client()
    users = db.get_all_active_users()
    for user in users:
        uid    = user["user_id"]
        max_id = db.get_max_user_id_by_internal(uid)
        if max_id is None:
            continue
        try:
            tz        = pytz.timezone(user["timezone"] or "Europe/Moscow")
            now_local = datetime.now(tz)
            if now_local.hour != 20:
                continue
            if not db.is_program_started(uid):
                continue
            day = db.get_current_day(uid)
            if day not in WEEKLY_MILESTONE_DAYS:
                continue
            await bot.send_message(max_id, build_weekly_milestone_text(uid))
        except Exception:
            logger.exception(f"Weekly job error for MAX user {max_id}")


def setup_scheduler():
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(_job_morning,   "cron", hour="*", minute=0,  id="max_morning")
    _scheduler.add_job(_job_afternoon, "cron", hour="*", minute=5,  id="max_afternoon")
    _scheduler.add_job(_job_evening,   "cron", hour="*", minute=10, id="max_evening")
    _scheduler.add_job(_job_weekly,    "cron", hour="*", minute=15, id="max_weekly")
    _scheduler.start()
    logger.info("MAX планировщик запущен")


# ── Инициализация вебхука ─────────────────────────────────────

async def setup(webapp_base_url: str):
    if not MAX_PROGRAM_TOKEN:
        logger.warning("MAX_PROGRAM_BOT_TOKEN не задан — MAX основной бот не запущен")
        return
    bot = get_client()
    me  = await bot.get_me()
    logger.info(f"MAX программный бот: {me.get('name', '?')} (@{me.get('username', '?')})")
    webhook_url = f"{webapp_base_url.rstrip('/')}{WEBHOOK_PATH}"
    await bot.setup_webhook(webhook_url)
    logger.info(f"MAX программный бот вебхук: {webhook_url}")
    setup_scheduler()
