"""
lead_bot.py — Лид-бот Зарика: воронка продаж, подписка на канал, оплата ЮКасса.
После успешной оплаты → ссылка на программный бот (@Shagov77_bot).

Переменные окружения:
  LEAD_BOT_TOKEN          — токен лид-бота (или BOT_TOKEN как fallback)
  PROVIDER_TOKEN          — токен ЮКасса
  CHANNEL_USERNAME        — @username канала для подписки (опционально)
  PROGRAM_BOT_USERNAME    — username программного бота (по умолчанию Shagov77_bot)
  PARTICIPATION_FEE_KOPECKS — участие в копейках (по умолчанию 1000 = 10₽)
  STAKE_MIN_RUB           — минимальная ставка (по умолчанию 500)
  ADMIN_ID                — Telegram ID администратора
"""
import json
import logging
import os
import time as _time

from telegram import (
    ChatMember, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice, Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters,
)

import database as db

# ── Конфигурация ──────────────────────────────────────────────
LEAD_BOT_TOKEN       = os.environ["SHAGOV77_BOT_TOKEN"]           # токен @Shagov77_bot
PROVIDER_TOKEN       = os.environ["PROVIDER_TOKEN"]
PARTICIPATION_FEE    = int(os.environ.get("PARTICIPATION_FEE_KOPECKS", "1000"))  # 10₽
STAKE_MIN_RUB        = int(os.environ.get("STAKE_MIN_RUB", "500"))
CHANNEL_USERNAME     = os.environ.get("CHANNEL_USERNAME", "")   # @zarik_channel
PROGRAM_BOT_USERNAME = os.environ.get("PROGRAM_BOT_USERNAME", "myeasystartbot")  # программный бот
ADMIN_ID             = int(os.environ.get("ADMIN_ID", "283760217"))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_last_start: dict[int, float] = {}
_awaiting_stake: dict[int, int] = {}


# ── Воронка ──────────────────────────────────────────────────

FUNNEL = {
    1: {
        "text": (
            "🦥 Привет\\.\n\n"
            "Сколько раз ты *начинал*?\n\n"
            "«С понедельника», «с нового года», «вот разберусь с делами\\.\\.\\.»\n\n"
            "Потом жизнь брала своё\\. И снова всё как раньше\\.\n\n"
            "Это не слабость\\. Просто подход был не тот\\.\n\n"
            "У меня — другой\\. 👇"
        ),
        "button": ("Расскажи →", "funnel:2"),
    },
    2: {
        "text": (
            "Большинство программ работают на силе воли\\.\n\n"
            "Сила воли конечна\\. Особенно к вечеру\\.\n\n"
            "Я работаю иначе: *5 простых задач каждый день*\\.\n\n"
            "Не часовые тренировки\\. Не жёсткая диета\\.\n"
            "Просто — следующий шаг\\. Каждый день\\. *77 дней*\\.\n\n"
            "_Маленькое, но своё — всегда сильнее большого чужого\\._"
        ),
        "button": ("Что за задачи? →", "funnel:3"),
    },
    3: {
        "text": (
            "📋 *5 задач каждый день:*\n\n"
            "💪 *Тренировка* — отжимания, приседания, пресс\\. Нагрузка растёт постепенно, "
            "под твои возможности\\.\n\n"
            "💧 *Вода* — 2 литра \\/ 8 стаканов в день\\.\n\n"
            "📚 *Чтение* — 10 страниц нон\\-фикшн или саморазвитие\\.\n\n"
            "🥗 *Питание* — без фастфуда, чипсов и снеков\\.\n\n"
            "🚫 *Без алкоголя* — день трезвости\\.\n\n"
            "*77 дней\\. Каждый день\\. Все 5\\.*"
        ),
        "button": ("Как это работает? →", "funnel:4"),
    },
    4: {
        "text": (
            "🔑 *Главное отличие Зарика — ставка\\.*\n\n"
            "Ты сам выбираешь сумму, которую ставишь на себя\\.\n\n"
            "✅ Завершил 77 дней → ставка возвращается полностью\n"
            "❌ Сошёл с дистанции → ставка остаётся\n\n"
            "Это не штраф\\. Это *твой личный договор с собой*\\.\n\n"
            "Мозг по\\-другому относится к тому, за что заплачено\\. "
            "Именно поэтому это работает там, где одна мотивация — нет\\.\n\n"
            "💳 Участие в программе: *10 ₽*\n"
            "_Ставка — любая сумма, ты решаешь 👇_"
        ),
        "button": ("Назначить ставку →", "funnel:stake"),
    },
}


def funnel_keyboard(button_text: str, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(button_text, callback_data=callback_data)
    ]])


def channel_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    if CHANNEL_USERNAME:
        ch_clean = CHANNEL_USERNAME.lstrip("@")
        buttons.append([InlineKeyboardButton(
            f"Подписаться на {CHANNEL_USERNAME}",
            url=f"https://t.me/{ch_clean}"
        )])
    buttons.append([InlineKeyboardButton("✅ Я подписался", callback_data="check_channel")])
    return InlineKeyboardMarkup(buttons)


async def is_subscribed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет подписку на канал. Если канал не задан — всегда True."""
    if not CHANNEL_USERNAME:
        return True
    try:
        ch = CHANNEL_USERNAME if CHANNEL_USERNAME.startswith("@") else f"@{CHANNEL_USERNAME}"
        member = await context.bot.get_chat_member(ch, user_id)
        return member.status not in (ChatMember.BANNED, ChatMember.LEFT)
    except Exception as e:
        logger.warning(f"Ошибка проверки подписки {user_id}: {e}")
        return True  # Если бот не является админом канала — пропускаем проверку


# ── Команды ──────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Защита от спама
    now = _time.time()
    if now - _last_start.get(user.id, 0) < 5:
        return
    _last_start[user.id] = now

    # Уже оплатил — напоминаем перейти к программному боту
    if db.is_payment_confirmed(user.id):
        await update.message.reply_text(
            f"✅ Твоя оплата уже подтверждена!\n\n"
            f"Переходи к боту программы и начинай:\n\n"
            f"👉 @{PROGRAM_BOT_USERNAME}"
        )
        return

    # Проверяем подписку на канал
    if CHANNEL_USERNAME and not await is_subscribed(user.id, context):
        await update.message.reply_text(
            f"🦥 Привет!\n\n"
            f"Для начала подпишись на наш канал {CHANNEL_USERNAME} — "
            f"там всё о программе и полезные материалы.\n\n"
            f"Как подпишешься — нажми кнопку ниже 👇",
            reply_markup=channel_keyboard()
        )
        return

    # Показываем первый шаг воронки
    step = FUNNEL[1]
    await update.message.reply_text(
        step["text"],
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=funnel_keyboard(*step["button"])
    )


# ── Callback-обработчик ──────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    # Проверка подписки на канал
    if data == "check_channel":
        await query.answer()
        if await is_subscribed(user_id, context):
            step = FUNNEL[1]
            await query.edit_message_text(
                step["text"],
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=funnel_keyboard(*step["button"])
            )
        else:
            ch = CHANNEL_USERNAME or "канал"
            await query.answer(
                f"Ты ещё не подписан на {ch}. Подпишись и нажми кнопку снова.",
                show_alert=True
            )
        return

    # Воронка
    if data.startswith("funnel:"):
        await query.answer()
        key = data.split(":")[1]

        if key == "stake":
            msg = await query.edit_message_text(
                f"💳 Введи сумму ставки в рублях\n\n"
                f"Просто напиши число — например: 5000\n\n"
                f"Минимум: {STAKE_MIN_RUB} ₽\n"
                f"Ставка вернётся полностью если пройдёшь 77 дней"
            )
            _awaiting_stake[user_id] = msg.message_id
            return

        step_num = int(key)
        if step_num not in FUNNEL:
            return
        step = FUNNEL[step_num]
        await query.edit_message_text(
            step["text"],
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=funnel_keyboard(*step["button"])
        )


# ── Обработчик текста ────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id not in _awaiting_stake:
        return

    clean = text.replace(" ", "").replace(",", "").replace("₽", "").replace("руб", "")
    if not clean.isdigit():
        await update.message.reply_text("🦥 Введи просто число — например: 5000")
        return

    amount_rub = int(clean)
    participation_rub = PARTICIPATION_FEE // 100
    stake_effective_min = max(STAKE_MIN_RUB, 60 - participation_rub)

    if amount_rub < stake_effective_min:
        await update.message.reply_text(
            f"🦥 Минимальная ставка — {stake_effective_min} ₽. Попробуй ещё раз."
        )
        return

    del _awaiting_stake[user_id]
    await send_invoice_for_stake(user_id, amount_rub * 100, context)


# ── Оплата ───────────────────────────────────────────────────

async def send_invoice_for_stake(chat_id: int, stake: int, context: ContextTypes.DEFAULT_TYPE):
    participation_rub = PARTICIPATION_FEE // 100
    stake_rub = stake // 100
    total_rub = participation_rub + stake_rub

    prices = [
        LabeledPrice("Участие в программе", PARTICIPATION_FEE),
        LabeledPrice("Ставка (возврат при завершении)", stake),
    ]

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"💳 Всё готово!\n\n"
            f"Участие: {participation_rub} ₽\n"
            f"Ставка: {stake_rub} ₽ (вернётся при завершении 77 дней)\n\n"
            f"Итого: {total_rub} ₽\n\n"
            f"Нажми кнопку ниже чтобы оплатить 👇\n\n"
            f"Если открываешь с компьютера — оплата может не работать в десктопном Telegram. "
            f"В этом случае открой бота с телефона."
        )
    )

    provider_data = {
        "receipt": {
            "items": [
                {
                    "description": "Участие в программе Зарик 77 дней",
                    "quantity": "1.00",
                    "amount": {"value": f"{participation_rub}.00", "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                },
                {
                    "description": "Ставка участника",
                    "quantity": "1.00",
                    "amount": {"value": f"{stake_rub}.00", "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                },
            ],
            "tax_system_code": 2,
        }
    }

    try:
        await context.bot.send_invoice(
            chat_id=chat_id,
            title="Зарик 77 дней",
            description=(
                f"Участие {participation_rub}р + ставка {stake_rub}р. "
                f"Ставка возвращается при завершении 77 дней."
            ),
            payload=f"zarik_{chat_id}_{stake}",
            provider_token=PROVIDER_TOKEN,
            currency="RUB",
            start_parameter="pay",
            prices=prices,
            need_email=True,
            send_email_to_provider=True,
            need_phone_number=True,
            send_phone_number_to_provider=True,
            need_name=True,
            provider_data=json.dumps(provider_data, ensure_ascii=False),
        )
        logger.info(f"Инвойс отправлен: user={chat_id}, ставка={stake_rub}₽")
    except Exception as e:
        logger.error(f"Ошибка send_invoice user={chat_id}: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Ошибка при создании счёта: {e}")


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    logger.info(f"PreCheckout: user={query.from_user.id}, сумма={query.total_amount}")
    try:
        await query.answer(ok=True)
    except Exception as e:
        logger.error(f"Ошибка PreCheckout: {e}")


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    payment = update.message.successful_payment

    try:
        stake_amount = int(payment.invoice_payload.split("_")[-1])
    except Exception:
        stake_amount = 0

    order_info = payment.order_info
    full_name = order_info.name         if order_info and order_info.name         else ""
    phone     = order_info.phone_number if order_info and order_info.phone_number else ""
    email     = order_info.email        if order_info and order_info.email        else ""

    db.register_user(user.id, user.username, user.first_name)
    db.save_payment(
        user_id=user.id,
        charge_id=payment.telegram_payment_charge_id,
        participation_fee=PARTICIPATION_FEE,
        stake_amount=stake_amount,
        full_name=full_name,
        phone=phone,
        email=email,
    )
    logger.info(f"Новый участник: {user.id} | {full_name} | ставка {stake_amount // 100}₽")

    stake_rub = stake_amount // 100
    participation_rub = PARTICIPATION_FEE // 100

    await update.message.reply_text(
        f"🎉 Оплата подтверждена!\n\n"
        f"Участие: {participation_rub} ₽\n"
        f"Ставка: {stake_rub} ₽ — вернётся когда пройдёшь 77 дней\n\n"
        f"Теперь — последний шаг.\n"
        f"Переходи к боту Зарика и начинай программу:\n\n"
        f"👉 @{PROGRAM_BOT_USERNAME}\n\n"
        f"Напиши там /start — он тебя встретит 🦥"
    )


# ── Сборка приложения ────────────────────────────────────────

def build_app() -> Application:
    db.init_db()
    app = Application.builder().token(LEAD_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app


if __name__ == "__main__":
    build_app().run_polling(drop_pending_updates=True)
