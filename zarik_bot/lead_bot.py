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
import difflib
import logging
import os
import re
import time as _time
from datetime import datetime

from telegram import (
    BotCommand, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, LabeledPrice, ReplyKeyboardMarkup, Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters,
)

import database as db
from admin_utils import make_admin_commands as _make_admin_commands

# ── Конфигурация ──────────────────────────────────────────────
LEAD_BOT_TOKEN       = os.environ["SHAGOV77_BOT_TOKEN"]
PROVIDER_TOKEN       = os.environ.get("LEAD_PROVIDER_TOKEN") or os.environ.get("PROVIDER_TOKEN", "")
PROGRAM_BOT_USERNAME = os.environ.get("PROGRAM_BOT_USERNAME", "Zarik_Lazy_Bot")
ADMIN_ID             = int(os.environ["ADMIN_ID"])
WEBAPP_URL           = os.environ.get("WEBAPP_URL", "")

CHANNEL              = "kabanovofficial"           # без @
CHANNEL_URL          = "https://t.me/kabanovofficial"

# ── Тест-пользователи (получают тестовые цены) ───────────────
_test_ids_raw = os.environ.get("TEST_USER_IDS", "")
TEST_USER_IDS = {int(x) for x in _test_ids_raw.split(",") if x.strip().isdigit()}

# ── Цены ─────────────────────────────────────────────────────
COURSE_PRICE_PROD = 199_000   # 1990 ₽ — боевой
COURSE_PRICE_TEST = 6_000     # 60 ₽  — тест
MIN_STAKE_PROD    = 10_000    # 100 ₽
MIN_STAKE_TEST    = 1_000     # 10 ₽

def _price_for(user_id: int) -> int:
    """Стоимость программы в копейках: тест для TEST_USER_IDS, боевая для всех остальных."""
    return COURSE_PRICE_TEST if user_id in TEST_USER_IDS else COURSE_PRICE_PROD

def _min_stake_for(user_id: int) -> int:
    return MIN_STAKE_TEST if user_id in TEST_USER_IDS else MIN_STAKE_PROD

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_last_start: dict[int, float] = {}

# ── Состояние диалога (last message для повтора) ──────────────
_user_last: dict[int, dict] = {}   # user_id → {text, markup, parse_mode}


def _remember(user_id: int, text: str, markup=None, parse_mode=ParseMode.MARKDOWN):
    """Запоминает последнее сообщение бота — для повтора при некорректном вводе."""
    _user_last[user_id] = {"text": text, "markup": markup, "parse_mode": parse_mode}


def _is_discount_request(text: str) -> bool:
    """Проверяет, есть ли в тексте слово «скидка» или близкое к нему (с учётом опечаток)."""
    tl = text.lower()
    if re.search(r"скид", tl):
        return True
    for word in re.findall(r"[а-яёa-z]+", tl):
        if len(word) >= 5 and difflib.SequenceMatcher(None, word, "скидка").ratio() >= 0.70:
            return True
    return False


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


def stake_confirm_keyboard() -> InlineKeyboardMarkup:
    """Выбор: ставить или нет."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, хочу поставить", callback_data="stake_yes")],
        [InlineKeyboardButton("➡️ Нет, перейти к оплате", callback_data="stake_no")],
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
        "📱 _iPhone: нажми ··· → Открыть в Safari → Поделиться → На экран Домой_\n"
        "🤖 _Android: открой в Chrome → меню ⋮ → Добавить на главный экран_"
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

    check_text = "🦥 Ну как? Всё получилось с трекером? Нравится? 👇"
    _remember(user_id, check_text, markup=tracker_check_keyboard(attempt), parse_mode=None)
    await context.bot.send_message(
        chat_id=user_id,
        text=check_text,
        reply_markup=tracker_check_keyboard(attempt),
    )
    db.mark_lead_tracker_question_sent(user_id)


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
    db.mark_lead_intro_sent(user_id)

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
        "🎯 *Плюс — добавь ставку на себя*\n\n"
        "Положи любую сумму сверху стоимости программы.\n"
        "Пройдёшь все 77 дней — получишь её обратно.\n\n"
        "_Это не штраф. Это твой личный договор с собой._\n\n"
        "Нажми кнопку — оплата прямо в Telegram 👇"
    )

    _remember(user_id, text, markup=buy_keyboard())
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
            "Ты можешь вернуться в любой момент — кнопка доступна выше."
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

    # Реферальный код блогера: /start ref_ivan
    if context.args:
        ref_code = context.args[0].strip()
        if ref_code:
            db.set_lead_referral(user.id, ref_code)
            logger.info(f"[REF] user={user.id} ref={ref_code}")

    if db.is_payment_confirmed(user.id):
        await update.message.reply_text(
            f"✅ Ты уже в программе!\n\n"
            f"Переходи к боту и продолжай:\n\n"
            f'👉 <a href="https://t.me/{PROGRAM_BOT_USERNAME}">Зарик Ленивец</a>',
            parse_mode=ParseMode.HTML,
        )
        return

    sub_text = (
        f"🦥 Привет, {user.first_name}!\n\n"
        f"Я подготовил тебе подарок — универсальный интерактивный трекер для достижения твоих задач.\n\n"
        f"Чтобы получить его, подпишись на канал — там всё самое важное о программе.\n\n"
        f"Как подпишешься — нажми кнопку ниже 👇"
    )
    _remember(user.id, sub_text, markup=subscribe_keyboard(), parse_mode=None)
    await update.message.reply_text(sub_text, reply_markup=subscribe_keyboard())


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
        db.mark_lead_tracker_reply(user_id, yes=True)
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
        db.mark_lead_tracker_reply(user_id, yes=False)
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

    # ── Покупка курса → предложение ставки ───────────────────
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
        db.mark_lead_start_clicked(user_id)
        min_r = _min_stake_for(user_id) // 100
        stake_text = (
            f"🎯 *Хочешь добавить ставку на себя?*\n\n"
            f"Ставка добавляется к стоимости программы.\n"
            f"Пройдёшь все 77 дней — вернём её обратно.\n\n"
            f"_Минимальная сумма — {min_r} ₽._"
        )
        _remember(user_id, stake_text, markup=stake_confirm_keyboard())
        await context.bot.send_message(
            chat_id=user_id,
            text=stake_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=stake_confirm_keyboard(),
        )
        db.mark_lead_stake_asked(user_id)
        return

    # ── Да, хочу ставку → просим ввести сумму ────────────────
    if data == "stake_yes":
        await query.answer()
        if db.is_payment_confirmed(user_id):
            return
        db.mark_lead_stake_choice(user_id, "yes")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        context.user_data["awaiting_stake"] = True
        min_r = _min_stake_for(user_id) // 100
        ask_text = f"💬 Введи сумму ставки в рублях (минимум {min_r} ₽):"
        _remember(user_id, ask_text, parse_mode=None)
        await context.bot.send_message(chat_id=user_id, text=ask_text)
        return

    # ── Нет, без ставки → сразу инвойс ───────────────────────
    if data == "stake_no":
        await query.answer()
        if db.is_payment_confirmed(user_id):
            return
        db.mark_lead_stake_choice(user_id, "no")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await send_course_invoice(user_id, context, stake_kopecks=0)
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Текстовые сообщения: ввод ставки, реакция на «скидку», или защита от случайного ввода."""
    user_id = update.effective_user.id
    text = update.message.text or ""

    # 1. Ждём ввод суммы ставки — обрабатываем как число
    if context.user_data.get("awaiting_stake"):
        min_stake = _min_stake_for(user_id)
        clean = text.strip().replace(",", ".").replace(" ", "")
        try:
            amount_rub = float(clean)
        except ValueError:
            await update.message.reply_text(
                f"⚠️ Некорректный формат ввода данных.\n\nВведи число, например: 100\n"
                f"Минимальная сумма — {min_stake // 100} ₽."
            )
            return

        amount_kopecks = int(amount_rub * 100)
        if amount_kopecks < min_stake:
            await update.message.reply_text(
                f"⚠️ Минимальная ставка — {min_stake // 100} ₽. Введи другую сумму:"
            )
            return

        context.user_data.pop("awaiting_stake", None)
        if db.is_payment_confirmed(user_id):
            return
        await send_course_invoice(user_id, context, stake_kopecks=amount_kopecks)
        return

    # 2. Запрос скидки — специальный ответ
    if _is_discount_request(text):
        await update.message.reply_text(
            "Предложение 1990 вместо 4900 действует в течение трёх дней с текущего момента, "
            "мы специально уронили цену, чтобы дать возможность большему количеству участников "
            "начать двигаться к своим целям."
        )
        return

    # 3. Любой другой текст — некорректный ввод, повторяем последнее сообщение
    last = _user_last.get(user_id)
    await update.message.reply_text("⚠️ Некорректный формат ввода данных.")
    if last:
        await context.bot.send_message(
            chat_id=user_id,
            text=last["text"],
            parse_mode=last.get("parse_mode"),
            reply_markup=last.get("markup"),
        )
    else:
        await cmd_start(update, context)


# ── Оплата ───────────────────────────────────────────────────

def _build_receipt(course_kopecks: int, stake_kopecks: int = 0) -> str:
    """
    Формирует provider_data с чеком для ЮКасса (54-ФЗ).
    vat_code=1 — без НДС.
    payment_mode=full_payment, payment_subject=service.
    Email/телефон покупателя передаёт Telegram автоматически
    через send_email_to_provider / send_phone_number_to_provider.
    """
    import json as _json

    def _rub(kopecks: int) -> str:
        return f"{kopecks / 100:.2f}"

    items = [
        {
            "description": "Программа «Зарик 77 дней»",
            "quantity": "1.00",
            "amount": {"value": _rub(course_kopecks), "currency": "RUB"},
            "vat_code": 1,
            "payment_mode": "full_payment",
            "payment_subject": "service",
        }
    ]
    if stake_kopecks > 0:
        items.append({
            "description": "Ставка на себя (возврат при завершении программы)",
            "quantity": "1.00",
            "amount": {"value": _rub(stake_kopecks), "currency": "RUB"},
            "vat_code": 1,
            "payment_mode": "full_payment",
            "payment_subject": "service",
        })

    return _json.dumps({"receipt": {"items": items}}, ensure_ascii=False)


async def send_course_invoice(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    stake_kopecks: int = 0,
):
    """Отправляет счёт на оплату: стоимость программы + ставка."""
    course_kopecks = _price_for(chat_id)
    prices = [LabeledPrice("Программа «Зарик 77 дней»", course_kopecks)]
    if stake_kopecks > 0:
        prices.append(LabeledPrice("Ставка на себя (вернём при завершении)", stake_kopecks))

    total_rub = (course_kopecks + stake_kopecks) // 100
    description = (
        f"Доступ к программе 77 дней + ставка {stake_kopecks // 100} ₽"
        if stake_kopecks > 0
        else "Полный доступ к программе на 77 дней."
    )

    try:
        await context.bot.send_invoice(
            chat_id=chat_id,
            title="Зарик 77 дней",
            description=description,
            payload=f"course_{chat_id}_stake_{stake_kopecks}",
            provider_token=PROVIDER_TOKEN,
            currency="RUB",
            prices=prices,
            need_name=True,
            need_email=True,
            need_phone_number=True,
            send_email_to_provider=True,
            send_phone_number_to_provider=True,
            provider_data=_build_receipt(course_kopecks, stake_kopecks),
        )
        logger.info(f"Инвойс отправлен: user={chat_id}, итого={total_rub}₽, ставка={stake_kopecks // 100}₽")
        db.mark_lead_invoice_sent(chat_id)
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


async def _notify_admin_tg_new_user(
    bot,
    user_id: int,
    first_name: str,
    username: str | None,
    fee_kopecks: int,
    stake_kopecks: int,
) -> None:
    """Отправляет администратору уведомление о новом участнике (Telegram)."""
    try:
        uname = f"@{username}" if username else f"id{user_id}"
        text  = (
            f"🆕 Новый участник программы!\n\n"
            f"👤 {first_name} ({uname})\n"
            f"📱 Telegram\n"
            f"💰 Взнос: {fee_kopecks // 100} ₽"
        )
        if stake_kopecks > 0:
            text += f"\n🎯 Ставка: {stake_kopecks // 100} ₽"
        await bot.send_message(chat_id=ADMIN_ID, text=text)
    except Exception:
        logger.exception("Не удалось отправить уведомление администратору (TG)")


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    payment = update.message.successful_payment

    # Извлекаем ставку из payload: "course_{uid}_stake_{kopecks}"
    payload = payment.invoice_payload
    try:
        stake_kopecks = int(payload.split("_stake_")[-1])
    except Exception:
        stake_kopecks = 0

    fee_kopecks = _price_for(user.id)
    db.register_user(user.id, user.username or "", user.first_name or "")
    db.save_payment(
        user_id=user.id,
        charge_id=payment.telegram_payment_charge_id,
        participation_fee=fee_kopecks,
        stake_amount=stake_kopecks,
    )
    db.mark_lead_purchased(user.id)
    logger.info(f"Новый участник: {user.id} | {user.first_name} | ставка={stake_kopecks // 100}₽")

    await _notify_admin_tg_new_user(
        context.bot,
        user.id,
        user.first_name or "",
        user.username,
        fee_kopecks,
        stake_kopecks,
    )

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


DEV_USER_IDS = {283760217, 262479340}


async def cmd_reset_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбрасывает себя — для тестирования воронки заново. Доступно dev-пользователям."""
    user_id = update.effective_user.id
    if user_id not in DEV_USER_IDS:
        return
    with db.get_conn() as conn:
        conn.execute("DELETE FROM leads WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE users SET payment_charge_id = NULL WHERE user_id = ?", (user_id,))
    # Сбрасываем in-memory состояние
    _user_last.pop(user_id, None)
    context.user_data.clear()
    await update.message.reply_text("✅ Твой аккаунт сброшен. Отправь /start — начнём с начала.")
    logger.info(f"DEV сброс: user={user_id}")


async def cmd_leads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает CRM-статистику по лидам (только для администратора)."""
    if update.effective_user.id != ADMIN_ID:
        return

    f = db.get_funnel_stats()
    if not f or not f.get("total"):
        await update.message.reply_text("📊 Лидов пока нет.")
        return

    cold = sum(1 for l in db.get_all_leads() if l.get("lead_status") == "cold")

    text = (
        f"📊 *CRM — лиды @Shagov77\\_bot*\n\n"
        f"Всего лидов: {f['total']}\n"
        f"Подписались на канал: {f['subscribed']}\n"
        f"Получили трекер: {f['tracker_sent']}\n"
        f"Получили оффер: {f['offer_sent']}\n"
        f"Купили курс: {f['purchased']} 🎉\n"
        f"Остыли (cold): {cold}\n\n"
        f"Конверсия: {f['purchased'] / max(f['tracker_sent'], 1) * 100:.1f}% (купили / получили трекер)"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_funnel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Детальная воронка с конверсией на каждом шаге (только для администратора)."""
    if update.effective_user.id != ADMIN_ID:
        return

    f = db.get_funnel_stats()
    if not f or not f.get("total"):
        await update.message.reply_text("📊 Данных пока нет.")
        return

    def pct(n, base):
        return f"{n / base * 100:.0f}%" if base else "—"

    def drop(current, prev):
        if not prev:
            return ""
        lost = prev - current
        return f" (−{lost}, {lost / prev * 100:.0f}% отвал)" if lost > 0 else ""

    t = f["total"]
    lines = [
        "🔽 *Воронка @Shagov77\\_bot*\n",
        f"Всего лидов:              {t:>4}  (100%)",
        f"Подписались:              {f['subscribed']:>4}  ({pct(f['subscribed'], t)}){drop(f['subscribed'], t)}",
        f"Получили трекер:          {f['tracker_sent']:>4}  ({pct(f['tracker_sent'], t)}){drop(f['tracker_sent'], f['subscribed'])}",
        f"Увидели вопрос:           {f['question_sent']:>4}  ({pct(f['question_sent'], t)}){drop(f['question_sent'], f['tracker_sent'])}",
        f"Ответили на вопрос:       {f['question_replied']:>4}  ({pct(f['question_replied'], t)}) → Да: {f['replied_yes']} / Нет: {f['replied_no']}",
        f"Знакомство отправлено:    {f['intro_sent']:>4}  ({pct(f['intro_sent'], t)}){drop(f['intro_sent'], f['question_replied'])}",
        f"Получили оффер:           {f['offer_sent']:>4}  ({pct(f['offer_sent'], t)}){drop(f['offer_sent'], f['intro_sent'])}",
        f"Нажали «Начать»:          {f['start_clicked']:>4}  ({pct(f['start_clicked'], t)}){drop(f['start_clicked'], f['offer_sent'])}",
        f"Увидели вопрос о ставке:  {f['stake_asked']:>4}  ({pct(f['stake_asked'], t)}) → Да: {f['stake_yes'] or 0} / Нет: {f['stake_no'] or 0}",
        f"Получили счёт:            {f['invoice_sent']:>4}  ({pct(f['invoice_sent'], t)}){drop(f['invoice_sent'], f['stake_asked'])}",
        f"Оплатили:                 {f['purchased']:>4}  ({pct(f['purchased'], t)}){drop(f['purchased'], f['invoice_sent'])}",
        "",
        f"🎯 Итоговая конверсия: *{pct(f['purchased'], f['tracker_sent'])}* (оплатили / получили трекер)",
    ]

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_bloggers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сводка по блогерам: переходы и покупки по реф-кодам (только для администратора)."""
    if update.effective_user.id != ADMIN_ID:
        return

    rows = db.get_blogger_stats_tg()
    if not rows:
        await update.message.reply_text("📊 Пока нет лидов с реферальными ссылками.")
        return

    total_all = sum(r["total"] for r in rows)
    bought_all = sum(r["purchased"] for r in rows)
    lines = [f"📊 *Блогеры — @Shagov77\\_bot* (всего: {total_all} → купили: {bought_all})\n"]
    for r in rows:
        code  = r["referral_code"]
        conv  = f"{r['purchased'] / r['total'] * 100:.0f}%" if r["total"] else "—"
        sub_r = f"{r['subscribed'] / r['total'] * 100:.0f}%" if r["total"] else "—"
        lines.append(
            f"🔗 `{code}`\n"
            f"   Переходов: {r['total']}  Подписались: {r['subscribed']} ({sub_r})"
            f"  Купили: *{r['purchased']}* ({conv})"
        )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── Сборка приложения ────────────────────────────────────────

async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "🦥 Начать"),
    ])
    # ── Дайджест участников за сегодня (при перезапуске бота) ──
    try:
        today = db.get_tg_leads_purchased_today()
        if today:
            lines = [f"📋 Участники сегодня (Telegram) — {len(today)} чел.:\n"]
            for p in today:
                uname = f"@{p['username']}" if p.get('username') else f"id{p['user_id']}"
                fee   = (p['participation_fee'] or 0) // 100
                stake = (p['stake_amount'] or 0) // 100
                t     = (p['purchased_at'] or '')[:16]
                line  = f"👤 {p['first_name'] or '?'} ({uname}) — {fee} ₽"
                if stake:
                    line += f" + ставка {stake} ₽"
                line += f"  [{t}]"
                lines.append(line)
            await application.bot.send_message(chat_id=ADMIN_ID, text="\n".join(lines))
    except Exception:
        logger.exception("Не удалось отправить дайджест участников (TG)")


def build_app() -> Application:
    db.init_db()
    app = (
        Application.builder()
        .token(LEAD_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    _cmd_getxls, _cmd_getdb = _make_admin_commands(ADMIN_ID)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("reset_user", cmd_reset_user))
    app.add_handler(CommandHandler("leads", cmd_leads))
    app.add_handler(CommandHandler("funnel", cmd_funnel))
    app.add_handler(CommandHandler("bloggers", cmd_bloggers))
    app.add_handler(CommandHandler("getxls", _cmd_getxls))
    app.add_handler(CommandHandler("getdb",  _cmd_getdb))
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
