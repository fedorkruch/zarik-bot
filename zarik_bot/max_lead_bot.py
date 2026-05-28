"""
max_lead_bot.py — лид-бот для Мессенджера MAX (аналог @Shagov77_bot).

Воронка:
  1. bot_started → подписка на канал
  2. «Я подписался» → трекер-подарок (ссылка) + вопрос через 20 сек
  3. «Всё получилось» / «Нет» → знакомство с программой
  4. +10 сек → оффер с кнопкой «Купить»
  5. Follow-up через 2 / 3 / 7 дней
  6. После покупки → ссылка на основной бот

Переменные окружения:
  MAX_LEAD_BOT_TOKEN     — токен лид-бота в MAX
  MAX_ADMIN_USER_ID      — MAX user_id администратора
  MAX_PROGRAM_BOT_URL    — ссылка на основной бот (max.ru/…)
  WEBAPP_URL             — URL мини-аппа
  PAYMENT_URL            — URL страницы оплаты (ЮКасса / Tinkoff)
  MAX_LEAD_WEBHOOK_PATH  — путь вебхука, по умолчанию /webhook/max-lead
"""
import asyncio
import logging
import os
import time as _time

from max_client import MaxClient, _btn_callback, _btn_link
import database as db

logger = logging.getLogger(__name__)

# ── Конфигурация ──────────────────────────────────────────────
MAX_LEAD_TOKEN      = os.environ.get("MAX_LEAD_BOT_TOKEN", "")
MAX_ADMIN_USER_ID   = int(os.environ.get("MAX_ADMIN_USER_ID", "0"))
MAX_PROGRAM_BOT_URL = os.environ.get("MAX_PROGRAM_BOT_URL", "")
WEBAPP_URL          = os.environ.get("WEBAPP_URL", "")
PAYMENT_URL         = os.environ.get("PAYMENT_URL", "")
WEBHOOK_PATH        = os.environ.get("MAX_LEAD_WEBHOOK_PATH", "/webhook/max-lead")

# ── Глобальный клиент ─────────────────────────────────────────
_client: MaxClient | None = None

def get_client() -> MaxClient:
    global _client
    if _client is None:
        _client = MaxClient(MAX_LEAD_TOKEN)
    return _client

# ── Тексты ────────────────────────────────────────────────────

WELCOME_TEXT = (
    "Привет! 🦥 Я Зарик — ленивый, но результативный.\n\n"
    "Помогаю людям меняться мягко — без насилия над собой.\n\n"
    "Подпишись на канал **@kabanovofficial** — и я пришлю тебе подарок 🎁"
)

SUBSCRIBE_TEXT = (
    "Подпишись на канал — там я рассказываю, как меняться без боли 🦥\n\n"
    "👉 t.me/kabanovofficial\n\n"
    "Как подпишешься — нажми кнопку ниже 👇"
)

TRACKER_TEXT = (
    "🎁 Держи трекер привычек — мой подарок!\n\n"
    "Открой его и попробуй поставить задачи на сегодня 👇"
)

TRACKER_QUESTION_TEXT = (
    "Ну как, всё получилось с трекером? 🦥"
)

TRACKER_NO_TEXT = (
    "Понял, бывает 😅\n\n"
    "Вот инструкция:\n"
    "**iOS:** Safari → кнопка «Поделиться» → «На экран Домой»\n"
    "**Android:** Chrome → меню ⋮ → «Добавить на главный экран»\n\n"
    "Попробуй ещё раз — должно получиться 👇"
)

INTRO_TEXT = (
    "Супер, рад что всё получилось! 🎉\n\n"
    "Хочу рассказать тебе про одну вещь, которая изменила мою жизнь.\n\n"
    "Это **77 Soft Challenge** — 77 дней без алкоголя, без фастфуда, "
    "с ежедневным спортом, чтением и водой. Ничего жёсткого.\n\n"
    "Просто мягкий сдвиг — каждый день по чуть-чуть."
)

OFFER_TEXT = (
    "🦥 **77 Soft Challenge — старт!**\n\n"
    "Что включено:\n"
    "• Ежедневные задания и трекер в боте\n"
    "• Утренние и вечерние чекины\n"
    "• Персональные тренировки по нарастающей\n"
    "• Поддержка и мотивация от Зарика\n\n"
    "**Стоимость: 1990 ₽**\n\n"
    "Нажми «Начать» — и вперёд 👇"
)

PURCHASED_TEXT = (
    "🎉 Ура, ты в игре!\n\n"
    "Переходи в основной бот — там тебя уже ждут 👇"
)

FOLLOW_2_TEXT = (
    "🦥 Привет! Как трекер — пользуешься?\n\n"
    "Если понравилась идея — присоединяйся к 77 Soft Challenge.\n"
    "Ещё не поздно начать 👇"
)

FOLLOW_3_TEXT = (
    "Заметил, что ты ещё не начал программу.\n\n"
    "Понимаю — начать всегда тяжело. Но у нас никакого насилия 🦥\n"
    "Просто 5 маленьких задач в день. Без боли."
)

FOLLOW_7_TEXT = (
    "Последний раз пишу — не хочу надоедать 🙂\n\n"
    "Если передумаешь — я здесь. Трекером пользуйся, он навсегда твой."
)


def _subscribe_buttons() -> list[list[dict]]:
    return [[_btn_callback("✅ Я подписался", "sub_check")]]


def _tracker_question_buttons() -> list[list[dict]]:
    return [[
        _btn_callback("👍 Всё получилось", "tracker_yes"),
        _btn_callback("Нет, не вышло", "tracker_no"),
    ]]


def _offer_buttons() -> list[list[dict]]:
    buttons = []
    if PAYMENT_URL:
        buttons.append([_btn_link("🚀 Начать — 1990 ₽", PAYMENT_URL)])
    if MAX_PROGRAM_BOT_URL:
        buttons.append([_btn_link("💬 Основной бот", MAX_PROGRAM_BOT_URL)])
    return buttons or [[_btn_callback("🚀 Начать", "buy_now")]]


def _follow_buttons() -> list[list[dict]]:
    buttons = []
    if PAYMENT_URL:
        buttons.append([_btn_link("Присоединиться", PAYMENT_URL)])
    return buttons


def _program_bot_buttons() -> list[list[dict]]:
    if MAX_PROGRAM_BOT_URL:
        return [[_btn_link("Открыть основной бот 🦥", MAX_PROGRAM_BOT_URL)]]
    return []


# ── Обработчики событий ───────────────────────────────────────

async def on_bot_started(max_user_id: int, username: str, first_name: str):
    logger.info(f"MAX lead on_bot_started: sending to user_id={max_user_id}")
    bot = get_client()
    db.upsert_max_lead(max_user_id, username, first_name)
    result = await bot.send_message(max_user_id, SUBSCRIBE_TEXT, buttons=_subscribe_buttons())
    logger.info(f"MAX lead send_message result: {result}")


async def on_callback(max_user_id: int, callback_id: str, payload: str,
                      username: str, first_name: str):
    bot = get_client()

    if payload == "sub_check":
        await bot.answer_callback(callback_id)
        db.upsert_max_lead(max_user_id, username, first_name)
        db.mark_max_lead_subscribed(max_user_id)

        # Отправляем трекер
        tracker_url = f"{WEBAPP_URL}/tracker" if WEBAPP_URL else "https://t.me/shagov77_bot"
        await bot.send_message(
            max_user_id, TRACKER_TEXT,
            buttons=[[_btn_link("📋 Открыть трекер", tracker_url)]]
        )
        db.mark_max_lead_tracker_sent(max_user_id)

        # Через 20 сек — вопрос о трекере
        asyncio.get_event_loop().call_later(
            20,
            lambda: asyncio.create_task(
                bot.send_message(max_user_id, TRACKER_QUESTION_TEXT,
                                 buttons=_tracker_question_buttons())
            )
        )

    elif payload == "tracker_yes":
        await bot.answer_callback(callback_id)
        db.mark_max_lead_tracker_reply(max_user_id, yes=True)
        await bot.send_message(max_user_id, INTRO_TEXT)
        # Через 10 сек — оффер
        asyncio.get_event_loop().call_later(
            10,
            lambda: asyncio.create_task(
                _send_offer(max_user_id)
            )
        )

    elif payload == "tracker_no":
        await bot.answer_callback(callback_id)
        db.mark_max_lead_tracker_reply(max_user_id, yes=False)
        tracker_url = f"{WEBAPP_URL}/tracker" if WEBAPP_URL else "https://t.me/shagov77_bot"
        await bot.send_message(
            max_user_id, TRACKER_NO_TEXT,
            buttons=[[_btn_link("📋 Попробовать снова", tracker_url)]]
        )

    elif payload == "buy_now":
        await bot.answer_callback(callback_id, "Скоро добавим оплату!")


async def _send_offer(max_user_id: int):
    bot = get_client()
    await bot.send_message(max_user_id, OFFER_TEXT, buttons=_offer_buttons())
    db.mark_max_lead_pitch_sent(max_user_id)


async def on_message(max_user_id: int, text: str, username: str, first_name: str):
    """Текстовые сообщения — только для команд администратора."""
    if max_user_id != MAX_ADMIN_USER_ID:
        return
    bot = get_client()

    if text.startswith("/stats"):
        leads = db.get_max_leads_for_followup(0) or []
        total = len(db.get_client_stats_max() if hasattr(db, "get_client_stats_max") else [])
        await bot.send_message(max_user_id, f"📊 MAX лид-бот\nВсего лидов: {total}")

    elif text.startswith("/broadcast "):
        msg = text[len("/broadcast "):]
        _schedule_broadcast(msg)
        await bot.send_message(max_user_id, "✅ Рассылка поставлена в очередь")


def _schedule_broadcast(text: str):
    """Заглушка для будущей рассылки."""
    logger.info(f"Broadcast queued: {text[:50]}")


# ── Dispatcher (точка входа для вебхука) ─────────────────────

async def process_update(data: dict):
    """Обрабатывает один входящий объект Update от MAX."""
    update_type = data.get("update_type", "")
    logger.info(f"MAX lead update: type={update_type!r} keys={list(data.keys())}")

    try:
        if update_type == "bot_started":
            user = data.get("user", {})
            max_user_id = user.get("user_id", 0)
            logger.info(f"MAX lead bot_started: user_id={max_user_id} user={user}")
            await on_bot_started(
                max_user_id=max_user_id,
                username=user.get("username", ""),
                first_name=user.get("name", ""),
            )

        elif update_type == "message_created":
            msg = data.get("message", {})
            sender = msg.get("sender", {})
            text = msg.get("body", {}).get("text", "") or ""
            logger.info(f"MAX lead message: user_id={sender.get('user_id')} text={text!r}")
            await on_message(
                max_user_id=sender.get("user_id", 0),
                text=text,
                username=sender.get("username", ""),
                first_name=sender.get("name", ""),
            )

        elif update_type == "message_callback":
            cb = data.get("callback", {})
            user = cb.get("user", {})
            payload = cb.get("payload", "")
            logger.info(f"MAX lead callback: user_id={user.get('user_id')} payload={payload!r}")
            await on_callback(
                max_user_id=user.get("user_id", 0),
                callback_id=cb.get("callback_id", ""),
                payload=payload,
                username=user.get("username", ""),
                first_name=user.get("name", ""),
            )

        else:
            logger.info(f"MAX lead unhandled update_type={update_type!r} data={data}")

    except Exception:
        logger.exception(f"Error processing MAX lead update: {update_type}")


# ── Инициализация вебхука ─────────────────────────────────────

async def setup(webapp_base_url: str):
    """Регистрирует вебхук в MAX. Вызывается при старте webapp_server."""
    if not MAX_LEAD_TOKEN:
        logger.warning("MAX_LEAD_BOT_TOKEN не задан — MAX лид-бот не запущен")
        return
    bot = get_client()
    me = await bot.get_me()
    logger.info(f"MAX лид-бот: {me.get('name', '?')} (@{me.get('username', '?')})")
    webhook_url = f"{webapp_base_url.rstrip('/')}{WEBHOOK_PATH}"
    await bot.setup_webhook(webhook_url)
    logger.info(f"MAX лид-бот вебхук: {webhook_url}")
