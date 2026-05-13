"""
bot.py — главный файл бота Зарик
Запуск: python3 bot.py
Переменные окружения (обязательные):
  BOT_TOKEN       — токен от @BotFather
  PROVIDER_TOKEN  — токен YooKassa (shopId:LIVE:secret)
Опциональные:
  PARTICIPATION_FEE_KOPECKS — стоимость участия в копейках (по умолчанию 499000 = 4990₽)
  STAKE_MIN_RUB             — минимальная ставка в рублях (по умолчанию 500)
  DATA_DIR                  — папка для zarik.db (по умолчанию рядом с bot.py)
"""
import json
import logging
import os
from datetime import datetime as dt
import pytz

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
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
from keyboards import tasks_keyboard, all_done_keyboard, timezone_keyboard, TIMEZONES

# ── Конфигурация ──────────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
PROVIDER_TOKEN   = os.environ["PROVIDER_TOKEN"]
PARTICIPATION_FEE = int(os.environ.get("PARTICIPATION_FEE_KOPECKS", "499000"))  # 4990₽ по умолчанию
STAKE_MIN_RUB     = int(os.environ.get("STAKE_MIN_RUB", "500"))                 # 500₽ по умолчанию
ADMIN_ID          = int(os.environ.get("ADMIN_ID", "283760217"))                # Telegram ID администратора
MIGRATE_THRESHOLD = 150                                                          # Порог для напоминания о переезде на Postgres

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

import time as _time
_last_start: dict[int, float] = {}

# Пользователи, ожидающие ввода суммы ставки
# user_id → message_id сообщения-приглашения (чтобы отредактировать его потом)
_awaiting_stake: dict[int, int] = {}


# ── Постоянная панель кнопок внизу чата ──────────────────────

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📋 Задания на сегодня"), KeyboardButton("📊 Мой прогресс")],
    ],
    resize_keyboard=True,
)


# ── Воронка продаж (5 шагов) ──────────────────────────────────

FUNNEL = {
    1: {
        "text": (
            "🦥 Привет\\.\n\n"
            "Сколько раз ты *начинал*?\n\n"
            "«С понедельника», «с нового года», «вот разберусь с делами\\.\\.\\.»\n\n"
            "А потом — жизнь брала своё\\. И снова всё как раньше\\.\n\n"
            "Это не слабость\\. Просто подход был не тот\\.\n\n"
            "У меня — другой\\. 👇"
        ),
        "button": ("Расскажи →", "funnel:2"),
    },
    2: {
        "text": (
            "Большинство программ работают на силе воли\\.\n\n"
            "Сила воли — ресурс конечный\\. Особенно к вечеру\\.\n\n"
            "Я работаю иначе: *4 маленькие задачи каждый день*\\.\n\n"
            "Не часовые тренировки\\. Не жёсткая диета\\.\n"
            "Просто — следующий шаг\\. Каждый день\\. 91 день\\.\n\n"
            "_Маленькое, но своё — всегда сильнее большого чужого\\._"
        ),
        "button": ("Что за программа? →", "funnel:3"),
    },
    3: {
        "text": (
            "🗓 *91 день\\. 6 направлений жизни\\.*\n\n"
            "📍 Модуль 1 \\(дни 1–21\\)\n"
            "Физическая база: вода, движение, активность, сон\n\n"
            "📍 Модуль 2 \\(дни 22–42\\)\n"
            "\\+ Мышление и самореализация\n\n"
            "📍 Модуль 3 \\(дни 43–63\\)\n"
            "\\+ Финансы и яркость жизни\n\n"
            "📍 Модуль 4 \\(дни 64–84\\)\n"
            "\\+ Управление временем\n\n"
            "🏁 Финал \\(85–91\\): всё на полную 🔥\n\n"
            "_Каждые 7 дней — итоги недели с твоими реальными цифрами\\._"
        ),
        "button": ("Что это даёт? →", "funnel:4"),
    },
    4: {
        "text": (
            "Через 91 день участники отмечают:\n\n"
            "💪 Тело — легче, подвижнее, сильнее\n"
            "🧠 Голова — яснее, меньше хаоса\n"
            "💰 Деньги — начали замечать и управлять\n"
            "⏱ Время — появилось, хотя часов в сутках столько же\n"
            "🌈 Жизнь — интереснее, не «потом», а прямо сейчас\n\n"
            "Это не обещания\\. Это то, что происходит, когда человек делает своё каждый день\\.\n\n"
            "*91 день маленьких шагов — и ты другой человек\\.*\n\n"
            "──────────────────\n\n"
            "Но есть одна вещь, которая отличает Зарика от любой другой программы\\.\n\n"
            "Это *ставка* — инструмент, который превращает намерение в обязательство\\.\n\n"
            "_Не внешний контроль\\. Не куратор над душой\\. Твой собственный договор с собой — подкреплённый деньгами\\._"
        ),
        "button": ("Как работает ставка? →", "funnel:5"),
    },
    5: {
        "text": (
            "🔑 *Главное отличие Зарика — ставка\\.*\n\n"
            "Ты сам выбираешь сумму, которую ставишь на себя\\.\n\n"
            "✅ Завершил 91 день → ставка возвращается полностью\n"
            "❌ Сошёл с дистанции → ставка остаётся\n\n"
            "Это не штраф\\. Это *твой личный договор с собой*\\.\n\n"
            "Мозг по\\-другому относится к тому, за что заплачено\\.\n"
            "Именно поэтому это работает там, где одна мотивация — нет\\.\n\n"
            "💳 Участие в программе: *10 ₽* \\(тест\\)\n"
            "_Ставка — любая сумма, ты решаешь 👇_"
        ),
        "button": ("Назначить ставку →", "funnel:stake"),
    },
}


def funnel_keyboard(button_text: str, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(button_text, callback_data=callback_data)
    ]])


# ── Онбординг (выбор часового пояса после оплаты) ────────────

ONBOARDING_TEXT = (
    "🦥 Отлично, {name}\\! Оплата прошла\\.\n\n"
    "Осталось последнее — выбери часовой пояс,\n"
    "чтобы я присылал задания ровно в 8:00 *по твоему времени* 👇"
)


# ── Формирование сообщения дня ────────────────────────────────

def get_task_labels(day: int) -> list:
    """Возвращает список (icon, label) для задач дня"""
    data = ct.get_day_content(day)
    return [(t["icon"], t["label"]) for t in data["tasks"]]


def build_day_message(day: int, completed: set) -> str:
    data = ct.get_day_content(day)
    done = len(completed)

    lines = [
        f"🦥 *{data['title']}*",
        "",
        f"_{data['morning']}_",
        "",
        "*Задачи на сегодня:*",
        "",
    ]

    for i, task in enumerate(data["tasks"]):
        check = "✅" if i in completed else "◻️"
        lines.append(f"{check} {task['icon']} *{task['label']}*")
        lines.append(f"   {task['description']}")
        lines.append("")

    if done == 4:
        lines.append("🎉 *Все задачи выполнены! День засчитан.*")
    else:
        lines.append(f"_Выполнено: {done} из 4_")

    return "\n".join(lines)


BAR_WIDTH = 15

def make_progress_bar(done: int, total: int, active: bool = True) -> str:
    if not active:
        return "▪️" * BAR_WIDTH
    if total == 0:
        return "⬜" * BAR_WIDTH + "  0%"
    pct = round(done / total * 100)
    filled = round(done / total * BAR_WIDTH)
    if done > 0 and filled == 0:
        filled = 1
    bar = "🟩" * filled + "⬜" * (BAR_WIDTH - filled)
    return f"{bar}  {pct}%"


def build_progress_text(user_id: int) -> str:
    stats = db.get_stats(user_id)
    modules = db.get_module_stats(user_id)

    current = stats["current_day"]
    done = stats["days_completed"]
    streak = stats["streak"]

    def day_word(n):
        if n == 1: return "день"
        if 2 <= n <= 4: return "дня"
        return "дней"

    lines = [
        f"🦥 *Прогресс · День {current} из 91*",
        "",
        f"Общий прогресс  ·  ✅ {done} {day_word(done)} засчитано",
        make_progress_bar(current - 1, 91, active=True),
    ]

    if streak > 0:
        lines.append(f"🔥 Серия: *{streak}* {day_word(streak)} подряд")

    lines.append("")
    lines.append("──────────────────")

    icons = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "🏁"]
    for i, m in enumerate(modules):
        lines.append("")
        if m["started"]:
            lines.append(f"{icons[i]} *М{m['num']} · {m['name']}*  ·  {m['done']} из {m['total']}")
            lines.append(make_progress_bar(m["done"], m["total"], active=True))
        else:
            lines.append(f"{icons[i]} М{m['num']} · {m['name']}  ·  впереди")
            lines.append(make_progress_bar(0, m["total"], active=False))

    return "\n".join(lines)


# ── Команды ───────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    now = _time.time()
    if now - _last_start.get(user.id, 0) < 5:
        return
    _last_start[user.id] = now

    existing = db.get_user(user.id)

    if existing:
        if not db.is_onboarding_complete(user.id):
            # Оплатил, но не выбрал часовой пояс
            await update.message.reply_text(
                "🦥 Нужно выбрать часовой пояс, чтобы я присылал задания вовремя 👇",
                reply_markup=timezone_keyboard()
            )
            return
        if not db.is_program_started(user.id):
            # Онбординг завершён, но старт ещё не наступил
            await update.message.reply_text(
                "🦥 Всё готово! Завтра в 8:00 пришлю первые задания.\n\n"
                "Используй кнопки меню внизу 👇",
                reply_markup=MAIN_MENU
            )
            return
        # Уже в программе — показываем задания дня
        day = db.get_current_day(user.id)
        completed = db.get_completed_tasks(user.id, day)
        await update.message.reply_text(f"🦥 День {day} из 91", reply_markup=MAIN_MENU)
        await update.message.reply_text(
            build_day_message(day, completed),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=tasks_keyboard(day, completed, get_task_labels(day))
        )
        return

    # Новый пользователь — запускаем воронку
    step = FUNNEL[1]
    await update.message.reply_text(
        step["text"],
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=funnel_keyboard(*step["button"])
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not db.get_user(user.id) or not db.is_onboarding_complete(user.id):
        await update.message.reply_text("Сначала напиши /start чтобы начать программу.")
        return
    day = db.get_current_day(user.id)
    completed = db.get_completed_tasks(user.id, day)
    await update.message.reply_text(
        build_day_message(day, completed),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=tasks_keyboard(day, completed, get_task_labels(day))
    )


async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not db.get_user(user.id):
        await update.message.reply_text("Сначала напиши /start чтобы начать программу.")
        return
    await update.message.reply_text(
        build_progress_text(user.id),
        parse_mode=ParseMode.MARKDOWN
    )


async def cmd_setday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """[ТЕСТ] /setday N — переключить на день N"""
    user = update.effective_user
    if not db.get_user(user.id):
        await update.message.reply_text("Сначала /start")
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Использование: /setday 7")
        return
    target = int(args[0])
    if not 1 <= target <= 91:
        await update.message.reply_text("День должен быть от 1 до 91")
        return
    db.set_day_for_testing(user.id, target)
    completed = db.get_completed_tasks(user.id, target)
    await update.message.reply_text(
        f"🛠 Тест: день *{target}*\n\n{build_day_message(target, completed)}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=tasks_keyboard(target, completed, get_task_labels(target))
    )


# ── Обработка всех callback кнопок ───────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    # ── Шаги воронки ──
    if data.startswith("funnel:"):
        key = data.split(":")[1]

        if key == "stake":
            # Просим ввести сумму ставки текстом
            msg = await query.edit_message_text(
                "💳 *Введи сумму ставки в рублях*\n\n"
                "Просто напиши число — например: *5000*\n\n"
                f"Минимум: {STAKE_MIN_RUB} ₽\n"
                "_Ставка вернётся полностью если пройдёшь 91 день_",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            # Запоминаем что пользователь сейчас вводит ставку
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

    # stake: больше не используется как callback (ввод идёт через текст)
    if data.startswith("stake:"):
        return

    # ── Выбор часового пояса ──
    if data.startswith("tz:"):
        tz_name = data.split(":", 1)[1]
        db.set_user_timezone(user_id, tz_name)
        db.complete_onboarding(user_id)

        tz_label = next((label for label, tz in TIMEZONES if tz == tz_name), tz_name)

        await query.edit_message_text(
            f"✅ Часовой пояс: {tz_label}\n\n"
            f"Завтра в 8:00 по твоему времени я пришлю первые задания.\n\n"
            f"Отдыхай — завтра начинаем 🦥",
        )
        await query.message.reply_text("Меню всегда под рукой 👇", reply_markup=MAIN_MENU)
        return

    # ── Кнопки задач ──
    if data.startswith("task:"):
        _, day_str, task_str = data.split(":")
        day = int(day_str)
        task_index = int(task_str)
        current_day = db.get_current_day(user_id)

        if day != current_day:
            await query.answer("Это задание уже не актуально.", show_alert=True)
            return

        db.complete_task(user_id, day, task_index)
        completed = db.get_completed_tasks(user_id, day)
        text = build_day_message(day, completed)
        data_content = ct.get_day_content(day)
        total_tasks = len(data_content["tasks"])
        labels = [(t["icon"], t["label"]) for t in data_content["tasks"]]

        if len(completed) >= total_tasks:
            await query.edit_message_text(
                text, parse_mode=ParseMode.MARKDOWN, reply_markup=all_done_keyboard()
            )
            congrats = f"🦥 {data_content['evening']}"
            if day in ct.MILESTONE_MESSAGES:
                congrats += f"\n\n{ct.MILESTONE_MESSAGES[day]}"
            if day in ct.REFLECTION_NOTES:
                congrats += f"\n\n{ct.REFLECTION_NOTES[day]}"
            congrats += f"\n\n_На сегодня всё. Завтра вернусь в 8:00 👋_"
            await query.message.reply_text(congrats, parse_mode=ParseMode.MARKDOWN)

            if day % 7 == 0:
                all_completed_days = db.get_completed_days_set(user_id)
                week_completed_days = {d for d in all_completed_days if day - 6 <= d <= day}
                stats_text = ct.build_weekly_stats(day, week_completed_days, all_completed_days)
                if stats_text:
                    await query.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text(
                text, parse_mode=ParseMode.MARKDOWN,
                reply_markup=tasks_keyboard(day, completed, labels)
            )
        return

    if data == "progress":
        await query.message.reply_text(build_progress_text(user_id), parse_mode=ParseMode.MARKDOWN)
    elif data == "program":
        await query.message.reply_text(
            "🗓 *Программа — 91 день*\n\n"
            "Модуль 1 (1–21): 🏃 Физическая база\n"
            "Модуль 2 (22–42): + 🧠 Мышление · ✨ Самореализация\n"
            "Модуль 3 (43–63): + 💰 Финансы · 🌈 Яркость жизни\n"
            "Модуль 4 (64–84): + ⏱ Управление временем\n"
            "Финал (85–91): Все направления на полную 🔥",
            parse_mode=ParseMode.MARKDOWN
        )
    elif data == "noop":
        pass


# ── Обработка оплаты ──────────────────────────────────────────

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждаем платёж — должен ответить в течение 10 секунд"""
    query = update.pre_checkout_query
    logger.info(f"PreCheckout: user={query.from_user.id}, сумма={query.total_amount}, валюта={query.currency}, payload={query.invoice_payload}")
    try:
        await query.answer(ok=True)
        logger.info(f"PreCheckout подтверждён для user={query.from_user.id}")
    except Exception as e:
        logger.error(f"Ошибка PreCheckout: {e}")


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Платёж прошёл — регистрируем участника и запускаем онбординг"""
    user = update.effective_user
    payment = update.message.successful_payment

    # Извлекаем сумму ставки из payload: zarik_{user_id}_{stake}
    try:
        stake_amount = int(payment.invoice_payload.split("_")[-1])
    except Exception:
        stake_amount = 0

    # Контакты из формы оплаты (ФИО, телефон, email)
    order_info = payment.order_info
    full_name = order_info.name        if order_info and order_info.name        else ""
    phone     = order_info.phone_number if order_info and order_info.phone_number else ""
    email     = order_info.email        if order_info and order_info.email        else ""

    # Регистрируем и сохраняем данные об оплате
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
    logger.info(f"Новый участник: {user.id} | {full_name} | {phone} | {email} | ставка {stake_amount//100}₽")

    stake_rub = stake_amount // 100

    participation_rub = PARTICIPATION_FEE // 100
    await update.message.reply_text(
        f"✅ *Оплата подтверждена\\!*\n\n"
        f"Участие: {participation_rub} ₽\n"
        f"Ставка: {stake_rub} ₽ _\\(вернётся когда пройдёшь 91 день\\)_\n\n"
        f"Добро пожаловать в программу\\. Последний шаг 👇",
        parse_mode=ParseMode.MARKDOWN_V2
    )

    # Запускаем онбординг — выбор часового пояса
    await update.message.reply_text(
        ONBOARDING_TEXT.format(name=user.first_name or "друг"),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=timezone_keyboard()
    )


async def send_invoice_for_stake(chat_id: int, user_id: int, stake: int, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет инвойс ЮКассы с участием + ставкой"""
    participation_rub = PARTICIPATION_FEE // 100
    stake_rub = stake // 100
    total_rub = participation_rub + stake_rub

    total_kopecks = PARTICIPATION_FEE + stake

    # Две позиции — участие и ставка
    prices = [
        LabeledPrice("Участие в программе", PARTICIPATION_FEE),
        LabeledPrice("Ставка (возврат при завершении)", stake),
    ]

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"💳 Всё готово\!\n\n"
            f"Участие: *{participation_rub} ₽*\n"
            f"Ставка: *{stake_rub} ₽* _\(вернётся при завершении 91 дня\)_\n\n"
            f"Итого: *{total_rub} ₽*\n\n"
            f"Нажми кнопку ниже чтобы оплатить 👇\n\n"
            f"_Если открываешь с компьютера — оплата может не работать в десктопном приложении Telegram\. "
            f"В этом случае открой бота с телефона\._"
        ),
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    # Чек для ЮКассы (обязателен по 54-ФЗ).
    # Суммы в items должны точно совпадать с суммами в prices.
    provider_data = {
        "receipt": {
            "items": [
                {
                    "description": "Участие в программе Зарик 91 день",
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{participation_rub}.00",
                        "currency": "RUB"
                    },
                    "vat_code": 1,               # 1 = без НДС
                    "payment_mode": "full_payment",
                    "payment_subject": "service"
                },
                {
                    "description": "Ставка участника",
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{stake_rub}.00",
                        "currency": "RUB"
                    },
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service"
                }
            ],
            "tax_system_code": 2               # 2 = УСН доходы (используется для АУСН)
        }
    }

    try:
        await context.bot.send_invoice(
            chat_id=chat_id,
            title="Зарик 91 день",
            description=(
                f"Участие {participation_rub}р + ставка {stake_rub}р. "
                f"Ставка возвращается при завершении 91 дня."
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
        logger.info(f"Инвойс отправлен: user={user_id}, участие={participation_rub}₽, ставка={stake_rub}₽")
    except Exception as e:
        logger.error(f"Ошибка send_invoice для user={user_id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Ошибка при создании счёта: {e}"
        )


# ── Обработчик всех текстовых сообщений ──────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # ── Если пользователь вводит сумму ставки ──
    if user_id in _awaiting_stake:
        # Убираем пробелы, запятые, знак ₽
        clean = text.replace(" ", "").replace(",", "").replace("₽", "").replace("руб", "")
        if not clean.isdigit():
            await update.message.reply_text(
                "🦥 Введи просто число — например: *5000*",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        amount_rub = int(clean)
        # Проверяем минимум ставки и минимум итоговой суммы (Telegram: мин. 60₽)
        participation_rub = PARTICIPATION_FEE // 100
        total_min = 60
        stake_effective_min = max(STAKE_MIN_RUB, total_min - participation_rub)

        if amount_rub < stake_effective_min:
            await update.message.reply_text(
                f"🦥 Минимальная ставка — *{stake_effective_min} ₽*. Попробуй ещё раз.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Всё ок — снимаем режим ожидания и отправляем инвойс
        del _awaiting_stake[user_id]
        stake_kopecks = amount_rub * 100
        await send_invoice_for_stake(user_id, user_id, stake_kopecks, context)
        return

    # ── Кнопки нижнего меню ──
    if text == "📋 Задания на сегодня":
        await cmd_today(update, context)
        return
    elif text == "📊 Мой прогресс":
        await cmd_progress(update, context)
        return

    # ── Всё остальное — некорректный ввод ──
    await handle_unexpected(update, context)


async def handle_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Неизвестная команда"""
    await handle_unexpected(update, context)


async def handle_unexpected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возвращает пользователя к актуальному состоянию бота"""
    user_id = update.effective_user.id

    await update.message.reply_text("❌ Некорректный формат ввода данных.")

    user = db.get_user(user_id)

    if not user:
        # Новый пользователь — показываем первый шаг воронки
        step = FUNNEL[1]
        await update.message.reply_text(
            step["text"],
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=funnel_keyboard(*step["button"])
        )
        return

    if not db.is_onboarding_complete(user_id):
        # Оплатил, но не выбрал пояс
        await update.message.reply_text(
            "🦥 Выбери часовой пояс, чтобы продолжить 👇",
            reply_markup=timezone_keyboard()
        )
        return

    if not db.is_program_started(user_id):
        # Онбординг завершён, но старт ещё не наступил
        await update.message.reply_text(
            "🦥 Всё готово! Завтра в 8:00 пришлю первые задания.\n\n"
            "Используй кнопки меню внизу 👇",
            reply_markup=MAIN_MENU
        )
        return

    # В программе — показываем задания текущего дня
    day = db.get_current_day(user_id)
    completed = db.get_completed_tasks(user_id, day)
    await update.message.reply_text(
        build_day_message(day, completed),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=tasks_keyboard(day, completed)
    )


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
    active_count = len(active)

    lines = [
        "🦥 *Админ · Сводка*",
        "",
        f"👥 Участников всего: *{count}*",
        f"🏃 Активных сейчас: *{active_count}*",
        f"💰 Общая сумма ставок: *{total_stake:,} ₽*".replace(",", " "),
        "",
        f"📊 До переезда на Postgres: *{max(0, MIGRATE_THRESHOLD - count)}* участников",
    ]

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список участников"""
    if not is_admin(update.effective_user.id):
        return

    users = db.get_all_users()
    if not users:
        await update.message.reply_text("Участников пока нет.")
        return

    lines = ["🦥 *Участники*", ""]
    for u in users:
        day = db.get_current_day(u["user_id"]) if u["onboarding_complete"] else 0
        stake = (u["stake_amount"] or 0) // 100
        name = u["full_name"] or u["first_name"] or "—"
        status = f"День {day}" if u["onboarding_complete"] else "⏳ онбординг"
        lines.append(f"• {name} | ставка {stake}₽ | {status}")

    # Telegram лимит 4096 символов — если много, режем
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n_...и ещё. Используй /export для полного списка._"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


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
    csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM для Excel
    bio = io.BytesIO(csv_bytes)
    bio.name = "zarik_participants.csv"

    await update.message.reply_document(document=bio, filename="zarik_participants.csv",
                                        caption=f"📊 Участники Зарик — {len(users)} чел.")


# ── Рассылки ──────────────────────────────────────────────────

async def send_daily_tasks(context: ContextTypes.DEFAULT_TYPE):
    """Рассылает задания участникам, у которых сейчас 8:00 по местному времени"""
    users = db.get_all_active_users()
    for user in users:
        try:
            user_tz = pytz.timezone(user["timezone"] or "Europe/Moscow")
            if dt.now(user_tz).hour != 8:
                continue
            day = db.get_current_day(user["user_id"])
            if day > 91:
                continue
            completed = db.get_completed_tasks(user["user_id"], day)
            total_tasks = len(ct.get_day_content(day)["tasks"])
            if len(completed) >= total_tasks:
                continue
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=build_day_message(day, completed),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=tasks_keyboard(day, completed, get_task_labels(day))
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить задание {user['user_id']}: {e}")


async def send_evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Напоминание тем, кто не выполнил задачи к 21:00 по местному времени"""
    users = db.get_all_active_users()
    for user in users:
        try:
            user_tz = pytz.timezone(user["timezone"] or "Europe/Moscow")
            if dt.now(user_tz).hour != 21:
                continue
            day = db.get_current_day(user["user_id"])
            completed = db.get_completed_tasks(user["user_id"], day)
            total_tasks = len(ct.get_day_content(day)["tasks"])
            if len(completed) >= total_tasks:
                continue
            remaining = total_tasks - len(completed)
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=(
                    f"🦥 Эй, до конца дня ещё есть время.\n"
                    f"Осталось: *{remaining}* из 4 задач.\n\n"
                    f"Нажми «📋 Задания на сегодня» чтобы открыть."
                ),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить напоминание {user['user_id']}: {e}")


async def check_migrate_threshold(context: ContextTypes.DEFAULT_TYPE):
    """Уведомляет админа когда участников стало >= 150"""
    count = db.get_user_count()
    if count >= MIGRATE_THRESHOLD:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"🚨 *Достигли {MIGRATE_THRESHOLD} участников!*\n\n"
                    f"Сейчас в программе: *{count}* чел.\n\n"
                    f"*Что нужно сделать:*\n\n"
                    f"1️⃣ *Переезд на PostgreSQL*\n"
                    f"SQLite начнёт тормозить. Напиши мне — сделаем миграцию за час.\n\n"
                    f"2️⃣ *Персональные данные (152-ФЗ)*\n"
                    f"— Зарегистрироваться как оператор ПД на pd.rkn.gov.ru\n"
                    f"— Опубликовать Политику конфиденциальности\n"
                    f"— Добавить согласие на обработку ПД перед оплатой\n"
                    f"— Проверить вопрос локализации данных (серверы РФ)\n\n"
                    f"Напиши мне — подготовлю все документы 🦥"
                ),
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info(f"Отправлено уведомление о milestone 150: {count} участников")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление о milestone: {e}")


# ── Запуск ────────────────────────────────────────────────────

def main():
    db.init_db()
    logger.info("База данных инициализирована")

    app = Application.builder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("progress", cmd_progress))
    app.add_handler(CommandHandler("setday", cmd_setday))

    # Админ-команды
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("export", cmd_export))

    # Оплата
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Inline-кнопки
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Текстовые сообщения: ввод ставки + кнопки нижнего меню
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Неизвестные команды (должен быть последним)
    app.add_handler(MessageHandler(filters.COMMAND, handle_unknown_command))

    # Расписание — каждый час проверяем у кого 8:00 или 21:00 по местному времени
    job_queue = app.job_queue
    job_queue.run_repeating(send_daily_tasks,       interval=3600,        first=10,  name="daily_tasks")
    job_queue.run_repeating(send_evening_reminder,  interval=3600,        first=20,  name="evening_reminder")
    job_queue.run_repeating(check_migrate_threshold, interval=86400,      first=60,  name="migrate_check")  # раз в сутки

    logger.info("🦥 Зарик запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
