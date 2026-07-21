"""
bot.py — Telegram-бот «ТЕО».
Онбординг-машина + роутинг сообщений в Claude API.
"""
import hashlib
import hmac
import os
import re
import time
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

WEBAPP_URL    = os.getenv("WEBAPP_URL", "")
TEO_BOT_TOKEN = os.getenv("TEO_BOT_TOKEN", "")


def _tracker_url(user_id: int) -> str:
    """Генерирует подписанный URL трекера (действует 10 минут)."""
    ts  = int(time.time())
    sig = hmac.new(TEO_BOT_TOKEN.encode(), f"{user_id}:{ts}".encode(), hashlib.sha256).hexdigest()
    return f"{WEBAPP_URL}/teo?uid={user_id}&ts={ts}&sig={sig}"

import database as db
import claude_client as claude

logger = logging.getLogger(__name__)

# ── Стартовое сообщение (intro + вопрос про имя в одном) ─────────────────────

START_MESSAGE = (
    "Привет. Я ТЕО — твой личный наставник.\n\n"
    "Я здесь чтобы помочь тебе прийти к тому чего ты по-настоящему хочешь. "
    "Неважно в какой сфере — карьера, деньги, здоровье, отношения, отдых, смысл. "
    "Для меня нет правильных или неправильных тем.\n\n"
    "Мы будем работать вместе — разберёмся что важно именно тебе, разложим это на шаги "
    "и будем двигаться. Без давления, без осуждения.\n\n"
    "Как тебя зовут?"
)

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

    # Сохраняем intro в историю — Claude видит контекст с первого сообщения
    db.save_message(user.id, "assistant", START_MESSAGE)
    db.set_onboarding_step(user.id, "onboarding")

    await update.message.reply_text(START_MESSAGE)


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

        # Кнопка трекера (если настроен WEBAPP_URL)
        if WEBAPP_URL and TEO_BOT_TOKEN:
            url = _tracker_url(user.id)
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("📊 Открыть трекер", web_app=WebAppInfo(url=url))
            ]])
            await update.message.reply_text(
                "Все цели и задачи уже в трекере — открывай в любой момент:",
                reply_markup=keyboard,
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app
