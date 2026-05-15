"""
program_bot.py — Программный бот Зарика (@Zarik_Lazy_Bot): онбординг + 77-дневная программа.
Проверяет факт оплаты через БД по user_id. Без оплаты — не пускает.
"""
import html
import logging
import os
import re
import time as _time
from datetime import datetime as dt
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
    all_done_keyboard,
    tab_only_keyboard,
    tab_bar,
    timezone_keyboard,
    reps_keyboard,
    MAIN_MENU,
    START_MENU,
    TIMEZONES,
)
from workout import get_workout

# ── Конфигурация ──────────────────────────────────────────────
PROGRAM_BOT_TOKEN = os.environ.get("PROGRAM_BOT_TOKEN") or os.environ["BOT_TOKEN"]
LEAD_BOT_USERNAME = os.environ.get("LEAD_BOT_USERNAME", "Shagov77_bot")
ADMIN_ID          = int(os.environ.get("ADMIN_ID", "283760217"))
TOTAL_DAYS        = 77

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_last_start: dict[int, float] = {}

BAR_WIDTH = 20
DIV = "· · · · · · · · · · · ·"


# ── Утилиты ───────────────────────────────────────────────────

def make_progress_bar(day: int, total: int = TOTAL_DAYS, width: int = BAR_WIDTH) -> str:
    if total == 0:
        return "·" * width + "  0%"
    pct = round(day / total * 100)
    filled = round(day / total * width)
    return "●" * filled + "·" * (width - filled) + f"  {pct}%"


def make_mini_bar(value: int, total: int, width: int = 10) -> str:
    if total == 0:
        return "·" * width
    filled = round(value / total * width)
    return "●" * filled + "·" * (width - filled)


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


# ── Построители экранов (HTML) ────────────────────────────────

def build_today_screen(user_row, day: int, completed: set) -> str:
    """Экран «Сегодня» — утреннее послание + чеклист."""
    morning_text = h(ct.get_morning(day))
    workout = get_workout(dict(user_row), day)
    done = len(completed)
    percentile, _ = ct.get_planet_percentile(day - 1)
    bar = make_progress_bar(day - 1)
    task_bar = "●" * done + "·" * (5 - done)

    task_items = [
        ("💪", "Тренировка"),
        ("💧", "Вода · 2 л / 8 стаканов"),
        ("📚", "Чтение · 10 страниц"),
        ("🥗", "Без фастфуда и снеков"),
        ("🚫", "День без алкоголя"),
    ]

    lines = [
        f"<b>☀️  День {day} из {TOTAL_DAYS}</b>  ·  {h(percentile)} планеты",
        bar,
        "",
        f"<i>{morning_text}</i>",
        "",
        DIV,
        "<b>📋  Отметь что выполнил сегодня 👇</b>",
        "",
    ]

    for i, (icon, label) in enumerate(task_items):
        mark = "✅" if i in completed else "⬜"
        lines.append(f"{mark}  {icon}  {label}")
        if i == 0:
            for wline in h(workout["description"]).split("\n"):
                lines.append(f"      <i>{wline}</i>")

    lines += [
        "",
        DIV,
        f"Прогресс дня:  {task_bar}  {done} / 5",
    ]
    return "\n".join(lines)


def build_progress_screen(user_id: int) -> str:
    """Экран «Итоги» — общий прогресс и планетарный рейтинг."""
    stats = db.get_stats(user_id)
    day   = stats["current_day"]
    done  = stats["days_completed"]
    streak = stats["streak"]
    percentile, ctx = ct.get_planet_percentile(done)
    bar = make_progress_bar(day - 1)
    next_m = ct.get_next_percentile_milestone(done)

    lines = [
        f"<b>📊  Прогресс · День {day} из {TOTAL_DAYS}</b>",
        "",
        bar,
        "",
        DIV,
        f"✅  Засчитано:      <b>{done}</b> {day_word(done)}",
        f"🔥  Серия:            <b>{streak}</b> {day_word(streak)} подряд",
        f"🌍  Рейтинг:         <b>{h(percentile)}</b> планеты",
    ]
    if next_m:
        d, pct = next_m
        lines.append(f"      <i>ещё {d} {day_word(d)} → {h(pct)}</i>")

    lines += ["", DIV, f"<i>{h(ctx)}</i>"]
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

    if not db.is_payment_confirmed(user.id):
        await update.message.reply_text(not_paid_message(), reply_markup=START_MENU)
        return

    user_row = db.get_user(user.id)
    step     = db.get_onboarding_step(user.id)

    if step == "timezone":
        await update.message.reply_text(
            "🦥 Выбери часовой пояс — буду присылать задания в 6:00 по твоему времени 👇",
            reply_markup=timezone_keyboard()
        )
    elif step == "pushup":
        await update.message.reply_text(
            "🦥 Давай подберём тренировку под тебя.\n\n"
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
            await update.message.reply_text(
                build_today_screen(user_row, day, completed),
                parse_mode=ParseMode.HTML,
                reply_markup=tasks_keyboard(day, completed, active_tab="tasks")
            )
    else:
        await update.message.reply_text(
            "🦥 Привет! Оплата подтверждена — добро пожаловать в программу!\n\n"
            "Выбери часовой пояс, чтобы я присылал задания в 6:00 по твоему времени:",
            reply_markup=timezone_keyboard()
        )


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
    await update.message.reply_text(
        build_today_screen(user_row, day, completed),
        parse_mode=ParseMode.HTML,
        reply_markup=tasks_keyboard(day, completed, active_tab="tasks")
    )


async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not db.is_payment_confirmed(user.id):
        await update.message.reply_text(not_paid_message(), reply_markup=START_MENU)
        return
    await update.message.reply_text(
        build_progress_screen(user.id),
        parse_mode=ParseMode.HTML,
        reply_markup=tab_only_keyboard("progress")
    )


# ── Callback-обработчик ──────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_id = query.from_user.id
    data    = query.data

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
            text   = build_progress_screen(user_id)
            markup = tab_only_keyboard("progress")

        elif tab == "week":
            text   = build_week_screen(user_id)
            markup = tab_only_keyboard("week")

        elif tab == "achievements":
            text   = build_achievements_screen(user_id)
            markup = tab_only_keyboard("achievements")

        else:
            return

        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
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
            db.save_abs_start(user_id, reps)
            db.complete_onboarding(user_id)
            user_row = db.get_user(user_id)
            workout  = get_workout(dict(user_row), 1)
            await query.edit_message_text(
                f"🔥 Пресс: {reps} — красава!\n\n"
                f"Всё записано. Вот твоя тренировка на День 1:\n\n"
                f"{workout['description']}\n\n"
                f"Завтра в 6:00 пришлю первое задание. Отдыхай 🦥"
            )
            await query.message.reply_text(
                "Меню всегда под рукой 👇",
                reply_markup=MAIN_MENU
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


# ── Текстовые сообщения ──────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text    = update.message.text.strip()

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
            reply_markup=tab_only_keyboard("achievements")
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

            text = build_today_screen(user, day, completed)
            if 1 <= missed <= 2:
                last_day = db.get_last_completed_day(user["user_id"])
                text += f"\n\n{ct.get_miss_message(missed, last_day)}"

            await context.bot.send_message(
                chat_id=user["user_id"],
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=tasks_keyboard(day, completed, active_tab="tasks")
            )
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
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=ct.get_afternoon(day, all_done),
                reply_markup=None if all_done else tasks_keyboard(day, completed, active_tab="tasks")
            )
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

            # Сначала текстовое послание
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=ct.get_evening(day, all_done),
            )
            # Затем экран задач если не все выполнены
            if not all_done:
                user_row = db.get_user(user["user_id"])
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=build_today_screen(user_row, day, completed),
                    parse_mode=ParseMode.HTML,
                    reply_markup=tasks_keyboard(day, completed, active_tab="tasks")
                )
        except Exception as e:
            logger.warning(f"Вечер {user['user_id']}: {e}")


async def job_weekly(context: ContextTypes.DEFAULT_TYPE):
    users = db.get_all_active_users()
    for user in users:
        try:
            user_tz  = pytz.timezone(user["timezone"] or "Europe/Moscow")
            local_now = dt.now(user_tz)
            if local_now.weekday() != 6 or local_now.hour != 20:
                continue
            if not db.is_program_started(user["user_id"]):
                continue
            day = db.get_current_day(user["user_id"])
            if day < 1:
                continue
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=build_week_screen(user["user_id"]),
                parse_mode=ParseMode.HTML,
                reply_markup=tab_only_keyboard("week")
            )
        except Exception as e:
            logger.warning(f"Неделя {user['user_id']}: {e}")


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


async def cmd_setday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Использование: /setday 7")
        return
    target = int(args[0])
    if not 1 <= target <= TOTAL_DAYS:
        await update.message.reply_text(f"День должен быть от 1 до {TOTAL_DAYS}")
        return
    db.set_day_for_testing(update.effective_user.id, target)
    user_row  = db.get_user(update.effective_user.id)
    completed = db.get_completed_tasks(update.effective_user.id, target)
    await update.message.reply_text(
        f"🛠 Тест: день {target} установлен\n\n"
        + build_today_screen(user_row, target, completed),
        parse_mode=ParseMode.HTML,
        reply_markup=tasks_keyboard(target, completed, active_tab="tasks")
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


# ── Сборка приложения ────────────────────────────────────────

async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start",      "🦥 Начать"),
        BotCommand("today",      "📋 Задачи на сегодня"),
        BotCommand("progress",   "📊 Прогресс"),
    ])


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
    app.add_handler(CommandHandler("setday",     cmd_setday))
    app.add_handler(CommandHandler("reset_user", cmd_reset_user))
    app.add_handler(CommandHandler("grant",      cmd_grant))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    jq = app.job_queue
    jq.run_repeating(job_morning,   interval=3600, first=10,  name="morning")
    jq.run_repeating(job_afternoon, interval=3600, first=30,  name="afternoon")
    jq.run_repeating(job_evening,   interval=3600, first=50,  name="evening")
    jq.run_repeating(job_weekly,    interval=3600, first=70,  name="weekly")

    return app


if __name__ == "__main__":
    build_app().run_polling(drop_pending_updates=True)
