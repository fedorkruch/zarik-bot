"""
bot.py — Telegram-бот «ТЕО».
Онбординг-машина + роутинг сообщений в Claude API.
"""
import os
import re
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db
import claude_client as claude

logger = logging.getLogger(__name__)

# ── Вводное сообщение ТЕО (фиксированное, сохраняется в историю) ─────────────

ONBOARDING_INTRO = {
    "female": (
        "Привет! Я ТЕО — твоя личная наставница по жизни.\n\n"
        "Я здесь чтобы помочь тебе прийти к своим целям. "
        "Карьера, деньги, здоровье, отношения — любые направления, твой темп.\n\n"
        "Буду честна: я AI. Но это не мешает мне по-настоящему слышать "
        "и помогать двигаться туда, куда ты хочешь.\n\n"
        "Как тебя зовут?"
    ),
    "male": (
        "Привет! Я ТЕО — твой личный наставник по жизни.\n\n"
        "Я здесь чтобы помочь тебе прийти к своим целям. "
        "Карьера, деньги, здоровье, отношения — любые направления, твой темп.\n\n"
        "Буду честен: я AI. Но это не мешает мне по-настоящему слышать "
        "и помогать двигаться туда, куда ты хочешь.\n\n"
        "Как тебя зовут?"
    ),
}

TIME_ASK_MESSAGE = (
    "Последний вопрос — в какое время тебе удобно получать задачи на день?\n\n"
    "Напиши время и часовой пояс, например:\n"
    "08:00 МСК\n"
    "09:30 UTC+5\n"
    "07:00 Новосибирск"
)

# ── Карта часовых поясов ──────────────────────────────────────────────────────

_TZ_MAP = {
    "МСК": "Europe/Moscow",
    "MSK": "Europe/Moscow",
    "МОСКВА": "Europe/Moscow",
    "UTC+3": "Europe/Moscow",
    "UTC+2": "Europe/Kaliningrad",
    "КАЛИНИНГРАД": "Europe/Kaliningrad",
    "UTC+4": "Europe/Samara",
    "САМАРА": "Europe/Samara",
    "UTC+5": "Asia/Yekaterinburg",
    "ЕКАТЕРИНБУРГ": "Asia/Yekaterinburg",
    "UTC+6": "Asia/Omsk",
    "ОМСК": "Asia/Omsk",
    "UTC+7": "Asia/Krasnoyarsk",
    "КРАСНОЯРСК": "Asia/Krasnoyarsk",
    "НОВОСИБИРСК": "Asia/Novosibirsk",
    "UTC+8": "Asia/Irkutsk",
    "ИРКУТСК": "Asia/Irkutsk",
    "UTC+9": "Asia/Yakutsk",
    "ЯКУТСК": "Asia/Yakutsk",
    "UTC+10": "Asia/Vladivostok",
    "ВЛАДИВОСТОК": "Asia/Vladivostok",
    "UTC+11": "Asia/Magadan",
    "МАГАДАН": "Asia/Magadan",
    "UTC+12": "Asia/Kamchatka",
    "КАМЧАТКА": "Asia/Kamchatka",
    "UTC": "UTC",
    "UTC+0": "UTC",
    "UTC+1": "Europe/Warsaw",
    "UTC-5": "America/New_York",
    "UTC-8": "America/Los_Angeles",
}


def _parse_time_message(text: str) -> tuple[str | None, str]:
    """
    Парсит строку вида «09:00 МСК» или «8:30 UTC+5».
    Возвращает (время_HH:MM, tz_строка). При ошибке — (None, 'Europe/Moscow').
    """
    upper = text.strip().upper()

    m = re.search(r"\b(\d{1,2}):(\d{2})\b", upper)
    if not m:
        return None, "Europe/Moscow"

    h, mn = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mn <= 59):
        return None, "Europe/Moscow"

    time_str = f"{h:02d}:{mn:02d}"

    tz = "Europe/Moscow"
    for key, val in _TZ_MAP.items():
        if key in upper:
            tz = val
            break

    return time_str, tz


# ── Хэндлеры ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = db.get_user(user.id)

    if existing and existing.get("onboarding_step") == "complete":
        await update.message.reply_text("Привет! Я здесь. Что происходит? 👋")
        return

    db.upsert_user(user.id, user.username or "", user.first_name or "")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌸 Леди", callback_data="gender:female"),
            InlineKeyboardButton("🎩 Джентльмен", callback_data="gender:male"),
        ]
    ])

    await update.message.reply_text(
        "Привет! Это ТЕО — твой личный наставник по жизни.\n\n"
        "Прежде чем начать — выбери образ ТЕО 👇",
        reply_markup=keyboard,
    )
    db.set_onboarding_step(user.id, "awaiting_gender")


async def handle_gender_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not query.data.startswith("gender:"):
        return

    _, gender = query.data.split(":", 1)
    user_id = query.from_user.id

    db.set_gender(user_id, gender)
    db.set_onboarding_step(user_id, "onboarding")

    # Фиксированное вводное сообщение сохраняем в историю — Claude будет его видеть
    intro = ONBOARDING_INTRO[gender]
    db.save_message(user_id, "assistant", intro)

    await query.message.reply_text(intro)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()

    if not text:
        return

    user_data = db.get_user(user.id)
    if not user_data:
        await cmd_start(update, context)
        return

    step = user_data.get("onboarding_step", "start")

    # ── Ждём выбора пола ──────────────────────────────────────────────────────
    if step == "awaiting_gender":
        await update.message.reply_text(
            "Сначала выбери образ ТЕО кнопками выше 👆"
        )
        return

    # ── Ждём время ────────────────────────────────────────────────────────────
    if step == "awaiting_time":
        time_str, tz = _parse_time_message(text)
        if not time_str:
            await update.message.reply_text(
                "Не могу разобрать время 🙈\n"
                "Попробуй в формате: 09:00 МСК"
            )
            return

        db.set_time_and_timezone(user.id, time_str, tz)

        city = tz.split("/")[-1].replace("_", " ") if "/" in tz else tz
        await update.message.reply_text(
            f"Готово. Задачи буду присылать в {time_str} ({city}).\n\n"
            "Всё, мы начали. Сегодня просто поживи — завтра утром ТЕО пришлёт первые задачи 🌿"
        )
        return

    # ── Все остальные состояния → Claude ─────────────────────────────────────
    await update.message.chat.send_action("typing")

    try:
        response_text, tool_calls = await claude.chat(user.id, text)
    except Exception as e:
        logger.error(f"[CLAUDE ERROR] user={user.id}: {e}")
        await update.message.reply_text(
            "Что-то пошло не так на моей стороне. Попробуй ещё раз через секунду."
        )
        return

    if response_text:
        await update.message.reply_text(response_text)

    # Если Claude сохранил план — переходим к сбору времени
    plan_saved = any(tc["name"] == "save_weekly_plan" for tc in tool_calls)
    if plan_saved:
        db.set_onboarding_step(user.id, "awaiting_time")
        await update.message.reply_text(TIME_ASK_MESSAGE)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Только для разработки — сброс пользователя."""
    user_id = update.effective_user.id
    # Сбрасываем шаг онбординга и чистим историю
    db.set_onboarding_step(user_id, "start")
    db.clear_messages(user_id)
    await update.message.reply_text("Сброшено. /start чтобы начать заново.")


# ── Сборка приложения ──────────────────────────────────────────────────────────

def build_app() -> Application:
    db.init_db()

    token = os.getenv("TEO_BOT_TOKEN")
    if not token:
        raise ValueError("TEO_BOT_TOKEN не задан в переменных окружения")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CallbackQueryHandler(handle_gender_callback, pattern=r"^gender:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app
