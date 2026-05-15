"""
program_bot.py — Программный бот Зарика (@Shagov77_bot): онбординг + 77-дневная программа.
Проверяет факт оплаты через БД по user_id. Без оплаты — не пускает.

Переменные окружения:
  PROGRAM_BOT_TOKEN  — токен @Shagov77_bot
  LEAD_BOT_USERNAME  — @username лид-бота (для сообщения "не оплачено")
  ADMIN_ID           — Telegram ID администратора
  DATA_DIR           — папка для zarik.db
"""
import logging
import os
import time as _time
from datetime import datetime as dt
import pytz

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
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
    timezone_keyboard,
    reps_keyboard,
    progress_keyboard,
    MAIN_MENU,
    START_MENU,
    TIMEZONES,
)
from workout import get_workout

# ── Конфигурация ──────────────────────────────────────────────
PROGRAM_BOT_TOKEN  = os.environ.get("PROGRAM_BOT_TOKEN") or os.environ["BOT_TOKEN"]  # токен @myeasystartbot
LEAD_BOT_USERNAME  = os.environ.get("LEAD_BOT_USERNAME", "Shagov77_bot")  # лид-бот
ADMIN_ID           = int(os.environ.get("ADMIN_ID", "283760217"))
TOTAL_DAYS         = 77

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_last_start: dict[int, float] = {}

BAR_WIDTH = 20


# ── Утилиты ───────────────────────────────────────────────────

def make_progress_bar(day: int, total: int = TOTAL_DAYS) -> str:
    pct = round(day / total * 100)
    filled = round(day / total * BAR_WIDTH)
    bar = "▓" * filled + "░" * (BAR_WIDTH - filled)
    return f"{bar} {pct}%"


def build_checklist_message(user_row, day: int, completed: set) -> str:
    morning_text = ct.get_morning(day)
    workout = get_workout(dict(user_row), day)
    bar = make_progress_bar(day - 1)
    done = len(completed)

    task_items = [
        ("💪", "Тренировка"),
        ("💧", "Вода · 2 литра / 8 стаканов"),
        ("📚", "Чтение · 10 страниц"),
        ("🥗", "Без фастфуда, чипсов и снеков"),
        ("🚫", "День без алкоголя"),
    ]

    lines = [
        morning_text,
        "",
        f"День {day} из {TOTAL_DAYS}",
        bar,
        "",
    ]

    for i, (icon, label) in enumerate(task_items):
        mark = "✅" if i in completed else "⬜"
        lines.append(f"{mark} {icon} {label}")
        if i == 0:
            for detail_line in workout["description"].split("\n"):
                lines.append(f"   {detail_line}")

    lines.append("")
    lines.append(f"Прогресс дня: {done} из 5")

    return "\n".join(lines)


def build_progress_text(user_id: int) -> str:
    stats = db.get_stats(user_id)
    day = stats["current_day"]
    done = stats["days_completed"]
    streak = stats["streak"]
    percentile, percentile_ctx = ct.get_planet_percentile(done)

    def day_word(n):
        if n == 1:
            return "день"
        if 2 <= n <= 4:
            return "дня"
        return "дней"

    bar = make_progress_bar(day - 1)
    next_milestone = ct.get_next_percentile_milestone(done)

    lines = [
        f"🦥 Прогресс · День {day} из {TOTAL_DAYS}",
        bar,
        "",
        f"✅ Засчитано: {done} {day_word(done)}",
        f"🔥 Серия: {streak} {day_word(streak)} подряд",
        f"🌍 {percentile} планеты",
    ]

    if next_milestone:
        days_to_next, next_pct = next_milestone
        lines.append(f"   ещё {days_to_next} {day_word(days_to_next)} — {next_pct}")

    return "\n".join(lines)


def not_paid_message() -> str:
    lead = f"\n\n👉 @{LEAD_BOT_USERNAME}" if LEAD_BOT_USERNAME else ""
    return (
        "🦥 Участие в программе не оплачено.\n\n"
        "Вернись к боту регистрации, пройди воронку и оплати участие — "
        "после этого возвращайся сюда.{lead}"
    ).format(lead=lead)


# ── Команда /start ────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Защита от спама
    now = _time.time()
    if now - _last_start.get(user.id, 0) < 5:
        return
    _last_start[user.id] = now

    # Проверка оплаты
    if not db.is_payment_confirmed(user.id):
        await update.message.reply_text(not_paid_message(), reply_markup=START_MENU)
        return

    user_row = db.get_user(user.id)
    step = db.get_onboarding_step(user.id)

    if step == "timezone":
        await update.message.reply_text(
            "🦥 Выбери часовой пояс, чтобы я присылал задания в 6:00 по твоему времени 👇",
            reply_markup=timezone_keyboard()
        )

    elif step == "pushup":
        await update.message.reply_text(
            "🦥 Давай подберём тренировку под тебя.\n\n"
            "Сколько отжиманий можешь сделать прямо сейчас, без подготовки?",
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
            day = db.get_current_day(user.id)
            completed = db.get_completed_tasks(user.id, day)
            await update.message.reply_text(
                build_checklist_message(user_row, day, completed),
                reply_markup=tasks_keyboard(day, completed)
            )

    else:
        # Оплата есть, но онбординг не начат — первый /start в программном боте
        await update.message.reply_text(
            "🦥 Привет! Оплата подтверждена — добро пожаловать в программу!\n\n"
            "Последний шаг — выбери часовой пояс, чтобы я присылал задания в 6:00 по твоему времени:",
            reply_markup=timezone_keyboard(),
        )


# ── Команды меню ──────────────────────────────────────────────

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not db.is_payment_confirmed(user.id):
        await update.message.reply_text(not_paid_message())
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

    day = db.get_current_day(user.id)
    completed = db.get_completed_tasks(user.id, day)
    await update.message.reply_text(
        build_checklist_message(user_row, day, completed),
        reply_markup=tasks_keyboard(day, completed)
    )


async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not db.is_payment_confirmed(user.id):
        await update.message.reply_text(not_paid_message())
        return
    await update.message.reply_text(build_progress_text(user.id))


# ── Callback-обработчик ──────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    # Выбор часового пояса
    if data.startswith("tz:"):
        await query.answer()
        tz_name = data.split(":", 1)[1]
        db.set_user_timezone(user_id, tz_name)
        tz_label = next((label for label, tz in TIMEZONES if tz == tz_name), tz_name)
        await query.edit_message_text(f"✅ Часовой пояс: {tz_label}")
        await query.message.reply_text(
            "🦥 Теперь давай подберём тренировку под тебя.\n\n"
            "Сколько отжиманий можешь сделать прямо сейчас, без подготовки?",
            reply_markup=reps_keyboard("pushup")
        )
        return

    # Онбординг: количество повторений
    if data.startswith("reps:"):
        await query.answer()
        parts = data.split(":")
        exercise = parts[1]
        reps = int(parts[2])

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
            workout = get_workout(dict(user_row), 1)

            await query.edit_message_text(
                f"🔥 Пресс: {reps} — красава!\n\n"
                f"Всё записано. Вот твоя тренировка на День 1:\n\n"
                f"{workout['description']}\n\n"
                f"Завтра в 6:00 пришлю первое задание. Отдыхай — завтра начинаем! 🦥"
            )
            await query.message.reply_text(
                "Меню всегда под рукой 👇",
                reply_markup=MAIN_MENU
            )
        return

    # Задачи (чеклист)
    if data.startswith("task:"):
        _, day_str, task_str = data.split(":")
        day = int(day_str)
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
                build_checklist_message(user_row, day, completed),
                reply_markup=all_done_keyboard()
            )
            evening_text = ct.get_evening(day, all_done=True)
            await query.message.reply_text(f"{evening_text}\n\n_День {day} засчитан! 🎉_")

            # Финальное сообщение на день 77
            if day == TOTAL_DAYS:
                await query.message.reply_text(ct.FINAL_MESSAGE, parse_mode=ParseMode.MARKDOWN)

            # Ачивки
            stats = db.get_stats(user_id)
            new_achievements = ct.check_achievements(stats["days_completed"])
            for ach_id in new_achievements:
                if not db.has_achievement(user_id, ach_id):
                    db.award_achievement(user_id, ach_id)
                    await query.message.reply_text(ct.get_achievement_text(ach_id))
        else:
            await query.edit_message_text(
                build_checklist_message(user_row, day, completed),
                reply_markup=tasks_keyboard(day, completed)
            )
        return

    # Прогресс
    if data == "progress":
        await query.answer()
        await query.message.reply_text(build_progress_text(user_id))
    elif data == "noop":
        await query.answer()


# ── Текстовые сообщения ──────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Кнопка «Начать» работает как /start
    if text == "🦥 Начать":
        await cmd_start(update, context)
        return

    if not db.is_payment_confirmed(user_id):
        await update.message.reply_text(not_paid_message(), reply_markup=START_MENU)
        return

    if text == "📋 Мои задачи на сегодня":
        await cmd_today(update, context)
    elif text == "📊 Мой прогресс":
        await cmd_progress(update, context)
    elif text == "❓ Помощь":
        await update.message.reply_text(
            "🦥 Как пользоваться Зариком:\n\n"
            "📋 Мои задачи на сегодня — открыть чеклист\n"
            "📊 Мой прогресс — статистика и позиция в топе планеты\n\n"
            "Задачи отмечаются кнопками прямо в сообщении.\n"
            "При любых вопросах — напиши сюда!",
            reply_markup=MAIN_MENU
        )
    else:
        await update.message.reply_text(
            "🦥 Используй кнопки меню 👇",
            reply_markup=MAIN_MENU
        )


# ── Планировщик: утро (6:00) ──────────────────────────────────

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

            morning_msg = build_checklist_message(user, day, completed)

            if 1 <= missed <= 2:
                last_day = db.get_last_completed_day(user["user_id"])
                morning_msg += f"\n\n{ct.get_miss_message(missed, last_day)}"

            await context.bot.send_message(
                chat_id=user["user_id"],
                text=morning_msg,
                reply_markup=tasks_keyboard(day, completed)
            )

        except Exception as e:
            logger.warning(f"Утро {user['user_id']}: {e}")


# ── Планировщик: день (14:00) ─────────────────────────────────

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
            all_done = len(completed) >= 5
            text = ct.get_afternoon(day, all_done)

            await context.bot.send_message(
                chat_id=user["user_id"],
                text=text,
                reply_markup=None if all_done else tasks_keyboard(day, completed)
            )

        except Exception as e:
            logger.warning(f"День {user['user_id']}: {e}")


# ── Планировщик: вечер (21:00) ────────────────────────────────

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
            all_done = len(completed) >= 5
            text = ct.get_evening(day, all_done)

            await context.bot.send_message(
                chat_id=user["user_id"],
                text=text,
                reply_markup=None if all_done else tasks_keyboard(day, completed)
            )

        except Exception as e:
            logger.warning(f"Вечер {user['user_id']}: {e}")


# ── Планировщик: воскресенье 20:00 ───────────────────────────

async def job_weekly(context: ContextTypes.DEFAULT_TYPE):
    users = db.get_all_active_users()
    for user in users:
        try:
            user_tz = pytz.timezone(user["timezone"] or "Europe/Moscow")
            local_now = dt.now(user_tz)

            if local_now.weekday() != 6 or local_now.hour != 20:
                continue
            if not db.is_program_started(user["user_id"]):
                continue

            day = db.get_current_day(user["user_id"])
            week_num = (day - 1) // 7 + 1
            if week_num < 1:
                continue

            group_stats = db.get_group_stats()
            all_completed = db.get_completed_days_set(user["user_id"])
            week_start = (week_num - 1) * 7 + 1
            week_completed = {d for d in all_completed if week_start <= d <= day}

            text = ct.build_weekly_stats(day, week_completed, all_completed, group_stats)
            await context.bot.send_message(chat_id=user["user_id"], text=text)

        except Exception as e:
            logger.warning(f"Неделя {user['user_id']}: {e}")


# ── Админ-команды ─────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    count = db.get_user_count()
    total_stake = db.get_total_stake() // 100
    active = db.get_all_active_users()
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
    user_row = db.get_user(update.effective_user.id)
    completed = db.get_completed_tasks(update.effective_user.id, target)
    await update.message.reply_text(
        f"🛠 Тест: день {target} установлен\n\n"
        + build_checklist_message(user_row, target, completed),
        reply_markup=tasks_keyboard(target, completed)
    )


async def cmd_reset_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    target_id = int(args[0]) if args and args[0].isdigit() else update.effective_user.id
    db.reset_user(target_id)
    await update.message.reply_text(f"🛠 Пользователь {target_id} сброшен.")


# ── Сборка приложения ────────────────────────────────────────

def build_app() -> Application:
    db.init_db()
    app = Application.builder().token(PROGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("today",      cmd_today))
    app.add_handler(CommandHandler("progress",   cmd_progress))
    app.add_handler(CommandHandler("admin",      cmd_admin))
    app.add_handler(CommandHandler("setday",     cmd_setday))
    app.add_handler(CommandHandler("reset_user", cmd_reset_user))
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
