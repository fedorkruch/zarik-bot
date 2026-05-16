"""
lead_bot.py — @Shagov77_bot: воронка продаж
Флоу:
  1. /start → проверка подписки на @kabanovofficial
  2. Подписан → отправляем трекер-подарок + добавляем в CRM
  3. +20 сек → «Ну как? Всё получилось с трекером?» [Да / Нет]
  4a. Нет → инструкция iOS/Android → +20 сек → снова вопрос (макс. 2 раза)
  4b. Да (или 2-й раз нет → авто) → «Супер!» → +2 сек → знакомство с программой
  5. +10 сек → оффер 1990 ₽ + кнопка «Начать»
  6. +60 сек без клика → «без давления»
  7. Day 2 (24h) / Day 3 (48h) / Day 7 (168h) → follow-up с кнопкой
  8. После Day 7 без покупки → прощальное сообщение
  9. Оплата → ссылка на @Zarik_Lazy_Bot + CRM обновление

Переменные окружения:
  SHAGOV77_BOT_TOKEN     — токен @Shagov77_bot
  LEAD_PROVIDER_TOKEN    — токен ЮКасса для лид-бота
  PROVIDER_TOKEN         — fallback токен ЮКасса
  PROGRAM_BOT_USERNAME   — username программного бота (по умолчанию Zarik_Lazy_Bot)
  ADMIN_ID               — Telegram ID администратора
  WEBAPP_URL             — URL мини-аппа (для трекера)
"""
import logging
import os
import time as _time
from datetime import datetime

from telegram import (
    BotCommand, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice, Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters,
)

import database as db

# ── Конфигурация ──────────────────────────────────────────────
LEAD_BOT_TOKEN       = os.environ["SHAGOV77_BOT_TOKEN"]
PROVIDER_TOKEN       = os.environ.get("LEAD_PROVIDER_TOKEN") or os.environ.get("PROVIDER_TOKEN", "")
PROGRAM_BOT_USERNAME = os.environ.get("PROGRAM_BOT_USERNAME", "Zarik_Lazy_Bot")
ADMIN_ID             = int(os.environ.get("ADMIN_ID", "283760217"))
WEBAPP_URL           = os.environ.get("WEBAPP_URL", "")

CHANNEL              = "kabanovofficial"           # без @
CHANNEL_URL          = "https://t.me/kabanovofficial"
COURSE_PRICE_KOPECKS = 199_000                    # 1990 ₽

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_last_start: dict[int, float] = {}


# ── Клавиатуры ───────────────────────────────────────────────

def subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Подписаться на канал", url=CHANNEL_URL)],
        [InlineKeyboardButton("✅ Я подписался", callback_data="check_sub")],
    ])


def buy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Начать за 1990 ₽", callback_data="buy_course")],
    ])


def tracker_keyboard() -> InlineKeyboardMarkup | None:
    """Кнопка открытия интерактивного трекера (обычная ссылка, поддерживает PWA)."""
    if WEBAPP_URL:
        tracker_url = WEBAPP_URL.rstrip("/") + "/tracker"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Открыть трекер", url=tracker_url)],
        ])
    return None


def tracker_check_keyboard(attempt: int = 1) -> InlineKeyboardMarkup:
    """Кнопки ответа на вопрос «Всё получилось с трекером?»."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, всё отлично!", callback_data="tracker_ok")],
        [InlineKeyboardButton("😕 Нет, не получилось", callback_data=f"tracker_fail_{attempt}")],
    ])


# ── Проверка подписки ─────────────────────────────────────────

async def is_subscribed(user_id: int, bot) -> bool:
    """
    Проверяет подписку на @kabanovofficial.
    При любой ошибке — возвращает False (не пропускаем).
    Бот должен быть администратором канала.
    """
    try:
        member = await bot.get_chat_member(f"@{CHANNEL}", user_id)
        logger.info(f"[SUB] user={user_id} status={member.status}")
        return member.status not in (ChatMember.BANNED, ChatMember.LEFT)
    except Exception as e:
        logger.error(f"[SUB ERROR] user={user_id} error={e}")
        return False


# ── Основной флоу ────────────────────────────────────────────

async def do_send_tracker(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 2: финальная проверка подписки, отправляем трекер-подарок,
    затем через 20 сек спрашиваем «Всё получилось?».
    """
    if not await is_subscribed(user_id, context.bot):
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🦥 Я не обнаружил подписки на канал.\n\n"
                "Как только подпишешься — я сразу пришлю подарок 🎁"
            ),
            reply_markup=subscribe_keyboard(),
        )
        return

    tracker_text = (
        "🎁 *Держи трекер достижений — твой подарок!*\n\n"
        "Устанавливай количество дней на пути к цели, прописывай ежедневные задачи. "
        "Отмечай каждый день выполнение и наблюдай свой путь.\n\n"
        "Это твой личный дашборд прогресса 👇\n\n"
        "📱 _iPhone: нажми ··· → Открыть в Safari → Поделиться → На экран Домой_"
    )
    tracker_kb = tracker_keyboard()

    if tracker_kb:
        await context.bot.send_message(
            chat_id=user_id,
            text=tracker_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=tracker_kb,
        )
    else:
        await context.bot.send_message(
            chat_id=user_id,
            text=tracker_text,
            parse_mode=ParseMode.MARKDOWN,
        )

    db.mark_lead_tracker_sent(user_id)

    # +20 сек → спрашиваем про трекер
    context.job_queue.run_once(
        job_ask_tracker_check,
        when=20,
        data={"user_id": user_id, "attempt": 1},
        name=f"tracker_check_{user_id}",
    )


async def job_ask_tracker_check(context: ContextTypes.DEFAULT_TYPE):
    """Шаг 3: спрашиваем, всё ли получилось с трекером."""
    data = context.job.data
    user_id = data["user_id"]
    attempt = data.get("attempt", 1)

    if db.is_payment_confirmed(user_id):
        return

    await context.bot.send_message(
        chat_id=user_id,
        text="🦥 Ну как? Всё получилось с трекером? Нравится? 👇",
        reply_markup=tracker_check_keyboard(attempt),
    )


async def job_send_intro(context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 4: знакомство с программой 77 дней.
    Запускается после ответа «Да» или после двух «Нет».
    """
    user_id = context.job.data

    if db.is_payment_confirmed(user_id):
        return

    text = (
        "🦥 Кстати, мы уже собрали *программу 77 дней* — "
        "для тех, кто хочет реальных изменений без надрыва.\n\n"
        "Каждое утро твой личный бот-наставник присылает тебе задачи дня. "
        "Ты отмечаешь что выполнил — и видишь как растёт прогресс.\n\n"
        "Никакого жёсткого расписания.\n"
        "Никаких часовых тренировок.\n"
        "Никакого «начну с понедельника».\n\n"
        "Просто маленькие шаги каждый день — "
        "и через 77 дней ты не узнаешь себя.\n\n"
        "_Это работает, потому что не требует героизма — только привычки._"
    )

    await context.bot.send_message(
        chat_id=user_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
    )

    # +10 сек → оффер с ценой
    context.job_queue.run_once(
        job_send_offer,
        when=10,
        data=user_id,
        name=f"offer_{user_id}",
    )


async def job_send_offer(context: ContextTypes.DEFAULT_TYPE):
    """Шаг 5: цена 1990 ₽ + кнопка «Начать»."""
    user_id = context.job.data

    if db.is_payment_confirmed(user_id):
        return

    text = (
        "💳 *Сегодня — 1990 ₽ вместо 4990 ₽*\n\n"
        "Полный доступ к программе на 77 дней:\n"
        "• Персональный наставник-бот\n"
        "• Трекер задач и прогресса\n"
        "• Еженедельная статистика группы\n"
        "• Ачивки за серии и достижения\n\n"
        "Нажми кнопку — оплата прямо в Telegram 👇"
    )

    await context.bot.send_message(
        chat_id=user_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=buy_keyboard(),
    )

    db.mark_lead_pitch_sent(user_id)

    # +60 сек без клика → «без давления»
    context.job_queue.run_once(
        job_no_pressure,
        when=60,
        data=user_id,
        name=f"nopressure_{user_id}",
    )


async def job_no_pressure(context: ContextTypes.DEFAULT_TYPE):
    """Шаг 6: без давления — если не нажал за 60 сек."""
    user_id = context.job.data

    if db.is_payment_confirmed(user_id):
        return

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "🦥 Никакого давления.\n\n"
            "Ты можешь вернуться в любой момент — кнопка доступна выше.\n\n"
            "Если есть вопросы — просто напиши, отвечу."
        ),
    )


# ── Follow-up: периодическая проверка ────────────────────────

FOLLOWUP_TEXTS = {
    2: (
        "🦥 Как ты?\n\n"
        "Вчера смотрел(а) программу — решил(а) попробовать?\n\n"
        "77 дней начинаются с одного шага.\n"
        "Цена пока 1990 ₽ 👇"
    ),
    3: (
        "📊 Три дня, как ты видел(а) трекер.\n\n"
        "Знаешь что объединяет тех, кто прошёл 77 дней?\n"
        "Они просто *начали*.\n\n"
        "Не «когда будет время». Не «с понедельника».\n"
        "Сегодня. Прямо сейчас 👇"
    ),
    7: (
        "🏁 Прошла неделя.\n\n"
        "Ты видел(а) трекер, читал(а) о программе.\n\n"
        "Это последнее напоминание — я не хочу быть навязчивым.\n\n"
        "Если решишь начать — кнопка ниже.\n"
        "Если нет — всё равно желаю тебе результата 🦥"
    ),
}


async def _send_followup(context: ContextTypes.DEFAULT_TYPE, user_id: int, day: int):
    """Отправляет follow-up и обновляет CRM."""
    text = FOLLOWUP_TEXTS.get(day, "")
    if not text:
        return
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=buy_keyboard(),
        )
        db.mark_lead_follow_up(user_id, day)
        logger.info(f"Follow-up day {day} отправлен: {user_id}")
    except Exception as e:
        logger.warning(f"Follow-up day {day} ошибка {user_id}: {e}")


async def job_followup_check(context: ContextTypes.DEFAULT_TYPE):
    """
    Запускается каждый час. Robust против перезапусков: использует метки времени из БД.
    Расписание относительно pitch_sent_at:
      24h+  → Day 2 follow-up
      48h+  → Day 3 follow-up
      168h+ → Day 7 follow-up → через час прощание
    """
    leads = db.get_leads_for_followup()
    now = datetime.utcnow()

    for lead in leads:
        user_id  = lead["user_id"]
        pitch_at = lead.get("pitch_sent_at")

        if not pitch_at:
            continue

        try:
            pitch_dt = datetime.fromisoformat(pitch_at)
        except Exception:
            continue

        hours = (now - pitch_dt).total_seconds() / 3600

        if db.is_payment_confirmed(user_id):
            db.mark_lead_purchased(user_id)
            continue

        if hours >= 168 and not lead.get("follow_7_sent_at") and not lead.get("final_sent_at"):
            await _send_followup(context, user_id, day=7)
            context.job_queue.run_once(
                job_farewell,
                when=3600,
                data=user_id,
                name=f"farewell_{user_id}",
            )
            continue

        if (hours >= 48
                and not lead.get("follow_3_sent_at")
                and not lead.get("follow_7_sent_at")
                and not lead.get("final_sent_at")):
            await _send_followup(context, user_id, day=3)
            continue

        if hours >= 24 and not lead.get("follow_2_sent_at"):
            await _send_followup(context, user_id, day=2)
            continue


async def job_farewell(context: ContextTypes.DEFAULT_TYPE):
    """Прощальное сообщение — после финального follow-up через 1 час."""
    user_id = context.job.data

    if db.is_payment_confirmed(user_id):
        return

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🦥 Окей, не буду больше напоминать.\n\n"
                "Если захочешь вернуться — просто напиши /start.\n\n"
                "Удачи тебе, что бы ты ни выбрал(а) 🙌"
            ),
        )
        db.mark_lead_final(user_id)
    except Exception as e:
        logger.warning(f"Farewell ошибка {user_id}: {e}")


# ── Команды ──────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    now = _time.time()
    if now - _last_start.get(user.id, 0) < 5:
        return
    _last_start[user.id] = now

    db.upsert_lead(user.id, user.username or "", user.first_name or "")

    if db.is_payment_confirmed(user.id):
        await update.message.reply_text(
            f"✅ Ты уже в программе!\n\n"
            f"Переходи к боту и продолжай:\n\n"
            f'👉 <a href="https://t.me/{PROGRAM_BOT_USERNAME}">Зарик Ленивец</a>',
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text(
        f"🦥 Привет, {user.first_name}!\n\n"
        f"Я подготовил тебе подарок — универсальный интерактивный трекер для достижения твоих задач.\n\n"
        f"Чтобы получить его, подпишись на канал — там всё самое важное о программе.\n\n"
        f"Как подпишешься — нажми кнопку ниже 👇",
        reply_markup=subscribe_keyboard(),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    logger.info(f"[CB] user={user_id} data={data}")

    # ── Проверка подписки на канал ────────────────────────────
    if data == "check_sub":
        subscribed = await is_subscribed(user_id, context.bot)
        if subscribed:
            await query.answer("✅ Подписка подтверждена!")
            db.upsert_lead(user_id, query.from_user.username or "", query.from_user.first_name or "")
            db.mark_lead_subscribed(user_id)
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception as e:
                logger.warning(f"[CB] edit_markup error (ignored): {e}")
            await do_send_tracker(user_id, context)
        else:
            await query.answer("Подписка не обнаружена. Подпишись и нажми снова.", show_alert=True)
            try:
                await query.edit_message_text(
                    "🦥 Я не обнаружил подписки на канал.\n\n"
                    "Как только подпишешься — я сразу пришлю подарок 🎁\n\n"
                    "Подпишись и нажми кнопку снова 👇",
                    reply_markup=subscribe_keyboard(),
                )
            except Exception as e:
                logger.warning(f"[CB] edit_text error (ignored): {e}")
        return

    # ── «Да, всё отлично!» ────────────────────────────────────
    if data == "tracker_ok":
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 Супер, я очень рад что всё получилось!",
        )
        context.job_queue.run_once(
            job_send_intro,
            when=2,
            data=user_id,
            name=f"intro_{user_id}",
        )
        return

    # ── «Нет, не получилось» ──────────────────────────────────
    if data.startswith("tracker_fail"):
        await query.answer()
        attempt = int(data.split("_")[-1])
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        # Инструкция для iOS и Android
        instructions = (
            "📱 *Как открыть трекер и сохранить на экран:*\n\n"
            "*Если у тебя iPhone (iOS):*\n"
            "1️⃣ Нажми кнопку «📊 Открыть трекер» выше\n"
            "2️⃣ Нажми *···* (три точки) вверху браузера\n"
            "3️⃣ Выбери *Открыть в Safari*\n"
            "4️⃣ В Safari: *Поделиться* → *На экран Домой*\n\n"
            "*Если у тебя Android:*\n"
            "1️⃣ Нажми кнопку «📊 Открыть трекер» выше\n"
            "2️⃣ Chrome покажет баннер — нажми *Добавить*\n"
            "Или: меню *⋮* → *Добавить на главный экран*\n\n"
            "Сделай это — и трекер будет работать как отдельное приложение 👆"
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=instructions,
            parse_mode=ParseMode.MARKDOWN,
        )

        if attempt < 2:
            # Спрашиваем снова через 20 секунд
            context.job_queue.run_once(
                job_ask_tracker_check,
                when=20,
                data={"user_id": user_id, "attempt": attempt + 1},
                name=f"tracker_check_{user_id}",
            )
        else:
            # Второй «нет» — всё равно двигаемся дальше через 30 сек
            context.job_queue.run_once(
                job_send_intro,
                when=30,
                data=user_id,
                name=f"intro_{user_id}",
            )
        return

    # ── Покупка курса ─────────────────────────────────────────
    if data == "buy_course":
        await query.answer()
        if db.is_payment_confirmed(user_id):
            try:
                await query.edit_message_text(
                    f"✅ Ты уже в программе!\n\n"
                    f'👉 <a href="https://t.me/{PROGRAM_BOT_USERNAME}">Зарик Ленивец</a>',
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            return
        await send_course_invoice(user_id, context)
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Любое текстовое сообщение перезапускает воронку."""
    await cmd_start(update, context)


# ── Оплата ───────────────────────────────────────────────────

async def send_course_invoice(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет счёт на оплату курса 1990 ₽."""
    prices = [LabeledPrice("Программа «Зарик 77 дней»", COURSE_PRICE_KOPECKS)]

    try:
        await context.bot.send_invoice(
            chat_id=chat_id,
            title="Зарик 77 дней",
            description=(
                "Полный доступ к программе на 77 дней. "
                "Персональный наставник-бот, трекер задач и прогресса."
            ),
            payload=f"course_{chat_id}",
            provider_token=PROVIDER_TOKEN,
            currency="RUB",
            start_parameter="pay",
            prices=prices,
            need_name=False,
            need_email=False,
            need_phone_number=False,
        )
        logger.info(f"Инвойс отправлен: user={chat_id}")
    except Exception as e:
        logger.error(f"Ошибка send_invoice user={chat_id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Не удалось создать счёт. Попробуй позже или напиши нам.",
        )


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    logger.info(f"PreCheckout: user={query.from_user.id}, сумма={query.total_amount}")
    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    payment = update.message.successful_payment

    db.register_user(user.id, user.username or "", user.first_name or "")
    db.save_payment(
        user_id=user.id,
        charge_id=payment.telegram_payment_charge_id,
        participation_fee=COURSE_PRICE_KOPECKS,
        stake_amount=0,
    )
    db.mark_lead_purchased(user.id)
    logger.info(f"Новый участник: {user.id} | {user.first_name}")

    await update.message.reply_text(
        f"🎉 *Оплата подтверждена! Добро пожаловать в программу.*\n\n"
        f"Теперь переходи к боту Зарика — он тебя встретит и проведёт через онбординг:\n\n"
        f'👉 <a href="https://t.me/{PROGRAM_BOT_USERNAME}">Зарик Ленивец</a>\n\n'
        f"Нажми кнопку — и начнём 🦥",
        parse_mode=ParseMode.HTML,
    )


# ── Административные команды ──────────────────────────────────

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбрасывает лида — удаляет из таблицы leads (только для администратора)."""
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Использование: /reset <user_id>")
        return
    user_id = int(args[0])
    with db.get_conn() as conn:
        conn.execute("DELETE FROM leads WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE users SET payment_charge_id = NULL WHERE user_id = ?", (user_id,))
    await update.message.reply_text(
        f"✅ Лид {user_id} сброшен: удалён из CRM + оплата очищена. Теперь он пройдёт воронку заново."
    )
    logger.info(f"Лид {user_id} сброшен администратором {update.effective_user.id}")


async def cmd_leads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает CRM-статистику по лидам (только для администратора)."""
    if update.effective_user.id != ADMIN_ID:
        return

    leads = db.get_all_leads()
    if not leads:
        await update.message.reply_text("📊 Лидов пока нет.")
        return

    total      = len(leads)
    subscribed = sum(1 for l in leads if l.get("subscribed_at"))
    tracker_s  = sum(1 for l in leads if l.get("tracker_sent_at"))
    pitched    = sum(1 for l in leads if l.get("pitch_sent_at"))
    purchased  = sum(1 for l in leads if l.get("lead_status") == "purchased")
    cold       = sum(1 for l in leads if l.get("lead_status") == "cold")

    text = (
        f"📊 *CRM — лиды @Shagov77\\_bot*\n\n"
        f"Всего лидов: {total}\n"
        f"Подписались на канал: {subscribed}\n"
        f"Получили трекер: {tracker_s}\n"
        f"Получили оффер: {pitched}\n"
        f"Купили курс: {purchased} 🎉\n"
        f"Остыли (cold): {cold}\n\n"
        f"Конверсия: {purchased / max(tracker_s, 1) * 100:.1f}% (купили / получили трекер)"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── Сборка приложения ────────────────────────────────────────

async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "🦥 Начать"),
    ])


def build_app() -> Application:
    db.init_db()
    app = (
        Application.builder()
        .token(LEAD_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("leads", cmd_leads))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Периодическая проверка follow-up — каждый час, robust к перезапускам
    app.job_queue.run_repeating(
        job_followup_check,
        interval=3600,
        first=60,
        name="followup_check",
    )

    return app


if __name__ == "__main__":
    build_app().run_polling(drop_pending_updates=True)
