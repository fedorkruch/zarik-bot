"""
bot.py — главный файл бота Зарик (77-дневный челлендж)
Запуск: python3 bot.py
Переменные окружения (обязательные):
  BOT_TOKEN       — токен от @BotFather
  PROVIDER_TOKEN  — токен YooKassa (shopId:LIVE:secret)
Опциональные:
  PARTICIPATION_FEE_KOPECKS — стоимость участия в копейках (по умолчанию 100 = 1₽ тест)
  STAKE_MIN_RUB             — минимальная ставка в рублях (по умолчанию 500)
  DATA_DIR                  — папка для zarik.db
  ADMIN_ID                  — Telegram ID администратора
"""
import json
import logging
import os
import time as _time
from datetime import datetime as dt
import pytz

from telegram import Update, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
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
    TIMEZONES,
)
from workout import get_workout

# ── Конфигурация ──────────────────────────────────────────────
BOT_TOKEN         = os.environ["BOT_TOKEN"]
PROVIDER_TOKEN    = os.environ["PROVIDER_TOKEN"]
PARTICIPATION_FEE = int(os.environ.get("PARTICIPATION_FEE_KOPECKS", "100"))   # 1₽ по умолчанию (тест)
STAKE_MIN_RUB     = int(os.environ.get("STAKE_MIN_RUB", "500"))
ADMIN_ID          = int(os.environ.get("ADMIN_ID", "283760217"))
TOTAL_DAYS        = 77

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Защита от двойных нажатий /start
_last_start: dict[int, float] = {}

# Пользователи, ожидающие ввода суммы ставки
_awaiting_stake: dict[int, int] = {}


# ── Воронка продаж ────────────────────────────────────────────

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


# ── Построение сообщений ──────────────────────────────────────

BAR_WIDTH = 20


def make_progress_bar(day: int, total: int = TOTAL_DAYS) -> str:
    """Прогресс-бар: ▓▓▓░░░░░░░░░░░░░░░░░ 15%"""
    pct = round(day / total * 100)
    filled = round(day / total * BAR_WIDTH)
    bar = "▓" * filled + "░" * (BAR_WIDTH - filled)
    return f"{bar} {pct}%"


def build_checklist_message(user_row, day: int, completed: set) -> str:
    """
    Формирует полное сообщение чеклиста:
    утренний текст + прогресс + 5 задач с деталями тренировки.
    """
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
            # Детали тренировки под первой задачей
            for detail_line in workout["description"].split("\n"):
                lines.append(f"   {detail_line}")

    lines.append("")
    lines.append(f"Прогресс дня: {done} из 5")

    return "\n".join(lines)


def build_progress_text(user_id: int) -> str:
    """Страница прогресса участника"""
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


# ── Команда /start ────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Защита от спама
    now = _time.time()
    if now - _last_start.get(user.id, 0) < 5:
        return
    _last_start[user.id] = now

    user_row = db.get_user(user.id)

    if user_row:
        step = db.get_onboarding_step(user.id)

        if step == "payment":
            # Не завершил оплату — снова показываем воронку
            step_data = FUNNEL[1]
            await update.message.reply_text(
                step_data["text"],
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=funnel_keyboard(*step_data["button"])
            )

        elif step == "timezone":
            await update.message.reply_text(
                "🦥 Выбери часовой пояс, чтобы я присылал задания в 6:00 по твоему времени 👇",
                reply_markup=timezone_keyboard()
            )

        elif step == "pushup":
            await update.message.reply_text(
                "🦥 Сколько отжиманий можешь сделать прямо сейчас, без подготовки?",
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
        return

    # Новый пользователь — запускаем воронку
    step_data = FUNNEL[1]
    await update.message.reply_text(
        step_data["text"],
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=funnel_keyboard(*step_data["button"])
    )


# ── Команды меню ──────────────────────────────────────────────

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_row = db.get_user(user.id)

    if not user_row or db.get_onboarding_step(user.id) != "done":
        await update.message.reply_text("Сначала напиши /start чтобы начать программу.")
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
    if not db.get_user(user.id):
        await update.message.reply_text("Сначала напиши /start.")
        return
    await update.message.reply_text(build_progress_text(user.id))


# ── Обработка всех callback кнопок ───────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    # ── Воронка ──
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
        return

    # ── Выбор часового пояса ──
    if data.startswith("tz:"):
        await query.answer()
        tz_name = data.split(":", 1)[1]
        db.set_user_timezone(user_id, tz_name)  # → шаг 'pushup'
        tz_label = next((label for label, tz in TIMEZONES if tz == tz_name), tz_name)
        await query.edit_message_text(f"✅ Часовой пояс: {tz_label}")
        await query.message.reply_text(
            "🦥 Теперь давай подберём тренировку под тебя.\n\n"
            "Сколько отжиманий можешь сделать прямо сейчас, без подготовки?",
            reply_markup=reps_keyboard("pushup")
        )
        return

    # ── Онбординг: ввод количества повторений ──
    if data.startswith("reps:"):
        await query.answer()
        parts = data.split(":")
        exercise = parts[1]   # pushup | squat | abs
        reps = int(parts[2])

        if exercise == "pushup":
            db.save_pushup_start(user_id, reps)  # → шаг 'squat'
            await query.edit_message_text(
                f"💪 Отжимания: {reps} — записал!\n\nСколько приседаний?",
                reply_markup=reps_keyboard("squat")
            )

        elif exercise == "squat":
            db.save_squat_start(user_id, reps)   # → шаг 'abs'
            await query.edit_message_text(
                f"🦵 Приседания: {reps} — отлично!\n\nСколько раз пресс?",
                reply_markup=reps_keyboard("abs")
            )

        elif exercise == "abs":
            db.save_abs_start(user_id, reps)     # → шаг 'done'
            db.complete_onboarding(user_id)

            # Показываем тренировку на День 1
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

    # ── Задачи (чеклист) ──
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

        # Сбрасываем предупреждение о выбытии если участник вернулся
        if db.has_dropout_warning(user_id):
            db.clear_dropout_warning(user_id)

        user_row = db.get_user(user_id)

        if len(completed) >= 5:
            # Все 5 задач выполнены — показываем финальный вид и поздравление
            await query.edit_message_text(
                build_checklist_message(user_row, day, completed),
                reply_markup=all_done_keyboard()
            )
            evening_text = ct.get_evening(day, all_done=True)
            await query.message.reply_text(f"{evening_text}\n\n_День {day} засчитан! 🎉_")

            # Проверяем и выдаём ачивки
            stats = db.get_stats(user_id)
            new_achievements = ct.check_achievements(stats["days_completed"])
            for ach_id in new_achievements:
                if not db.has_achievement(user_id, ach_id):
                    db.award_achievement(user_id, ach_id)
                    ach_text = ct.get_achievement_text(ach_id)
                    await query.message.reply_text(ach_text)
        else:
            await query.edit_message_text(
                build_checklist_message(user_row, day, completed),
                reply_markup=tasks_keyboard(day, completed)
            )
        return

    # ── Кнопка прогресса ──
    if data == "progress":
        await query.answer()
        await query.message.reply_text(build_progress_text(user_id))
    elif data == "noop":
        await query.answer()


# ── Обработка оплаты ──────────────────────────────────────────

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждаем платёж — должен ответить в течение 10 секунд"""
    query = update.pre_checkout_query
    logger.info(f"PreCheckout: user={query.from_user.id}, сумма={query.total_amount}, payload={query.invoice_payload}")
    try:
        await query.answer(ok=True)
    except Exception as e:
        logger.error(f"Ошибка PreCheckout: {e}")


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Платёж прошёл — регистрируем участника и запускаем онбординг"""
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
        f"✅ Оплата подтверждена!\n\n"
        f"Участие: {participation_rub} ₽\n"
        f"Ставка: {stake_rub} ₽ (вернётся когда пройдёшь 77 дней)\n\n"
        f"Последний шаг — выбери часовой пояс 👇"
    )
    await update.message.reply_text(
        "🦥 Выбери часовой пояс, чтобы я присылал задания в 6:00 по твоему времени:",
        reply_markup=timezone_keyboard()
    )


async def send_invoice_for_stake(
    chat_id: int,
    user_id: int,
    stake: int,
    context: ContextTypes.DEFAULT_TYPE
):
    """Отправляет инвойс ЮКассы"""
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
                    "payment_subject": "service"
                },
                {
                    "description": "Ставка участника",
                    "quantity": "1.00",
                    "amount": {"value": f"{stake_rub}.00", "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service"
                }
            ],
            "tax_system_code": 2
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
            payload=f"zarik_{user_id}_{stake}",
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
        logger.info(f"Инвойс отправлен: user={user_id}, ставка={stake_rub}₽")
    except Exception as e:
        logger.error(f"Ошибка send_invoice user={user_id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Ошибка при создании счёта: {e}"
        )


# ── Обработчик текстовых сообщений ───────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # ── Ввод суммы ставки ──
    if user_id in _awaiting_stake:
        clean = text.replace(" ", "").replace(",", "").replace("₽", "").replace("руб", "")
        if not clean.isdigit():
            await update.message.reply_text(
                "🦥 Введи просто число — например: 5000"
            )
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
        stake_kopecks = amount_rub * 100
        await send_invoice_for_stake(user_id, user_id, stake_kopecks, context)
        return

    # ── Кнопки нижнего меню ──
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
        # Любой другой текст — напоминаем о меню
        user_row = db.get_user(user_id)
        if not user_row:
            step_data = FUNNEL[1]
            await update.message.reply_text(
                step_data["text"],
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=funnel_keyboard(*step_data["button"])
            )
        else:
            await update.message.reply_text(
                "🦥 Используй кнопки меню 👇",
                reply_markup=MAIN_MENU
            )


# ── Планировщик: утренняя рассылка (6:00) ────────────────────

async def job_morning(context: ContextTypes.DEFAULT_TYPE):
    """6:00 по местному времени — утреннее послание + чеклист"""
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
                continue  # День уже закрыт, не беспокоим

            missed = db.get_missed_streak(user["user_id"])

            # Проверяем выбытие: 24ч прошло после предупреждения
            if db.should_dropout(user["user_id"]):
                last_day = db.get_last_completed_day(user["user_id"])
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=ct.get_dropout_message(last_day)
                )
                db.deactivate_user(user["user_id"])
                logger.info(f"Участник {user['user_id']} выбыл после 24ч без ответа")
                continue

            # 3 пропуска — спецсообщение удержания (только один раз)
            if missed >= 3 and not db.has_dropout_warning(user["user_id"]):
                last_day = db.get_last_completed_day(user["user_id"])
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=ct.get_miss_message(3, last_day)
                )
                db.set_dropout_warning_sent(user["user_id"])
                logger.info(f"Отправлено предупреждение о выбытии: {user['user_id']}")
                continue

            # Формируем утреннее сообщение
            morning_msg = build_checklist_message(user, day, completed)

            # При 1–2 пропусках добавляем мотивирующий блок
            if 1 <= missed <= 2:
                last_day = db.get_last_completed_day(user["user_id"])
                miss_block = ct.get_miss_message(missed, last_day)
                morning_msg += f"\n\n{miss_block}"

            await context.bot.send_message(
                chat_id=user["user_id"],
                text=morning_msg,
                reply_markup=tasks_keyboard(day, completed)
            )

        except Exception as e:
            logger.warning(f"Утренняя рассылка {user['user_id']}: {e}")


# ── Планировщик: дневная проверка (14:00) ────────────────────

async def job_afternoon(context: ContextTypes.DEFAULT_TYPE):
    """14:00 по местному времени — дневная проверка"""
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

            if all_done:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=text
                )
            else:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=text,
                    reply_markup=tasks_keyboard(day, completed)
                )

        except Exception as e:
            logger.warning(f"Дневная рассылка {user['user_id']}: {e}")


# ── Планировщик: вечерняя проверка (21:00) ───────────────────

async def job_evening(context: ContextTypes.DEFAULT_TYPE):
    """21:00 по местному времени — вечерняя проверка"""
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

            if all_done:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=text
                )
            else:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=text,
                    reply_markup=tasks_keyboard(day, completed)
                )

        except Exception as e:
            logger.warning(f"Вечерняя рассылка {user['user_id']}: {e}")


# ── Планировщик: еженедельные итоги (воскресенье 20:00) ──────

async def job_weekly(context: ContextTypes.DEFAULT_TYPE):
    """Воскресенье 20:00 по местному времени — еженедельные итоги группы"""
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
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=text
            )

        except Exception as e:
            logger.warning(f"Еженедельная рассылка {user['user_id']}: {e}")


# ── Админ-команды ─────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сводка для администратора"""
    if not is_admin(update.effective_user.id):
        return

    count = db.get_user_count()
    total_stake = db.get_total_stake() // 100
    active = db.get_all_active_users()

    lines = [
        "🦥 Админ · Сводка",
        "",
        f"👥 Участников: {count}",
        f"🏃 Активных: {len(active)}",
        f"💰 Ставки: {total_stake:,} ₽".replace(",", " "),
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список участников"""
    if not is_admin(update.effective_user.id):
        return

    users = db.get_all_users()
    if not users:
        await update.message.reply_text("Участников пока нет.")
        return

    lines = ["🦥 Участники", ""]
    for u in users:
        day = db.get_current_day(u["user_id"]) if u["onboarding_complete"] else 0
        stake = (u["stake_amount"] or 0) // 100
        name = u["full_name"] or u["first_name"] or "—"
        status = f"День {day}" if u["onboarding_complete"] else "⏳ онбординг"
        lines.append(f"• {name} | {stake}₽ | {status}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n_...ещё. /export для полного списка._"
    await update.message.reply_text(text)


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Экспорт участников в CSV"""
    if not is_admin(update.effective_user.id):
        return

    import csv, io
    users = db.get_all_users()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Имя", "Username", "ФИО", "Телефон", "Email",
                     "Ставка (₽)", "Участие (₽)", "День", "Дата регистрации"])
    for u in users:
        day = db.get_current_day(u["user_id"]) if u["onboarding_complete"] else 0
        writer.writerow([
            u["user_id"],
            u["first_name"] or "",
            u["username"] or "",
            u["full_name"] or "",
            u["phone"] or "",
            u["email"] or "",
            (u["stake_amount"] or 0) // 100,
            (u["participation_fee"] or 0) // 100,
            day,
            u["created_at"] or "",
        ])

    output.seek(0)
    bio = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    bio.name = "zarik_participants.csv"
    await update.message.reply_document(
        document=bio,
        filename="zarik_participants.csv",
        caption=f"📊 Участники Зарик — {len(users)} чел."
    )


async def cmd_setday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """[ТЕСТ ADMIN] /setday N — переключить на день N"""
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
    """[ТЕСТ ADMIN] /reset_user [user_id] — полный сброс пользователя до нового старта"""
    if not is_admin(update.effective_user.id):
        return
    # Если передан user_id — сбрасываем его, иначе — себя
    args = context.args
    if args and args[0].isdigit():
        target_id = int(args[0])
    else:
        target_id = update.effective_user.id
    db.reset_user(target_id)
    await update.message.reply_text(
        f"🛠 Пользователь {target_id} сброшен. Можно заново /start"
    )


# ── Запуск ────────────────────────────────────────────────────

def main():
    db.init_db()
    logger.info("БД инициализирована")

    app = Application.builder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("today",    cmd_today))
    app.add_handler(CommandHandler("progress", cmd_progress))

    # Админ
    app.add_handler(CommandHandler("admin",   cmd_admin))
    app.add_handler(CommandHandler("users",   cmd_users))
    app.add_handler(CommandHandler("export",  cmd_export))
    app.add_handler(CommandHandler("setday",     cmd_setday))
    app.add_handler(CommandHandler("reset_user", cmd_reset_user))

    # Оплата
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Inline-кнопки
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Текст + кнопки нижнего меню
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Расписание — проверяем каждый час
    jq = app.job_queue
    jq.run_repeating(job_morning,   interval=3600, first=10,  name="morning")
    jq.run_repeating(job_afternoon, interval=3600, first=30,  name="afternoon")
    jq.run_repeating(job_evening,   interval=3600, first=50,  name="evening")
    jq.run_repeating(job_weekly,    interval=3600, first=70,  name="weekly")

    logger.info("🦥 Зарик запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
