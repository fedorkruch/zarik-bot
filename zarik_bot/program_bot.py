"""
program_bot.py — Программный бот Зарика (@Zarik_Lazy_Bot): онбординг + 77-дневная программа.
Проверяет факт оплаты через БД по user_id. Без оплаты — не пускает.
"""
import hashlib
import hmac as _hmac
import html
import logging
import os
import re
import time as _time
from datetime import datetime as dt
from pathlib import Path
import pytz

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

import database as db
import content as ct
from keyboards import (
    tasks_keyboard,
    tasks_and_webapp_keyboard,
    all_done_keyboard,
    tab_only_keyboard,
    timezone_keyboard,
    reps_keyboard,
    webapp_keyboard,
    welcome_keyboard,
    photo_keyboard,
    photos_done_keyboard,
    photos_retry_keyboard,
    main_menu,
    START_MENU,
    TIMEZONES,
)
from workout import get_workout

# ── Конфигурация ──────────────────────────────────────────────
PROGRAM_BOT_TOKEN = os.environ.get("PROGRAM_BOT_TOKEN") or os.environ["BOT_TOKEN"]
LEAD_BOT_USERNAME = os.environ.get("LEAD_BOT_USERNAME", "Shagov77_bot")
ADMIN_ID          = int(os.environ.get("ADMIN_ID", "283760217"))
WEBAPP_URL        = os.environ.get("WEBAPP_URL", "")   # https://xxx.up.railway.app
MAIN_MENU         = main_menu(WEBAPP_URL)              # ← собирается с WebApp-кнопкой если URL задан
TOTAL_DAYS        = 77
BEFORE_EXAMPLE    = Path(__file__).parent / "before_example.jpg"   # пример фото «до» для онбординга
# Тест-пользователи: обходят проверку оплаты и сбрасываются при каждом /start
TEST_USER_IDS     = {283760217, 262479340}
VERSION           = "v2.1-miniapp"  # меняй чтобы проверить версию деплоя

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_last_start: dict[int, float] = {}


def make_miniapp_url(user_id: int) -> str:
    """Генерирует подписанный URL для открытия Mini App без зависимости от initData."""
    if not WEBAPP_URL:
        return ""
    ts  = int(_time.time())
    sig = _hmac.new(
        PROGRAM_BOT_TOKEN.encode(),
        f"{user_id}:{ts}".encode(),
        hashlib.sha256,
    ).hexdigest()[:20]
    return f"{WEBAPP_URL}?uid={user_id}&ts={ts}&sig={sig}"

DIV = "· · · · · · · · · · · ·"

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


# ── Утилиты ───────────────────────────────────────────────────

def make_mini_bar(value: int, total: int, width: int = 10) -> str:
    if total == 0:
        return "·" * width
    filled = round(value / total * width)
    return "●" * filled + "·" * (width - filled)


def get_rank(days_done: int) -> str:
    """Ранг участника на основе засчитанных дней."""
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
    if n == 1:
        return "день"
    if 2 <= n <= 4:
        return "дня"
    return "дней"


def h(text: str) -> str:
    """Экранирует HTML-спецсимволы в пользовательских данных."""
    return html.escape(str(text))


def md2html(text: str) -> str:
    """Конвертирует *text* → <b>text</b>, остальное экранирует."""
    parts = re.split(r'\*(.+?)\*', str(text), flags=re.DOTALL)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            result.append(html.escape(part))
        else:
            result.append(f"<b>{html.escape(part)}</b>")
    return "".join(result)


def not_paid_message() -> str:
    lead = f"\n\n👉 @{LEAD_BOT_USERNAME}" if LEAD_BOT_USERNAME else ""
    return (
        "🦥 Участие в программе не оплачено.\n\n"
        "Вернись к боту регистрации, пройди воронку и оплати участие — "
        f"после этого возвращайся сюда.{lead}"
    )


# ── Утилита: редактировать любое сообщение (текст или фото) ──

async def _safe_edit(query, text: str, reply_markup=None, parse_mode=None):
    """
    Редактирует сообщение независимо от его типа.
    • Текстовое  → edit_message_text
    • Фото       → edit_message_caption
    • Ни то ни другое → reply_text (новое сообщение)
    """
    kwargs = {"reply_markup": reply_markup}
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception:
        try:
            await query.edit_message_caption(caption=text, **kwargs)
        except Exception:
            await query.message.reply_text(text, **kwargs)


# ── Выбор клавиатуры для экрана «Сегодня» ────────────────────

def today_markup(user_id: int, day: int, completed: set):
    """
    Если WEBAPP_URL задан и пользователь в Mini App режиме — показывает
    чекбоксы задач + кнопку «Открыть в мини-апп» (синхронизация в реальном времени).
    Иначе — обычная инлайн-клавиатура с задачами.
    """
    if WEBAPP_URL and db.get_use_miniapp(user_id):
        return tasks_and_webapp_keyboard(day, completed, WEBAPP_URL)
    return tasks_keyboard(day, completed, active_tab="tasks")


# ── Построители экранов (HTML) ────────────────────────────────

def build_today_screen(user_row, day: int, completed: set) -> str:
    """Экран трекера — только призыв к действию, без заголовка и прогресс-бара."""
    return "<b>📋  Отметь что выполнил сегодня 👇</b>"


def build_morning_text(user_row, day: int) -> str:
    """Утреннее мотивационное послание — только текст, без тренировки."""
    return ct.get_morning(day)


def build_tasks_list(user_row, day: int) -> str:
    """Задания на день — отдельное сообщение после мотивации."""
    workout = get_workout(dict(user_row), day)
    lines = [
        f"<b>📋 Задания на день {day}:</b>",
        "",
        f"💪 <b>Тренировка на сегодня:</b>\n{workout['description']}",
        "",
        "💧 <b>Цель по воде:</b> 2 литра / 8 стаканов",
        "📚 <b>Цель по чтению:</b> 10 страниц",
        "🥗 Без фастфуда сегодня",
        "🚫 Без алкоголя",
    ]
    return "\n".join(lines)


def build_progress_screen(user_id: int) -> str:
    """Экран «Прогресс» — переработанный: ранг, бар, путь по неделям, ближайшая цель."""
    stats  = db.get_stats(user_id)
    day    = stats["current_day"]
    done   = stats["days_completed"]
    streak = stats["streak"]
    percentile, ctx = ct.get_planet_percentile(done)
    next_m = ct.get_next_percentile_milestone(done)

    # Прогресс-бар (моноширинный — выглядит ровно на всех устройствах)
    bar_width = 17
    filled = round(done / TOTAL_DAYS * bar_width)
    pct    = round(done / TOTAL_DAYS * 100)
    bar    = "▓" * filled + "░" * (bar_width - filled)

    # Визуализация пути по неделям (11 недель = 77 дней)
    week_now = (day - 1) // 7 + 1
    weeks = ""
    for w in range(1, 12):
        if w < week_now:
            weeks += "✅"
        elif w == week_now:
            weeks += "🔥"
        else:
            weeks += "⬜"

    # Следующая цель
    if next_m:
        d, pct_next = next_m
        goal_line = f"Ещё <b>{d}</b> {day_word(d)} → {h(pct_next)} 🌍"
    else:
        goal_line = "Ты достиг максимального рейтинга! 🏆"

    # Серия — текстовое усиление
    if streak >= 7:
        streak_label = f"<b>{streak}</b> {day_word(streak)} подряд 🔥"
    elif streak >= 3:
        streak_label = f"<b>{streak}</b> {day_word(streak)} подряд 💪"
    elif streak == 0:
        streak_label = "<b>0</b> — начни сегодня!"
    else:
        streak_label = f"<b>{streak}</b> {day_word(streak)} подряд"

    rank = get_rank(done)

    lines = [
        f"<b>{rank}</b>",
        "",
        f"День <b>{day}</b> из {TOTAL_DAYS}",
        f"<code>{bar}</code>  {pct}%",
        "",
        "─────────────────────",
        "",
        f"✅  Засчитано      <b>{done}</b> {day_word(done)}",
        f"🔥  Серия          {streak_label}",
        f"🌍  Рейтинг        <b>{h(percentile)}</b> планеты",
        "",
        f"🎯  {goal_line}",
        "",
        "─────────────────────",
        "",
        "📅  Путь по неделям:",
        weeks,
        "",
        "─────────────────────",
        "",
        f"<i>{h(ctx)}</i>",
    ]
    return "\n".join(lines)


def build_week_screen(user_id: int) -> str:
    """Экран «Неделя» — итоги текущей недели и группы."""
    stats = db.get_stats(user_id)
    day   = stats["current_day"]
    done  = stats["days_completed"]
    week_num    = (day - 1) // 7 + 1
    week_start  = (week_num - 1) * 7 + 1
    all_compl   = db.get_completed_days_set(user_id)
    week_done   = len({d for d in all_compl if week_start <= d <= day})
    percentile, ctx = ct.get_planet_percentile(done)
    week_bar    = make_mini_bar(week_done, 7)
    header      = md2html(ct.get_weekly_header(week_num))
    group       = db.get_group_stats()

    lines = [
        f"<b>📅  Неделя {week_num} · {week_done} из 7 дней</b>",
        "",
        header,
        "",
        DIV,
        f"Эта неделя:  {week_bar}  {week_done} / 7",
        f"Всего засчитано:  <b>{done}</b> из {day} дней",
        f"🌍  <b>{h(percentile)}</b> планеты",
    ]

    next_m = ct.get_next_percentile_milestone(done)
    if next_m:
        d, pct = next_m
        lines.append(f"      <i>ещё {d} {day_word(d)} → {h(pct)}</i>")

    if group and group.get("total", 0) > 0:
        total_g  = group["total"]
        active_g = group["active"]
        lines += [
            "",
            DIV,
            f"👥  Группа:           {total_g} участников",
            f"🏃  Продолжают:   <b>{active_g}</b>",
        ]

    return "\n".join(lines)


WEEKLY_MILESTONE_DAYS = {7, 14, 21, 28, 35, 42, 49, 56, 63, 70, 77}

# Ккал на одно повторение (усреднённо)
_KCAL_PUSHUP = 0.5
_KCAL_SQUAT  = 0.5
_KCAL_ABS    = 0.3
_KCAL_SESSION = 50  # базовые калории за сессию (разминка, кардио-эффект)

TASK_LABELS_WEEKLY = [
    "💪 Тренировка",
    "💧 Вода",
    "📚 Чтение",
    "🥗 Питание",
    "🚫 Алкоголь",
]


def _calc_task_percentile(user_rate: float, all_rates: list) -> int:
    """Возвращает % участников с более низким показателем (чем выше — тем лучше)."""
    if len(all_rates) < 2:
        return 50
    below = sum(1 for r in all_rates if r < user_rate)
    return round(below / len(all_rates) * 100)


def build_weekly_milestone_screen(user_id: int) -> str:
    """
    Итог недели для дней 7, 14, 21 ... 77.
    Форматирует шаблон WEEKLY_HEADER реальными данными пользователя.
    """
    stats    = db.get_stats(user_id)
    day      = stats["current_day"]
    week_num = (day - 1) // 7 + 1

    user_row  = db.get_user(user_id)
    all_compl = db.get_completed_days_set(user_id)

    # Границы текущей недели
    week_start = (week_num - 1) * 7 + 1
    week_end   = week_num * 7

    # ── Накопленные и недельные повторения ──────────────────────
    total_pushups = total_squats = total_abs = workout_days = 0
    week_pushups  = week_squats  = week_abs  = w_train = 0

    for d in all_compl:
        tasks = db.get_completed_tasks(user_id, d)
        if 0 in tasks:
            w = get_workout(dict(user_row), d)
            total_pushups += w["pushup"]["total"]
            total_squats  += w["squat"]["total"]
            total_abs     += w["abs"]["total"]
            workout_days  += 1
            if week_start <= d <= week_end:
                week_pushups += w["pushup"]["total"]
                week_squats  += w["squat"]["total"]
                week_abs     += w["abs"]["total"]
                w_train      += 1

    # ── Подсчёт задач за неделю ─────────────────────────────────
    user_counts        = db.get_task_completion_counts(user_id)
    weekly_task_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    for d in all_compl:
        if week_start <= d <= week_end:
            for t in db.get_completed_tasks(user_id, d):
                if t in weekly_task_counts:
                    weekly_task_counts[t] += 1

    # Конвертация: вода — литры (×2), чтение — страницы (×20)
    w_water  = weekly_task_counts[1] * 2
    w_pages  = weekly_task_counts[2] * 20
    w_nojunk = weekly_task_counts[3]
    w_noalc  = weekly_task_counts[4]

    total_water  = user_counts.get(1, 0) * 2
    total_pages  = user_counts.get(2, 0) * 20
    total_nojunk = user_counts.get(3, 0)
    total_noalc  = user_counts.get(4, 0)

    # ── Форматируем шаблон ──────────────────────────────────────
    header_text = ct.format_weekly_header(
        week_num,
        train=w_train,
        pushups=week_pushups,
        abs=week_abs,
        squats=week_squats,
        water=w_water,
        pages=w_pages,
        nojunk=w_nojunk,
        noalc=w_noalc,
        total_pushups=total_pushups,
        total_abs=total_abs,
        total_squats=total_squats,
        total_water=total_water,
        total_pages=total_pages,
        total_nojunk=total_nojunk,
        total_noalc=total_noalc,
    )

    # ── Группа ──────────────────────────────────────────────────
    group = db.get_group_stats()
    lines = [md2html(header_text)]

    if group and group.get("total", 0) > 0:
        lines += [
            "",
            "─────────────────────",
            "",
            f"👥  Группа:      {group['total']} участников",
            f"🏃  Продолжают:  <b>{group['active']}</b>",
        ]

    return "\n".join(lines)

def build_achievements_screen(user_id: int) -> str:
    """Экран «Ачивки» — все достижения с ✅/⬜."""
    lines = ["<b>🏆  Твои ачивки</b>", ""]
    for ach_id, threshold in ct.ACHIEVEMENT_ORDER:
        ach      = ct.ACHIEVEMENTS[ach_id]
        unlocked = db.has_achievement(user_id, ach_id)
        mark     = "✅" if unlocked else "⬜"
        name     = h(ach["name"])
        if unlocked:
            lines.append(f"{mark}  {ach['icon']}  <b>День {threshold} — {name}</b>")
        else:
            lines.append(f"{mark}  {ach['icon']}  <i>День {threshold} — {name}</i>")
    return "\n".join(lines)


# ── /start ────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    now = _time.time()
    if now - _last_start.get(user.id, 0) < 5:
        return
    _last_start[user.id] = now

    # ── Тест-пользователи: сброс + обход оплаты ──────────────
    if user.id in TEST_USER_IDS:
        db.register_user(user.id, user.username or "", user.first_name or "Тест")
        if not db.is_payment_confirmed(user.id):
            db.save_payment(
                user_id=user.id,
                charge_id=f"test_{user.id}",
                participation_fee=0,
                stake_amount=0,
            )
        db.reset_to_onboarding(user.id)
        await update.message.reply_text(WELCOME_TEXT, reply_markup=welcome_keyboard())
        db.set_onboarding_step(user.id, "welcome")
        return

    if not db.is_payment_confirmed(user.id):
        await update.message.reply_text(not_paid_message(), reply_markup=START_MENU)
        return

    user_row = db.get_user(user.id)
    step     = db.get_onboarding_step(user.id)

    if step == "welcome":
        # Онбординг уже начат — просто напоминаем нажать кнопку
        await update.message.reply_text(
            "🦥 Нажми кнопку «Поехали» чтобы продолжить 👇",
            reply_markup=welcome_keyboard()
        )
    elif step == "timezone":
        await update.message.reply_text(
            "🦥 Шаг 1 из 4 · Часовой пояс\n\n"
            "Выбери свой часовой пояс — буду присылать задания в 6:00 по твоему времени 👇",
            reply_markup=timezone_keyboard()
        )
    elif step == "pushup":
        await update.message.reply_text(
            "🦥 Шаг 2 из 4 · Тренировка\n\n"
            "Сколько отжиманий можешь сделать прямо сейчас?",
            reply_markup=reps_keyboard("pushup")
        )
    elif step == "squat":
        await update.message.reply_text(
            "🦥 Сколько приседаний?",
            reply_markup=reps_keyboard("squat")
        )
    elif step == "abs":
        await update.message.reply_text(
            "🦥 Сколько раз пресс?",
            reply_markup=reps_keyboard("abs")
        )
    elif step == "photo":
        await update.message.reply_text(
            "🦥 Шаг 4 из 4 · Фото до/после\n\n"
            "Хочешь делиться результатами до/после? Это поможет увидеть прогресс за 77 дней.",
            reply_markup=photo_keyboard()
        )
    elif step == "awaiting_photos":
        count = db.count_user_photos(user.id, "before")
        saved = f" Уже сохранено: {count} фото." if count else ""
        await update.message.reply_text(
            f"📸 Жду твои фото «до».{saved}\n\nКогда всё отправишь — нажми кнопку 👇",
            reply_markup=photos_done_keyboard()
        )
    elif step == "done":
        if not db.is_program_started(user.id):
            await update.message.reply_text(
                "🦥 Всё готово! Завтра в 6:00 пришлю первые задания.\n\n"
                "Используй кнопки меню 👇",
                reply_markup=MAIN_MENU
            )
        else:
            day       = db.get_current_day(user.id)
            completed = db.get_completed_tasks(user.id, day)
            tracker_msg = await update.message.reply_text(
                build_today_screen(user_row, day, completed),
                parse_mode=ParseMode.HTML,
                reply_markup=today_markup(user.id, day, completed)
            )
            db.save_tracker_message(user.id, day, tracker_msg.message_id)
    else:
        # Первый визит — приветствие и описание программы
        await update.message.reply_text(WELCOME_TEXT, reply_markup=welcome_keyboard())
        db.set_onboarding_step(user.id, "welcome")


# ── Команды меню ──────────────────────────────────────────────

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not db.is_payment_confirmed(user.id):
        await update.message.reply_text(not_paid_message(), reply_markup=START_MENU)
        return
    user_row = db.get_user(user.id)
    if not user_row or db.get_onboarding_step(user.id) != "done":
        await update.message.reply_text("Сначала напиши /start чтобы завершить настройку.")
        return
    if not db.is_program_started(user.id):
        await update.message.reply_text(
            "🦥 Всё готово! Завтра в 6:00 пришлю первые задания.",
            reply_markup=MAIN_MENU
        )
        return
    day       = db.get_current_day(user.id)
    completed = db.get_completed_tasks(user.id, day)
    tracker_msg = await update.message.reply_text(
        build_today_screen(user_row, day, completed),
        parse_mode=ParseMode.HTML,
        reply_markup=today_markup(user.id, day, completed)
    )
    db.save_tracker_message(user.id, day, tracker_msg.message_id)


async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not db.is_payment_confirmed(user.id):
        await update.message.reply_text(not_paid_message(), reply_markup=START_MENU)
        return
    if not db.is_program_started(user.id):
        await update.message.reply_text("Ишь хитрюга)) Вот завтра начнем, тогда и прогресс появится 😄")
        return
    await update.message.reply_text(
        build_progress_screen(user.id),
        parse_mode=ParseMode.HTML,
    )


# ── Callback-обработчик ──────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_id = query.from_user.id
    data    = query.data
    db.log_user_session(user_id)

    # ── Переключение вкладок ──────────────────────────────────
    if data.startswith("tab:"):
        await query.answer()
        tab      = data.split(":", 1)[1]
        user_row = db.get_user(user_id)

        if tab == "tasks":
            if not db.is_program_started(user_id):
                await query.answer("Программа ещё не началась — ждём завтра 🦥", show_alert=True)
                return
            day       = db.get_current_day(user_id)
            completed = db.get_completed_tasks(user_id, day)
            text      = build_today_screen(user_row, day, completed)
            markup    = tasks_keyboard(day, completed, active_tab="tasks")

        elif tab == "progress":
            if not db.is_program_started(user_id):
                await query.answer("Ишь хитрюга)) Вот завтра начнем, тогда и прогресс появится 😄", show_alert=True)
                return
            text   = build_progress_screen(user_id)
            markup = None

        elif tab == "week":
            text   = build_week_screen(user_id)
            markup = tab_only_keyboard("week")

        elif tab == "achievements":
            text   = build_achievements_screen(user_id)
            markup = None

        else:
            return

        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
        return

    # ── Старт онбординга (кнопка «Поехали») ──────────────────
    if data == "onboarding_start":
        await query.answer()
        db.set_onboarding_step(user_id, "timezone")
        await query.edit_message_text(
            "🦥 Шаг 1 из 4 · Часовой пояс\n\n"
            "Выбери свой часовой пояс — буду присылать задания в 6:00 по твоему времени 👇",
            reply_markup=timezone_keyboard()
        )
        return

    # ── Выбор часового пояса ──────────────────────────────────
    if data.startswith("tz:"):
        await query.answer()
        tz_name  = data.split(":", 1)[1]
        db.set_user_timezone(user_id, tz_name)
        tz_label = next((l for l, tz in TIMEZONES if tz == tz_name), tz_name)
        await query.edit_message_text(f"✅ Часовой пояс: {tz_label}")
        await query.message.reply_text(
            "🦥 Теперь давай подберём тренировку под тебя.\n\n"
            "Сколько отжиманий можешь сделать прямо сейчас?",
            reply_markup=reps_keyboard("pushup")
        )
        return

    # ── Онбординг: повторения ─────────────────────────────────
    if data.startswith("reps:"):
        await query.answer()
        _, exercise, reps_str = data.split(":")
        reps = int(reps_str)

        if exercise == "pushup":
            db.save_pushup_start(user_id, reps)
            await query.edit_message_text(
                f"💪 Отжимания: {reps} — записал!\n\nСколько приседаний?",
                reply_markup=reps_keyboard("squat")
            )
        elif exercise == "squat":
            db.save_squat_start(user_id, reps)
            await query.edit_message_text(
                f"🦵 Приседания: {reps} — отлично!\n\nСколько раз пресс?",
                reply_markup=reps_keyboard("abs")
            )
        elif exercise == "abs":
            db.save_abs_start(user_id, reps)  # устанавливает step='photo'
            await query.edit_message_text(
                f"🔥 Пресс: {reps} — красава!\n\n"
                f"🦥 Шаг 4 из 4 · Фото до/после\n\n"
                f"Хочешь делиться результатами до/после? "
                f"Это поможет увидеть свой прогресс за 77 дней.",
                reply_markup=photo_keyboard()
            )
        return

    # ── Фото до/после ─────────────────────────────────────────
    if data == "photo_yes":
        await query.answer()
        db.set_share_photos(user_id, True)
        db.set_onboarding_step(user_id, "awaiting_photos")
        await query.edit_message_text("📸 Отлично! Сейчас объясню что нужно сделать 👇")
        instruction = (
            "Сделай 2 фото «до»:\n"
            "• Фронтальное (анфас)\n"
            "• Боковое (профиль)\n\n"
            "На фото закрой лицо листом бумаги с датой старта в формате число/месяц/год и надписью «Для Зарика» ✍️\n\n"
            "👗 Девушки — купальник или нижнее бельё\n"
            "🩳 Парни — шорты или трусы\n\n"
            "Надевайте что вам комфортнее, но в рамках приличия "
            "(чтобы я, Зарик, не поплыл — я чувствительный 🦥)\n\n"
            "Вот пример фото, можно сделать так же 👆\n\n"
            "Отправляй фото сюда — я сохраню 👇\n"
            "Когда закончишь — нажми кнопку ниже.\n\n"
            "🔒 Мы не делимся твоими фото, не выкладываем их никуда — это только для тебя."
        )
        try:
            with open(BEFORE_EXAMPLE, "rb") as photo_file:
                await query.message.reply_photo(
                    photo=photo_file,
                    caption=instruction,
                    reply_markup=photos_done_keyboard(),
                )
        except Exception:
            # Если фото недоступно — шлём текст как запасной вариант
            await query.message.reply_text(instruction, reply_markup=photos_done_keyboard())
        return

    if data == "photo_no":
        await query.answer()
        db.set_share_photos(user_id, False)
        db.complete_onboarding(user_id)
        await query.edit_message_text("👌 Понял, без фото — тоже отлично!")
        await query.message.reply_text(
            "Отлично, твоя программа сформирована под тебя 🎯\n\n"
            "Я пока пошёл дальше висеть на ветке, а с тобой свяжусь завтра утром 🦥\n"
            "А пока — отдыхай)",
            reply_markup=MAIN_MENU
        )
        return

    if data == "photos_done":
        await query.answer()
        count = db.count_user_photos(user_id, "before")
        if count < 1:
            # Фото ещё не отправлено — просим прислать или пропустить
            await _safe_edit(
                query,
                "📸 Фото пока не получено.\n\n"
                "Отправь 2 фото в чат (анфас и профиль) "
                "или пропусти этот этап, если не хочешь делиться фото 👇",
                reply_markup=photos_retry_keyboard()
            )
            return
        db.complete_onboarding(user_id)
        noun = "фото" if count in (2, 3, 4) else "фото"
        await _safe_edit(query, f"✅ Сохранил {count} {noun} 📸")
        await query.message.reply_text(
            "Отлично, твоя программа сформирована под тебя 🎯\n\n"
            "Я пока пошёл дальше висеть на ветке, а с тобой свяжусь завтра утром 🦥\n"
            "А пока — отдыхай)",
            reply_markup=MAIN_MENU
        )
        return

    # ── Закрыть день ──────────────────────────────────────────
    if data.startswith("close_day:"):
        day = int(data.split(":")[1])
        current_day = db.get_current_day(user_id)
        if day != current_day:
            await query.answer("Это задание уже не актуально.", show_alert=True)
            return
        completed = db.get_completed_tasks(user_id, day)
        done = len(completed)
        if done < 5:
            remaining = 5 - done
            noun = "задача" if remaining == 1 else "задачи" if remaining < 5 else "задач"
            await query.answer(
                f"Осталось {remaining} {noun}. Отметь все — и закроем день! 💪",
                show_alert=True
            )
            return
        # Все 5 выполнены — закрываем день
        await query.answer()
        user_row = db.get_user(user_id)
        await query.edit_message_text(
            build_today_screen(user_row, day, completed),
            parse_mode=ParseMode.HTML,
            reply_markup=all_done_keyboard(active_tab="tasks")
        )
        evening_text = ct.get_evening(day, all_done=True)
        await query.message.reply_text(
            f"{evening_text}\n\n<i>День {day} засчитан! 🎉</i>",
            parse_mode=ParseMode.HTML
        )
        if day == TOTAL_DAYS:
            await query.message.reply_text(ct.FINAL_MESSAGE, parse_mode=ParseMode.MARKDOWN)
        stats = db.get_stats(user_id)
        new_achievements = ct.check_achievements(stats["days_completed"])
        for ach_id in new_achievements:
            if not db.has_achievement(user_id, ach_id):
                db.award_achievement(user_id, ach_id)
                await query.message.reply_text(
                    ct.get_achievement_text(ach_id), parse_mode=ParseMode.MARKDOWN
                )
        return

    # ── Отметка задач ─────────────────────────────────────────
    if data.startswith("task:"):
        _, day_str, task_str = data.split(":")
        day        = int(day_str)
        task_index = int(task_str)
        current_day = db.get_current_day(user_id)

        if day != current_day:
            await query.answer("Это задание уже не актуально.", show_alert=True)
            return

        await query.answer()
        db.complete_task(user_id, day, task_index)
        completed = db.get_completed_tasks(user_id, day)

        if db.has_dropout_warning(user_id):
            db.clear_dropout_warning(user_id)

        user_row = db.get_user(user_id)

        if len(completed) >= 5:
            await query.edit_message_text(
                build_today_screen(user_row, day, completed),
                parse_mode=ParseMode.HTML,
                reply_markup=all_done_keyboard(active_tab="tasks")
            )
            evening_text = ct.get_evening(day, all_done=True)
            await query.message.reply_text(
                f"{evening_text}\n\n<i>День {day} засчитан! 🎉</i>",
                parse_mode=ParseMode.HTML
            )

            if day == TOTAL_DAYS:
                await query.message.reply_text(
                    ct.FINAL_MESSAGE, parse_mode=ParseMode.MARKDOWN
                )

            stats = db.get_stats(user_id)
            new_achievements = ct.check_achievements(stats["days_completed"])
            for ach_id in new_achievements:
                if not db.has_achievement(user_id, ach_id):
                    db.award_achievement(user_id, ach_id)
                    await query.message.reply_text(
                        ct.get_achievement_text(ach_id),
                        parse_mode=ParseMode.MARKDOWN
                    )
        else:
            await query.edit_message_text(
                build_today_screen(user_row, day, completed),
                parse_mode=ParseMode.HTML,
                reply_markup=tasks_keyboard(day, completed, active_tab="tasks")
            )
        return

    if data == "noop":
        await query.answer()

    # ── Фоллбек: Mini App не открылся → переключаем на инлайн ─
    if data == "miniapp_fallback":
        await query.answer("Переключаю на режим кнопок 👇")
        db.set_miniapp_mode(user_id, False)
        if not db.is_program_started(user_id):
            await query.answer("Программа ещё не началась — ждём завтра 🦥", show_alert=True)
            return
        day       = db.get_current_day(user_id)
        completed = db.get_completed_tasks(user_id, day)
        user_row  = db.get_user(user_id)
        await query.edit_message_text(
            build_today_screen(user_row, day, completed),
            parse_mode=ParseMode.HTML,
            reply_markup=tasks_keyboard(day, completed, active_tab="tasks"),
        )
        return


# ── Текстовые сообщения ──────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text    = update.message.text.strip()
    db.log_user_session(user_id)

    if text == "🦥 Начать":
        await cmd_start(update, context)
        return

    if not db.is_payment_confirmed(user_id):
        await update.message.reply_text(not_paid_message(), reply_markup=START_MENU)
        return

    if text == "📋 Мои задачи на сегодня":
        await cmd_today(update, context)
    elif text in ("📊 Прогресс", "📊 Мой прогресс"):
        await cmd_progress(update, context)
    elif text == "🏆 Ачивки":
        await update.message.reply_text(
            build_achievements_screen(user_id),
            parse_mode=ParseMode.HTML,
        )
    elif text == "📱 МиниАПП":
        if WEBAPP_URL:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo as _WAI
            url = make_miniapp_url(user_id)
            await update.message.reply_text(
                "📱",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Открыть трекер", web_app=_WAI(url=url))
                ]]),
                disable_notification=True,
            )
    elif text == "❓ Помощь":
        await update.message.reply_text(
            "🦥 Как пользоваться Зариком:\n\n"
            "📋 Мои задачи — открыть чеклист и отмечать выполненные\n"
            "📊 Прогресс — статистика и рейтинг планеты\n"
            "🏆 Ачивки — все достижения: открытые и ещё впереди\n\n"
            "Вкладки внизу каждого экрана переключают разделы.\n"
            "При вопросах — пиши сюда!",
            reply_markup=MAIN_MENU
        )
    else:
        await update.message.reply_text(
            "🦥 Используй кнопки меню 👇",
            reply_markup=MAIN_MENU
        )


# ── Входящие фото (онбординг: шаг awaiting_photos) ──────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.log_user_session(user_id)
    if not db.is_payment_confirmed(user_id):
        return
    step = db.get_onboarding_step(user_id)
    if step != "awaiting_photos":
        return
    # Берём наибольшее разрешение из списка (последний элемент)
    photo = update.message.photo[-1]
    db.save_user_photo(user_id, "before", photo.file_id)
    count = db.count_user_photos(user_id, "before")
    await update.message.reply_text(
        f"✅ Фото {count} сохранено! Пришли ещё или нажми «Готово».",
        reply_markup=photos_done_keyboard()
    )


# ── Планировщик ──────────────────────────────────────────────

async def job_morning(context: ContextTypes.DEFAULT_TYPE):
    users = db.get_all_active_users()
    for user in users:
        try:
            user_tz = pytz.timezone(user["timezone"] or "Europe/Moscow")
            if dt.now(user_tz).hour != 6:
                continue
            if not db.is_program_started(user["user_id"]):
                continue
            day = db.get_current_day(user["user_id"])
            if day > TOTAL_DAYS:
                continue
            completed = db.get_completed_tasks(user["user_id"], day)
            if len(completed) >= 5:
                continue
            missed = db.get_missed_streak(user["user_id"])

            if db.should_dropout(user["user_id"]):
                last_day = db.get_last_completed_day(user["user_id"])
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=ct.get_dropout_message(last_day)
                )
                db.deactivate_user(user["user_id"])
                logger.info(f"Участник {user['user_id']} выбыл")
                continue

            if missed >= 3 and not db.has_dropout_warning(user["user_id"]):
                last_day = db.get_last_completed_day(user["user_id"])
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=ct.get_miss_message(3, last_day)
                )
                db.set_dropout_warning_sent(user["user_id"])
                continue

            # 1. Мотивационное послание
            user_row = db.get_user(user["user_id"])
            morning_msg = build_morning_text(user_row, day)
            if 1 <= missed <= 2:
                last_day = db.get_last_completed_day(user["user_id"])
                morning_msg += f"\n\n{ct.get_miss_message(missed, last_day)}"
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=morning_msg,
            )
            # 2. Задания на день
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=build_tasks_list(user_row, day),
                parse_mode=ParseMode.HTML,
            )
            # 3. Трекер с галочками
            tracker_msg = await context.bot.send_message(
                chat_id=user["user_id"],
                text=build_today_screen(user_row, day, completed),
                parse_mode=ParseMode.HTML,
                reply_markup=today_markup(user["user_id"], day, completed)
            )
            db.save_tracker_message(user["user_id"], day, tracker_msg.message_id)
        except Exception as e:
            logger.warning(f"Утро {user['user_id']}: {e}")


async def job_afternoon(context: ContextTypes.DEFAULT_TYPE):
    users = db.get_all_active_users()
    for user in users:
        try:
            user_tz = pytz.timezone(user["timezone"] or "Europe/Moscow")
            if dt.now(user_tz).hour != 14:
                continue
            if not db.is_program_started(user["user_id"]):
                continue
            day = db.get_current_day(user["user_id"])
            if day > TOTAL_DAYS:
                continue
            completed = db.get_completed_tasks(user["user_id"], day)
            all_done  = len(completed) >= 5

            # 1. Умное дневное послание (текст отдельно)
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=ct.get_afternoon_smart(day, completed),
            )
            # 2. Трекер отдельным сообщением если не все выполнены
            if not all_done:
                user_row = db.get_user(user["user_id"])
                tracker_msg = await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=build_today_screen(user_row, day, completed),
                    parse_mode=ParseMode.HTML,
                    reply_markup=today_markup(user["user_id"], day, completed)
                )
                db.save_tracker_message(user["user_id"], day, tracker_msg.message_id)
        except Exception as e:
            logger.warning(f"День {user['user_id']}: {e}")


async def job_evening(context: ContextTypes.DEFAULT_TYPE):
    users = db.get_all_active_users()
    for user in users:
        try:
            user_tz = pytz.timezone(user["timezone"] or "Europe/Moscow")
            if dt.now(user_tz).hour != 21:
                continue
            if not db.is_program_started(user["user_id"]):
                continue
            day = db.get_current_day(user["user_id"])
            if day > TOTAL_DAYS:
                continue
            completed = db.get_completed_tasks(user["user_id"], day)
            all_done  = len(completed) >= 5

            # Умное вечернее послание с учётом незакрытых задач
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=ct.get_evening_smart(day, completed),
            )
            # Затем экран задач если не все выполнены
            if not all_done:
                user_row = db.get_user(user["user_id"])
                tracker_msg = await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=build_today_screen(user_row, day, completed),
                    parse_mode=ParseMode.HTML,
                    reply_markup=today_markup(user["user_id"], day, completed)
                )
                db.save_tracker_message(user["user_id"], day, tracker_msg.message_id)
        except Exception as e:
            logger.warning(f"Вечер {user['user_id']}: {e}")


async def job_weekly(context: ContextTypes.DEFAULT_TYPE):
    users = db.get_all_active_users()
    for user in users:
        try:
            user_tz   = pytz.timezone(user["timezone"] or "Europe/Moscow")
            local_now = dt.now(user_tz)
            if local_now.hour != 20:
                continue
            if not db.is_program_started(user["user_id"]):
                continue
            day = db.get_current_day(user["user_id"])
            if day not in WEEKLY_MILESTONE_DAYS:
                continue
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=build_weekly_milestone_screen(user["user_id"]),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning(f"Недельный итог {user['user_id']}: {e}")


# ── Админ-команды ─────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    count      = db.get_user_count()
    total_stake = db.get_total_stake() // 100
    active     = db.get_all_active_users()
    lines = [
        "🦥 Админ · Сводка", "",
        f"👥 Участников: {count}",
        f"🏃 Активных: {len(active)}",
        f"💰 Ставки: {total_stake:,} ₽".replace(",", " "),
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика активности участников за 7 дней (только для admin)."""
    if not is_admin(update.effective_user.id):
        return

    stats = db.get_session_stats()

    # Считаем средние часы первого и последнего входа с учётом часового пояса
    import pytz
    first_hours, last_hours = [], []
    for s in stats["raw_sessions"]:
        tz_name = s.get("timezone") or "Europe/Moscow"
        try:
            tz = pytz.timezone(tz_name)
            for field, bucket in ((s["first_open_utc"], first_hours), (s["last_open_utc"], last_hours)):
                if field:
                    utc_dt = dt.fromisoformat(field).replace(tzinfo=pytz.utc)
                    local_dt = utc_dt.astimezone(tz)
                    bucket.append(local_dt.hour + local_dt.minute / 60)
        except Exception:
            pass

    def fmt_hour(hours: list) -> str:
        if not hours:
            return "—"
        avg = sum(hours) / len(hours)
        h, m = int(avg), int((avg % 1) * 60)
        return f"{h:02d}:{m:02d}"

    top_lines = ""
    for i, u in enumerate(stats["top_users"], 1):
        name = f"@{u['username']}" if u.get("username") else f"id{u['user_id']}"
        top_lines += f"\n  {i}. {name} — {u['total']} обращений"

    text = (
        "📊 *Активность · @Zarik\\_Lazy\\_Bot*\n\n"
        f"👥 Активных сегодня: *{stats['active_today']}*\n"
        f"👥 Активных за 7 дней: *{stats['active_users_7d']}*\n\n"
        "📅 *Средние показатели за 7 дней:*\n"
        f"• Обращений в день: *{stats['avg_interactions']}*\n"
        f"• Продолжительность сессии: *{stats['avg_session_min']} мин*\n"
        f"• Активных дней из 7: *{stats['avg_active_days']}*\n\n"
        "⏰ *Время по местному часовому поясу:*\n"
        f"• Первое открытие: *{fmt_hour(first_hours)}*\n"
        f"• Последнее открытие: *{fmt_hour(last_hours)}*\n"
    )
    if top_lines:
        text += f"\n🔥 *Топ-5 активных (7 дней):*{top_lines}"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_setday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in TEST_USER_IDS:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Использование: /setday 7")
        return
    target = int(args[0])
    if not 1 <= target <= TOTAL_DAYS:
        await update.message.reply_text(f"День должен быть от 1 до {TOTAL_DAYS}")
        return

    uid = update.effective_user.id
    db.set_day_for_testing(uid, target)
    user_row  = db.get_user(uid)
    completed = db.get_completed_tasks(uid, target)

    # ── Шапка ─────────────────────────────────────────────────
    await update.message.reply_text(
        f"🛠 <b>DEV · День {target} из {TOTAL_DAYS}</b>\n"
        f"──────────────────────",
        parse_mode=ParseMode.HTML,
    )

    # ── ☀️ 6:00 — Мотивационное послание ──────────────────────
    await update.message.reply_text(build_morning_text(user_row, target))

    # ── 📋 6:00 — Задания на день ─────────────────────────────
    await update.message.reply_text(
        build_tasks_list(user_row, target),
        parse_mode=ParseMode.HTML,
    )

    # ── Трекер с галочками ────────────────────────────────────
    tracker_msg = await update.message.reply_text(
        build_today_screen(user_row, target, completed),
        parse_mode=ParseMode.HTML,
        reply_markup=today_markup(uid, target, completed),
    )
    db.save_tracker_message(uid, target, tracker_msg.message_id)

    # ── 🌤 14:00 — ноль галочек ───────────────────────────────
    await update.message.reply_text(
        f"<i>— 14:00 · ноль галочек —</i>\n\n"
        + ct.get_afternoon_smart(target, set()),
        parse_mode=ParseMode.HTML,
    )

    # ── 🌤 14:00 — часть галочек (пример: выполнена только тренировка) ──
    partial_sample = {0}   # только тренировка закрыта
    await update.message.reply_text(
        f"<i>— 14:00 · частично (пример: только тренировка) —</i>\n\n"
        + ct.get_afternoon_smart(target, partial_sample),
        parse_mode=ParseMode.HTML,
    )

    # ── 🌤 14:00 — все выполнено ──────────────────────────────
    await update.message.reply_text(
        f"<i>— 14:00 · все галочки —</i>\n\n"
        + ct.get_afternoon_smart(target, {0, 1, 2, 3, 4}),
        parse_mode=ParseMode.HTML,
    )

    # ── 🌙 21:00 — ноль галочек ───────────────────────────────
    await update.message.reply_text(
        f"<i>— 21:00 · ноль галочек —</i>\n\n"
        + ct.get_evening_smart(target, set()),
        parse_mode=ParseMode.HTML,
    )

    # ── 🌙 21:00 — часть галочек ──────────────────────────────
    await update.message.reply_text(
        f"<i>— 21:00 · частично (пример: только тренировка) —</i>\n\n"
        + ct.get_evening_smart(target, partial_sample),
        parse_mode=ParseMode.HTML,
    )

    # ── 🌙 21:00 — все выполнено ──────────────────────────────
    await update.message.reply_text(
        f"<i>— 21:00 · все галочки —</i>\n\n"
        + ct.get_evening_smart(target, {0, 1, 2, 3, 4}),
        parse_mode=ParseMode.HTML,
    )

    # ── 📊 20:00 — Недельный итог (если milestone день) ───────
    if target in WEEKLY_MILESTONE_DAYS:
        await update.message.reply_text(
            build_weekly_milestone_screen(uid),
            parse_mode=ParseMode.HTML,
        )


async def cmd_reset_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args      = context.args
    target_id = int(args[0]) if args and args[0].isdigit() else update.effective_user.id
    db.reset_user(target_id)
    await update.message.reply_text(f"🛠 Пользователь {target_id} сброшен.")


async def cmd_grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вручную подтверждает оплату — для тестирования без реального платежа."""
    if not is_admin(update.effective_user.id):
        return
    args      = context.args
    target_id = int(args[0]) if args and args[0].isdigit() else update.effective_user.id
    db.register_user(target_id, None, "Тест")
    db.save_payment(
        user_id=target_id,
        charge_id=f"test_{target_id}",
        participation_fee=0,
        stake_amount=0,
    )
    await update.message.reply_text(f"✅ Оплата подтверждена для {target_id}. /start — онбординг.")


async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает состояние пользователя и версию бота."""
    if not is_admin(update.effective_user.id):
        return
    uid = update.effective_user.id
    paid = db.is_payment_confirmed(uid)
    step = db.get_onboarding_step(uid)
    started = db.is_program_started(uid)
    day = db.get_current_day(uid) if started else "—"
    completed = db.get_completed_tasks(uid, day) if started and day != "—" else set()

    lines = [
        f"🛠 Debug · <b>{VERSION}</b>",
        f"user_id: <code>{uid}</code>",
        f"paid: {paid}",
        f"onboarding: {step}",
        f"started: {started}",
        f"day: {day}",
        f"completed tasks: {sorted(completed)}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран задач принудительно (для проверки дизайна)."""
    if not is_admin(update.effective_user.id):
        return
    uid = update.effective_user.id
    if not db.is_payment_confirmed(uid):
        await update.message.reply_text("Сначала /grant")
        return
    if not db.is_program_started(uid):
        await update.message.reply_text("Программа не началась. Используй /setday 1")
        return
    day = db.get_current_day(uid)
    user_row = db.get_user(uid)
    completed = db.get_completed_tasks(uid, day)
    tracker_msg = await update.message.reply_text(
        build_today_screen(user_row, day, completed),
        parse_mode=ParseMode.HTML,
        reply_markup=today_markup(uid, day, completed)
    )
    db.save_tracker_message(uid, day, tracker_msg.message_id)


# ── Сборка приложения ────────────────────────────────────────

async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start",      "🦥 Начать"),
        BotCommand("today",      "📋 Задачи на сегодня"),
        BotCommand("progress",   "📊 Прогресс"),
    ])


async def cmd_getdb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет файл базы данных в чат (только для admin)."""
    if not is_admin(update.effective_user.id):
        return
    db_path = db.DB_PATH
    if not Path(db_path).exists():
        await update.message.reply_text("❌ Файл базы не найден.")
        return
    size_kb = Path(db_path).stat().st_size // 1024
    await update.message.reply_document(
        document=open(db_path, "rb"),
        filename=f"zarik_{dt.now().strftime('%Y%m%d_%H%M')}.db",
        caption=f"🗄 База данных · {size_kb} КБ",
    )


def build_app() -> Application:
    db.init_db()
    app = (
        Application.builder()
        .token(PROGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("today",      cmd_today))
    app.add_handler(CommandHandler("progress",   cmd_progress))
    app.add_handler(CommandHandler("admin",      cmd_admin))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(CommandHandler("getdb",      cmd_getdb))
    app.add_handler(CommandHandler("setday",     cmd_setday))
    app.add_handler(CommandHandler("reset_user", cmd_reset_user))
    app.add_handler(CommandHandler("grant",      cmd_grant))
    app.add_handler(CommandHandler("debug",      cmd_debug))
    app.add_handler(CommandHandler("screen",     cmd_screen))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    jq = app.job_queue
    jq.run_repeating(job_morning,   interval=3600, first=10,  name="morning")
    jq.run_repeating(job_afternoon, interval=3600, first=30,  name="afternoon")
    jq.run_repeating(job_evening,   interval=3600, first=50,  name="evening")
    jq.run_repeating(job_weekly,    interval=3600, first=70,  name="weekly")

    return app


if __name__ == "__main__":
    build_app().run_polling(drop_pending_updates=True)
