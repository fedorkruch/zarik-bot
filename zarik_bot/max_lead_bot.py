"""
max_lead_bot.py — лид-бот для Мессенджера MAX (аналог @Shagov77_bot).

Воронка:
  1. /start → предложение подписаться на канал
  2. «Я подписался» → трекер-подарок (ссылка) + вопрос через 20 сек
  3. «Всё получилось» (попытка 1 или 2) → +2 сек → знакомство с программой → +10 сек → оффер
  4. «Нет, не вышло» попытка 1 → инструкция iOS/Android → +20 сек → вопрос снова
  5. «Нет, не вышло» попытка 2 → инструкция → +30 сек → автоматически знакомство + оффер
  6. +60 сек без покупки → «без давления»
  7. Follow-up через 2 / 3 / 7 дней (APScheduler, каждый час)
  8. Через 1 час после day-7 → прощальное сообщение
  9. После покупки → ссылка на основной бот

Переменные окружения:
  MAX_LEAD_BOT_TOKEN     — токен лид-бота в MAX
  MAX_ADMIN_USER_ID      — MAX user_id администратора
  MAX_PROGRAM_BOT_URL    — ссылка на основной бот (max.ru/…)
  WEBAPP_URL             — URL мини-аппа
  PAYMENT_URL            — URL страницы оплаты (ЮКасса / Tinkoff)
  MAX_LEAD_WEBHOOK_PATH  — путь вебхука, по умолчанию /webhook/max-lead
"""
import asyncio
import base64
import difflib
import json as _json
import logging
import os
import re
import uuid

import aiohttp
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

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

# ── ЮКасса ────────────────────────────────────────────────────
YOOKASSA_SHOP_ID    = os.environ.get("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY", "")

# ── Тест-пользователи (получают тестовые цены) ───────────────
_test_ids_raw = os.environ.get("MAX_TEST_USER_IDS", "")
TEST_USER_IDS = {int(x) for x in _test_ids_raw.split(",") if x.strip().isdigit()}

# ── Цены ─────────────────────────────────────────────────────
COURSE_PRICE_PROD = 199_000   # 1990 ₽ — боевой
COURSE_PRICE_TEST = 6_000     # 60 ₽  — тест
MIN_STAKE_PROD    = 10_000    # 100 ₽
MIN_STAKE_TEST    = 1_000     # 10 ₽

def _price_for(max_user_id: int) -> int:
    return COURSE_PRICE_TEST if max_user_id in TEST_USER_IDS else COURSE_PRICE_PROD

def _min_stake_for(max_user_id: int) -> int:
    return MIN_STAKE_TEST if max_user_id in TEST_USER_IDS else MIN_STAKE_PROD
# Если MAX_SKIP_SUB_CHECK=1 — проверка подписки отключена (для каналов-пабликов,
# где API MAX не позволяет проверить подписчиков)
MAX_SKIP_SUB_CHECK  = os.environ.get("MAX_SKIP_SUB_CHECK", "0").strip() in ("1", "true", "yes")

# ID канала — принимаем и число, и URL вида https://max.ru/id781109203385_biz
def _parse_channel_id(raw: str) -> int:
    """Извлекает числовой ID из строки или URL типа https://max.ru/id123456_biz."""
    import re as _re
    raw = raw.strip()
    m = _re.search(r"id(\d+)", raw)
    if m:
        return int(m.group(1))
    digits = _re.sub(r"\D", "", raw)
    return int(digits) if digits else 0

MAX_CHANNEL_ID = _parse_channel_id(os.environ.get("MAX_CHANNEL_ID", "781109203385"))

# ── Состояние диалога (ожидание ввода суммы ставки) ──────────
_user_state: dict[int, dict] = {}   # max_user_id → {"awaiting_stake": True}

# ── Глобальный клиент ─────────────────────────────────────────
_client: MaxClient | None = None

def get_client() -> MaxClient:
    global _client
    if _client is None:
        _client = MaxClient(MAX_LEAD_TOKEN)
    return _client

# ── Проверка подписки на канал ────────────────────────────────

async def is_subscribed(max_user_id: int) -> bool:
    """
    Проверяет подписку пользователя на канал.

    Если MAX_SKIP_SUB_CHECK=1 — всегда возвращает True (для публичных каналов-пабликов,
    где MAX API не предоставляет доступа к списку подписчиков).

    Если MAX_CHANNEL_ID не задан — тоже не блокируем.
    При ошибке API — пропускаем (не блокируем пользователя из-за сбоя).
    """
    if MAX_SKIP_SUB_CHECK:
        logger.info(f"MAX sub check SKIPPED (MAX_SKIP_SUB_CHECK=1): user={max_user_id}")
        return True
    if not MAX_CHANNEL_ID:
        return True
    try:
        bot = get_client()
        member = await bot.get_chat_member(MAX_CHANNEL_ID, max_user_id)
        logger.info(f"MAX sub check: user={max_user_id} channel={MAX_CHANNEL_ID} member={member}")
        if member is None:
            logger.warning(
                f"MAX sub check: пустой ответ для user={max_user_id} "
                f"(возможно, канал — паблик и API не поддерживает проверку подписчиков). "
                f"Установите MAX_SKIP_SUB_CHECK=1 чтобы отключить проверку."
            )
        return member is not None
    except Exception as e:
        logger.error(f"MAX sub check error user={max_user_id}: {e} — пропускаем (не блокируем)")
        return True  # при ошибке API не блокируем пользователя


# ── Тексты ────────────────────────────────────────────────────

def _subscribe_text(first_name: str) -> str:
    return (
        f"🦥 Привет, {first_name}!\n\n"
        "Я подготовил тебе подарок — универсальный интерактивный трекер для достижения твоих задач.\n\n"
        "Чтобы получить его, подпишись на канал — там всё самое важное о программе.\n\n"
        "Как подпишешься — нажми кнопку ниже 👇"
    )

ALREADY_IN_PROGRAM_TEXT = (
    "✅ Ты уже в программе!\n\n"
    "Переходи к боту — там тебя ждут 👇"
)

TRACKER_TEXT = (
    "🎁 **Держи трекер достижений — твой подарок!**\n\n"
    "Устанавливай количество дней на пути к цели, прописывай ежедневные задачи. "
    "Отмечай каждый день выполнение и наблюдай свой путь.\n\n"
    "Это твой личный дашборд прогресса 👇\n\n"
    "📱 *iPhone: нажми ··· → Открыть в Safari → Поделиться → На экран Домой*\n"
    "🤖 *Android: открой в Chrome → меню ⋮ → Добавить на главный экран*"
)

TRACKER_QUESTION_TEXT = "🦥 Ну как? Всё получилось с трекером? Нравится? 👇"

TRACKER_INSTRUCTIONS_TEXT = (
    "📱 **Как открыть трекер и сохранить на экран:**\n\n"
    "**Если у тебя iPhone (iOS):**\n"
    "1️⃣ Нажми кнопку «📊 Открыть трекер» выше\n"
    "2️⃣ Нажми *···* (три точки) вверху браузера\n"
    "3️⃣ Выбери *Открыть в Safari*\n"
    "4️⃣ В Safari: *Поделиться* → *На экран Домой*\n\n"
    "**Если у тебя Android:**\n"
    "1️⃣ Нажми кнопку «📊 Открыть трекер» выше\n"
    "2️⃣ Chrome покажет баннер — нажми *Добавить*\n"
    "Или: меню *⋮* → *Добавить на главный экран*\n\n"
    "Сделай это — и трекер будет работать как отдельное приложение 👆"
)

INTRO_TEXT = (
    "🦥 Кстати, мы уже собрали **программу 77 дней** — "
    "для тех, кто хочет реальных изменений без надрыва.\n\n"
    "Каждое утро твой личный бот-наставник присылает тебе задачи дня. "
    "Ты отмечаешь что выполнил — и видишь как растёт прогресс.\n\n"
    "Никакого жёсткого расписания.\n"
    "Никаких часовых тренировок.\n"
    "Никакого «начну с понедельника».\n\n"
    "Просто маленькие шаги каждый день — "
    "и через 77 дней ты не узнаешь себя.\n\n"
    "*Это работает, потому что не требует героизма — только привычки.*"
)

OFFER_TEXT = (
    "💳 **Сегодня — 1990 ₽ вместо 4990 ₽**\n\n"
    "Полный доступ к программе на 77 дней:\n"
    "• Персональный наставник-бот\n"
    "• Трекер задач и прогресса\n"
    "• Еженедельная статистика группы\n"
    "• Ачивки за серии и достижения\n\n"
    "🎯 **Плюс — добавь ставку на себя**\n\n"
    "Положи любую сумму сверху стоимости программы.\n"
    "Пройдёшь все 77 дней — получишь её обратно.\n\n"
    "*Это не штраф. Это твой личный договор с собой.*\n\n"
    "Нажми кнопку — оплата онлайн 👇"
)

NO_PRESSURE_TEXT = (
    "🦥 Никакого давления.\n\n"
    "Ты можешь вернуться в любой момент — кнопка доступна выше."
)

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
        "Они просто **начали**.\n\n"
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

FAREWELL_TEXT = (
    "🦥 Окей, не буду больше напоминать.\n\n"
    "Если захочешь вернуться — просто напиши /start.\n\n"
    "Удачи тебе, что бы ты ни выбрал(а) 🙌"
)

PURCHASED_TEXT = (
    "🎉 Оплата подтверждена! Добро пожаловать в программу.\n\n"
    "Теперь переходи к боту Зарика — он тебя встретит и проведёт через онбординг:"
)

# ── Кнопки ────────────────────────────────────────────────────

def _subscribe_buttons() -> list[list[dict]]:
    return [
        [_btn_link("📢 Подписаться на канал", "https://max.ru/id781109203385_biz")],
        [_btn_callback("✅ Я подписался", "sub_check")],
    ]


def _is_discount_request(text: str) -> bool:
    """Проверяет, есть ли в тексте слово «скидка» или близкое к нему (с учётом опечаток)."""
    tl = text.lower()
    if re.search(r"скид", tl):
        return True
    for word in re.findall(r"[а-яёa-z]+", tl):
        if len(word) >= 5 and difflib.SequenceMatcher(None, word, "скидка").ratio() >= 0.70:
            return True
    return False


def _tracker_buttons() -> list[list[dict]]:
    tracker_url = f"{WEBAPP_URL.rstrip('/')}/tracker" if WEBAPP_URL else "https://t.me/shagov77_bot"
    return [[_btn_link("📊 Открыть трекер", tracker_url)]]


def _tracker_question_buttons(attempt: int = 1) -> list[list[dict]]:
    return [[
        _btn_callback("✅ Да, всё отлично!", "tracker_ok"),
        _btn_callback("😕 Нет, не вышло", f"tracker_fail_{attempt}"),
    ]]


def _offer_buttons() -> list[list[dict]]:
    return [[_btn_callback("🚀 Начать за 1990 ₽", "buy_course")]]


def _stake_confirm_buttons() -> list[list[dict]]:
    return [
        [_btn_callback("✅ Да, хочу поставить", "stake_yes")],
        [_btn_callback("➡️ Нет, перейти к оплате", "stake_no")],
    ]


def _follow_buttons() -> list[list[dict]]:
    return [[_btn_callback("Присоединиться — 1990 ₽", "buy_course")]]


def _program_bot_buttons() -> list[list[dict]]:
    if MAX_PROGRAM_BOT_URL:
        return [[_btn_link("Открыть основной бот 🦥", MAX_PROGRAM_BOT_URL)]]
    return []


# ── ЮКасса — создание платежа ────────────────────────────────

def _build_receipt_items(course_kopecks: int, stake_kopecks: int = 0) -> list:
    """Список позиций чека для ЮКасса (54-ФЗ). vat_code=1 — без НДС."""
    def _rub(k: int) -> str:
        return f"{k / 100:.2f}"

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
    return items


def _valid_email(s: str) -> bool:
    """Email только из ASCII-символов, формат x@x.x (длина частей любая)."""
    email = s.strip()
    if not email.isascii():
        return False
    return bool(re.match(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$", email))


def _valid_phone(s: str) -> bool:
    digits = re.sub(r"[\s\-\(\)]", "", s.strip())
    if digits.startswith("8"):
        digits = "+7" + digits[1:]
    if not digits.startswith("+"):
        digits = "+" + digits
    return bool(re.match(r"^\+7\d{10}$", digits))


def _normalize_phone(s: str) -> str:
    digits = re.sub(r"[\s\-\(\)]", "", s.strip())
    if digits.startswith("8"):
        digits = "+7" + digits[1:]
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits


async def create_yookassa_payment(
    max_user_id: int,
    course_kopecks: int,
    stake_kopecks: int = 0,
    full_name: str = "",
    email: str = "",
    phone: str = "",
) -> dict:
    """
    Создаёт платёж в ЮКасса через REST API v3.
    Возвращает объект платежа с confirmation.confirmation_url, или {} при ошибке.
    """
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        logger.error("YooKassa credentials not set (YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY)")
        return {}

    total_kopecks = course_kopecks + stake_kopecks
    total_rub = f"{total_kopecks / 100:.2f}"
    description = (
        f"Программа «Зарик 77 дней» + ставка {stake_kopecks // 100} ₽"
        if stake_kopecks > 0
        else "Программа «Зарик 77 дней» — полный доступ на 77 дней"
    )

    # Данные покупателя для чека (54-ФЗ): обязателен email или телефон
    customer: dict = {}
    if full_name:
        customer["full_name"] = full_name
    if email:
        customer["email"] = email
    if phone:
        customer["phone"] = phone

    receipt_body: dict = {"items": _build_receipt_items(course_kopecks, stake_kopecks)}
    if customer:
        receipt_body["customer"] = customer

    body = {
        "amount": {"value": total_rub, "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": MAX_PROGRAM_BOT_URL or "https://max.ru",
        },
        "capture": True,
        "description": description,
        "metadata": {
            "max_user_id": str(max_user_id),
            "stake_kopecks": str(stake_kopecks),
            "course_kopecks": str(course_kopecks),
        },
        "receipt": receipt_body,
    }

    credentials = base64.b64encode(
        f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}".encode()
    ).decode()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.yookassa.ru/v3/payments",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Idempotence-Key": str(uuid.uuid4()),
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    logger.error(f"YooKassa create payment error {resp.status}: {data}")
                    return {}
                return data
    except Exception as e:
        logger.exception(f"YooKassa payment creation failed: {e}")
        return {}


async def send_course_payment_link(
    max_user_id: int,
    stake_kopecks: int = 0,
    full_name: str = "",
    email: str = "",
    phone: str = "",
):
    """Создаёт платёж в ЮКасса и отправляет пользователю ссылку для оплаты."""
    course_kopecks = _price_for(max_user_id)
    bot = get_client()

    # Сохраняем данные покупателя в БД
    if full_name or email or phone:
        db.save_max_lead_buyer_info(max_user_id, full_name, email, phone)

    payment = await create_yookassa_payment(
        max_user_id, course_kopecks, stake_kopecks,
        full_name=full_name, email=email, phone=phone,
    )
    if not payment:
        await bot.send_message(
            max_user_id,
            "⚠️ Не удалось создать счёт. Попробуй позже или напиши нам."
        )
        return

    pay_url = payment.get("confirmation", {}).get("confirmation_url", "")
    if not pay_url:
        await bot.send_message(
            max_user_id,
            "⚠️ Не удалось получить ссылку на оплату. Попробуй позже."
        )
        return

    total_rub = (course_kopecks + stake_kopecks) // 100
    if stake_kopecks > 0:
        label = f"💳 Оплатить {total_rub} ₽ (программа + ставка {stake_kopecks // 100} ₽)"
    else:
        label = f"💳 Оплатить {total_rub} ₽"

    await bot.send_message(
        max_user_id,
        "✅ Счёт создан! Нажми кнопку для оплаты:",
        buttons=[[_btn_link(label, pay_url)]]
    )
    db.mark_max_lead_invoice_sent(max_user_id)
    logger.info(
        f"YooKassa payment link sent: user={max_user_id}, "
        f"total={total_rub}₽, stake={stake_kopecks // 100}₽"
    )


# ── Вспомогательные ───────────────────────────────────────────

def _call_later(delay: float, coro_factory):
    """Планирует корутину через delay секунд в текущем event loop."""
    loop = asyncio.get_event_loop()
    loop.call_later(delay, lambda: asyncio.ensure_future(coro_factory()))


# ── Шаги воронки ─────────────────────────────────────────────

async def _step_send_tracker(max_user_id: int):
    """Шаг 2: отправляем трекер-подарок."""
    bot = get_client()
    await bot.send_message(max_user_id, TRACKER_TEXT, buttons=_tracker_buttons())
    db.mark_max_lead_tracker_sent(max_user_id)
    # Через 20 сек — вопрос о трекере (попытка 1)
    _call_later(20, lambda: _step_ask_tracker(max_user_id, attempt=1))


async def _step_ask_tracker(max_user_id: int, attempt: int = 1):
    """Шаг 3: спрашиваем, всё ли получилось с трекером."""
    if db.is_max_lead_purchased(max_user_id):
        return
    bot = get_client()
    await bot.send_message(
        max_user_id, TRACKER_QUESTION_TEXT,
        buttons=_tracker_question_buttons(attempt)
    )


async def _step_send_intro(max_user_id: int):
    """Шаг 4: знакомство с программой."""
    if db.is_max_lead_purchased(max_user_id):
        return
    bot = get_client()
    await bot.send_message(max_user_id, INTRO_TEXT)
    db.mark_max_lead_intro_sent(max_user_id)
    # Через 10 сек — оффер
    _call_later(10, lambda: _step_send_offer(max_user_id))


async def _step_send_offer(max_user_id: int):
    """Шаг 5: оффер с ценой."""
    if db.is_max_lead_purchased(max_user_id):
        return
    bot = get_client()
    await bot.send_message(max_user_id, OFFER_TEXT, buttons=_offer_buttons())
    db.mark_max_lead_pitch_sent(max_user_id)
    # Через 60 сек без покупки → «без давления»
    _call_later(60, lambda: _step_no_pressure(max_user_id))


async def _step_no_pressure(max_user_id: int):
    """Шаг 6: без давления — если не купил за 60 сек."""
    if db.is_max_lead_purchased(max_user_id):
        return
    bot = get_client()
    await bot.send_message(max_user_id, NO_PRESSURE_TEXT)


async def _step_farewell(max_user_id: int):
    """Шаг: прощальное сообщение после day-7 follow-up."""
    if db.is_max_lead_purchased(max_user_id):
        return
    bot = get_client()
    try:
        await bot.send_message(max_user_id, FAREWELL_TEXT)
        db.mark_max_lead_final(max_user_id)
    except Exception as e:
        logger.warning(f"MAX farewell error user={max_user_id}: {e}")


# ── Follow-up (APScheduler) ───────────────────────────────────

async def _job_followup():
    """Периодический follow-up: каждый час, аналог Telegram job_followup_check."""
    leads = db.get_all_max_pitched_leads()
    now = datetime.utcnow()
    bot = get_client()

    for lead in leads:
        uid = lead["max_user_id"]
        pitch_at = lead.get("pitch_sent_at")
        if not pitch_at:
            continue
        try:
            pitch_dt = datetime.fromisoformat(pitch_at)
        except Exception:
            continue

        hours = (now - pitch_dt).total_seconds() / 3600

        if db.is_max_lead_purchased(uid):
            continue

        # Day 7: последний follow-up + прощание через 1ч
        if hours >= 168 and not lead.get("follow_7_sent_at") and not lead.get("final_sent_at"):
            await _send_followup(bot, uid, day=7)
            _call_later(3600, lambda u=uid: _step_farewell(u))
            continue

        # Day 3
        if (hours >= 48
                and not lead.get("follow_3_sent_at")
                and not lead.get("follow_7_sent_at")
                and not lead.get("final_sent_at")):
            await _send_followup(bot, uid, day=3)
            continue

        # Day 2
        if hours >= 24 and not lead.get("follow_2_sent_at"):
            await _send_followup(bot, uid, day=2)
            continue


async def _send_followup(bot: MaxClient, max_user_id: int, day: int):
    text = FOLLOWUP_TEXTS.get(day, "")
    if not text:
        return
    try:
        await bot.send_message(max_user_id, text, buttons=_follow_buttons())
        db.mark_max_lead_follow(max_user_id, day)
        logger.info(f"MAX follow-up day {day} → user={max_user_id}")
    except Exception as e:
        logger.warning(f"MAX follow-up day {day} error user={max_user_id}: {e}")


# ── Обработчики событий ───────────────────────────────────────

async def on_bot_started(max_user_id: int, username: str, first_name: str):
    logger.info(f"MAX lead on_bot_started: user_id={max_user_id}")
    bot = get_client()
    db.upsert_max_lead(max_user_id, username, first_name)

    # Уже купил — редирект в основной бот
    if db.is_max_lead_purchased(max_user_id):
        await bot.send_message(
            max_user_id, ALREADY_IN_PROGRAM_TEXT,
            buttons=_program_bot_buttons()
        )
        return

    await bot.send_message(max_user_id, _subscribe_text(first_name), buttons=_subscribe_buttons())


async def on_callback(max_user_id: int, callback_id: str, payload: str,
                      username: str, first_name: str):
    bot = get_client()

    # ── Проверка подписки ─────────────────────────────────────
    if payload == "sub_check":
        await bot.answer_callback(callback_id)
        db.upsert_max_lead(max_user_id, username, first_name)

        subscribed = await is_subscribed(max_user_id)
        if not subscribed:
            await bot.send_message(
                max_user_id,
                "🦥 Я не обнаружил подписки на канал.\n\n"
                "Как только подпишешься — я сразу пришлю подарок 🎁\n\n"
                "Подпишись и нажми кнопку снова 👇",
                buttons=_subscribe_buttons()
            )
            return

        db.mark_max_lead_subscribed(max_user_id)

        if db.is_max_lead_purchased(max_user_id):
            await bot.send_message(
                max_user_id, ALREADY_IN_PROGRAM_TEXT,
                buttons=_program_bot_buttons()
            )
            return

        await _step_send_tracker(max_user_id)

    # ── Трекер: всё получилось ────────────────────────────────
    elif payload == "tracker_ok":
        await bot.answer_callback(callback_id)
        db.mark_max_lead_tracker_reply(max_user_id, yes=True)
        await bot.send_message(max_user_id, "🎉 Супер, я очень рад что всё получилось!")
        # Через 2 сек — знакомство с программой
        _call_later(2, lambda: _step_send_intro(max_user_id))

    # ── Трекер: не получилось ─────────────────────────────────
    elif payload.startswith("tracker_fail_"):
        await bot.answer_callback(callback_id)
        db.mark_max_lead_tracker_reply(max_user_id, yes=False)
        attempt = int(payload.split("_")[-1])

        # Инструкция
        await bot.send_message(
            max_user_id, TRACKER_INSTRUCTIONS_TEXT,
            buttons=_tracker_buttons()
        )

        if attempt < 2:
            # Спрашиваем снова через 20 сек (попытка 2)
            _call_later(20, lambda: _step_ask_tracker(max_user_id, attempt=2))
        else:
            # Второй «нет» — через 30 сек автоматически идём дальше
            _call_later(30, lambda: _step_send_intro(max_user_id))

    # ── Покупка курса → предложение ставки ───────────────────
    elif payload == "buy_course":
        await bot.answer_callback(callback_id)
        if db.is_max_lead_purchased(max_user_id):
            await bot.send_message(
                max_user_id, ALREADY_IN_PROGRAM_TEXT,
                buttons=_program_bot_buttons()
            )
            return
        db.mark_max_lead_start_clicked(max_user_id)
        min_r = _min_stake_for(max_user_id) // 100
        stake_text = (
            f"🎯 **Хочешь добавить ставку на себя?**\n\n"
            f"Ставка добавляется к стоимости программы.\n"
            f"Пройдёшь все 77 дней — вернём её обратно.\n\n"
            f"*Минимальная сумма — {min_r} ₽.*"
        )
        await bot.send_message(max_user_id, stake_text, buttons=_stake_confirm_buttons())
        db.mark_max_lead_stake_asked(max_user_id)

    # ── Да, хочу ставку → просим ввести сумму ────────────────
    elif payload == "stake_yes":
        await bot.answer_callback(callback_id)
        if db.is_max_lead_purchased(max_user_id):
            return
        db.mark_max_lead_stake_choice(max_user_id, "yes")
        _user_state[max_user_id] = {"awaiting_stake": True}
        min_r = _min_stake_for(max_user_id) // 100
        await bot.send_message(
            max_user_id,
            f"💬 Введи сумму ставки в рублях (минимум {min_r} ₽):"
        )

    # ── Нет, без ставки → сбор данных для чека ───────────────
    elif payload == "stake_no":
        await bot.answer_callback(callback_id)
        if db.is_max_lead_purchased(max_user_id):
            return
        db.mark_max_lead_stake_choice(max_user_id, "no")
        _user_state[max_user_id] = {"awaiting_name": True, "stake_kopecks": 0}
        await bot.send_message(max_user_id, "📝 Введи своё полное имя (ФИО) для чека:")


async def on_message(max_user_id: int, text: str, username: str, first_name: str):
    """Текстовые сообщения."""
    bot = get_client()
    cmd = text.strip().lower().split()[0] if text.strip() else ""

    # /start — любой пользователь
    if cmd in ("/start", "start", "старт"):
        await on_bot_started(max_user_id, username, first_name)
        return

    # /reset_user — доступен тест-юзерам для самосброса (до общего блока)
    if cmd == "/reset_user" and max_user_id in TEST_USER_IDS:
        db.reset_max_lead_keep_purchased(max_user_id)
        _user_state.pop(max_user_id, None)
        # Сбрасываем прогресс и в программном боте (users таблица)
        internal_uid = db.get_max_internal_id(max_user_id)
        if internal_uid is not None:
            db.reset_user_keep_payment(internal_uid)
            db.save_payment(user_id=internal_uid, charge_id=f"max_test_{max_user_id}",
                            participation_fee=0, stake_amount=0)
            db.set_onboarding_step(internal_uid, "welcome")
        await bot.send_message(
            max_user_id,
            "✅ Аккаунт сброшен полностью (лид-бот + программный бот).\n\nОтправь /start — начнём с начала."
        )
        logger.info(f"DEV сброс (TEST_USER): max_user_id={max_user_id}")
        return

    # ── Машина состояний: сбор данных для оплаты ─────────────
    # Проверяем ДО разделения на admin/user — состояние может быть у любого
    state = _user_state.get(max_user_id, {})

    if state.get("awaiting_stake"):
        min_stake = _min_stake_for(max_user_id)
        clean = text.strip().replace(",", ".").replace(" ", "")
        try:
            amount_rub = float(clean)
        except ValueError:
            await bot.send_message(
                max_user_id,
                f"⚠️ Некорректный формат ввода данных.\n\n"
                f"Введи число, например: 100\n"
                f"Минимальная сумма — {min_stake // 100} ₽."
            )
            return
        amount_kopecks = int(amount_rub * 100)
        if amount_kopecks < min_stake:
            await bot.send_message(
                max_user_id,
                f"⚠️ Минимальная ставка — {min_stake // 100} ₽. Введи другую сумму:"
            )
            return
        if db.is_max_lead_purchased(max_user_id):
            _user_state.pop(max_user_id, None)
            return
        _user_state[max_user_id] = {"awaiting_name": True, "stake_kopecks": amount_kopecks}
        await bot.send_message(max_user_id, "📝 Введи своё полное имя (ФИО) для чека:")
        return

    if state.get("awaiting_name"):
        # Принимаем любой формат ФИО: с пробелами, без, любые символы; минимум 2 символа
        name = re.sub(r"\s+", " ", text.strip())
        if len(name) < 2:
            await bot.send_message(max_user_id, "⚠️ Введи имя (минимум 2 символа):")
            return
        _user_state[max_user_id] = {
            "awaiting_email": True,
            "stake_kopecks": state.get("stake_kopecks", 0),
            "full_name": name,
        }
        await bot.send_message(max_user_id, "📧 Введи email для чека:")
        return

    if state.get("awaiting_email"):
        email = text.strip().lower()
        if not _valid_email(email):
            await bot.send_message(
                max_user_id,
                "⚠️ Некорректный email. Только латинские буквы, например: ivan@mail.ru или user123@example.com:"
            )
            return
        _user_state[max_user_id] = {
            "awaiting_phone": True,
            "stake_kopecks": state.get("stake_kopecks", 0),
            "full_name": state.get("full_name", ""),
            "email": email,
        }
        await bot.send_message(
            max_user_id,
            "📱 Введи номер телефона для чека (например: +79001234567 или 89001234567):"
        )
        return

    if state.get("awaiting_phone"):
        phone_raw = text.strip()
        if not _valid_phone(phone_raw):
            await bot.send_message(
                max_user_id,
                "⚠️ Некорректный номер. Введи российский номер (например: +79001234567 или 89001234567):"
            )
            return
        phone         = _normalize_phone(phone_raw)
        full_name     = state.get("full_name", "")
        email         = state.get("email", "")
        stake_kopecks = state.get("stake_kopecks", 0)
        _user_state.pop(max_user_id, None)
        if db.is_max_lead_purchased(max_user_id):
            return
        await send_course_payment_link(
            max_user_id,
            stake_kopecks=stake_kopecks,
            full_name=full_name,
            email=email,
            phone=phone,
        )
        return

    # ── Обычные пользователи (не в состоянии ввода) ──────────
    if max_user_id != MAX_ADMIN_USER_ID:
        # Запрос скидки — специальный ответ
        if _is_discount_request(text):
            await bot.send_message(
                max_user_id,
                "Предложение 1990 вместо 4900 действует в течение трёх дней с текущего момента, "
                "мы специально уронили цену, чтобы дать возможность большему количеству участников "
                "начать двигаться к своим целям."
            )
        else:
            await bot.send_message(max_user_id, "⚠️ Некорректный формат ввода данных.")
        return

    # Только для администратора

    if text.startswith("/reset_user"):
        parts = text.split()
        # Администратор может сбросить любого; тест-юзеры — только себя
        if max_user_id == MAX_ADMIN_USER_ID:
            target_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else max_user_id
        elif max_user_id in TEST_USER_IDS:
            target_id = max_user_id   # тест-юзер сбрасывает только себя
        else:
            return  # молча игнорируем

        if target_id in TEST_USER_IDS:
            # Мягкий сброс: воронка обнуляется, purchased_at сохраняется
            db.reset_max_lead_keep_purchased(target_id)
            _user_state.pop(target_id, None)
            # Также сбрасываем прогресс в программном боте
            target_internal_uid = db.get_max_internal_id(target_id)
            if target_internal_uid is not None:
                db.reset_user_keep_payment(target_internal_uid)
                db.save_payment(user_id=target_internal_uid, charge_id=f"max_test_{target_id}",
                                participation_fee=0, stake_amount=0)
                db.set_onboarding_step(target_internal_uid, "welcome")
            await bot.send_message(
                target_id,
                "✅ Твой аккаунт сброшен. Отправь /start — начнём с начала."
            )
            if target_id != max_user_id:
                await bot.send_message(
                    max_user_id,
                    f"🛠 MAX-лид {target_id} сброшен (лид + программный бот, purchased сохранён)."
                )
        else:
            # Полный сброс
            db.reset_max_lead(target_id)
            _user_state.pop(target_id, None)
            await bot.send_message(
                max_user_id,
                f"🛠 MAX-лид {target_id} полностью сброшен."
            )
        logger.info(f"reset_user: admin={max_user_id} target={target_id}")
        return

    if text.startswith("/stats") or text.startswith("/leads"):
        f = db.get_max_funnel_stats()
        if not f or not f.get("total"):
            await bot.send_message(max_user_id, "📊 Лидов пока нет.")
            return
        msg = (
            f"📊 **CRM — MAX лид-бот**\n\n"
            f"Всего лидов: {f['total']}\n"
            f"Подписались на канал: {f['subscribed']}\n"
            f"Получили трекер: {f['tracker_sent']}\n"
            f"Увидели вопрос: {f['question_sent']}\n"
            f"Ответили: {f['question_replied']} (✅ {f['replied_yes']} / 😕 {f['replied_no']})\n"
            f"Получили оффер: {f['offer_sent']}\n"
            f"Купили: {f['purchased']} 🎉\n"
            f"Follow-up 2д/3д/7д: {f['follow_2']}/{f['follow_3']}/{f['follow_7']}\n\n"
            f"Конверсия: {f['purchased'] / max(f['tracker_sent'], 1) * 100:.1f}% (купили / трекер)"
        )
        await bot.send_message(max_user_id, msg)

    elif text.startswith("/funnel"):
        f = db.get_max_funnel_stats()
        if not f or not f.get("total"):
            await bot.send_message(max_user_id, "📊 Данных пока нет.")
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
            "🔽 **Воронка MAX лид-бот**\n",
            f"Всего лидов:              {t:>4}  (100%)",
            f"Подписались:              {f['subscribed']:>4}  ({pct(f['subscribed'], t)}){drop(f['subscribed'], t)}",
            f"Получили трекер:          {f['tracker_sent']:>4}  ({pct(f['tracker_sent'], t)}){drop(f['tracker_sent'], f['subscribed'])}",
            f"Увидели вопрос:           {f['question_sent']:>4}  ({pct(f['question_sent'], t)}){drop(f['question_sent'], f['tracker_sent'])}",
            f"Ответили:                 {f['question_replied']:>4}  ({pct(f['question_replied'], t)}) → ✅ {f['replied_yes']} / 😕 {f['replied_no']}",
            f"Знакомство:               {f['intro_sent']:>4}  ({pct(f['intro_sent'], t)}){drop(f['intro_sent'], f['question_replied'])}",
            f"Получили оффер:           {f['offer_sent']:>4}  ({pct(f['offer_sent'], t)}){drop(f['offer_sent'], f['intro_sent'])}",
            f"Нажали «Начать»:          {f.get('start_clicked', 0):>4}  ({pct(f.get('start_clicked', 0), t)}){drop(f.get('start_clicked', 0), f['offer_sent'])}",
            f"Увидели вопрос о ставке:  {f.get('stake_asked', 0):>4}  ({pct(f.get('stake_asked', 0), t)}) → Да: {f.get('stake_yes') or 0} / Нет: {f.get('stake_no') or 0}",
            f"Получили счёт:            {f.get('invoice_sent', 0):>4}  ({pct(f.get('invoice_sent', 0), t)}){drop(f.get('invoice_sent', 0), f.get('stake_asked', 0))}",
            f"Оплатили:                 {f['purchased']:>4}  ({pct(f['purchased'], t)}){drop(f['purchased'], f.get('invoice_sent', 0))}",
            "",
            f"Follow-up 2д:  {f['follow_2']:>3} | 3д: {f['follow_3']:>3} | 7д: {f['follow_7']:>3}",
            f"Прощание:      {f['final_sent']:>3}",
            "",
            f"🎯 Конверсия: **{pct(f['purchased'], f['tracker_sent'])}** (оплатили / трекер)",
        ]
        await bot.send_message(max_user_id, "\n".join(lines))

    elif text.startswith("/chats"):
        # Список чатов бота — нужен чтобы найти правильный chat_id канала
        result = await bot.get_chats()
        chats = result.get("chats", [])
        if not chats:
            await bot.send_message(max_user_id, f"❌ Чатов нет или ошибка API:\n`{result}`")
            return
        lines = [f"📋 Чаты бота ({len(chats)}):"]
        for c in chats[:20]:
            lines.append(
                f"id: `{c.get('chat_id')}` | type: {c.get('type')} | {c.get('title') or c.get('name','?')}"
            )
        await bot.send_message(max_user_id, "\n".join(lines))

    elif text.startswith("/check_sub"):
        # /check_sub <user_id>  или для самого себя
        parts = text.split()
        target_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else max_user_id
        member = await bot.get_chat_member(MAX_CHANNEL_ID, target_id)
        await bot.send_message(
            max_user_id,
            f"🔍 check_sub\nchannel_id: `{MAX_CHANNEL_ID}`\nuser_id: `{target_id}`\nresult: `{member}`"
        )

    elif text.startswith("/broadcast "):
        msg = text[len("/broadcast "):]
        logger.info(f"MAX lead broadcast queued: {msg[:50]}")
        await bot.send_message(max_user_id, "✅ Рассылка поставлена в очередь (not implemented)")


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
            logger.info(f"MAX lead unhandled update_type={update_type!r}")

    except Exception:
        logger.exception(f"Error processing MAX lead update: {update_type}")


# ── Инициализация вебхука и планировщика ─────────────────────

async def setup(webapp_base_url: str):
    """Регистрирует вебхук в MAX и запускает APScheduler. Вызывается при старте webapp_server."""
    if not MAX_LEAD_TOKEN:
        logger.warning("MAX_LEAD_BOT_TOKEN не задан — MAX лид-бот не запущен")
        return
    bot = get_client()
    me = await bot.get_me()
    logger.info(f"MAX лид-бот: {me.get('name', '?')} (@{me.get('username', '?')})")
    webhook_url = f"{webapp_base_url.rstrip('/')}{WEBHOOK_PATH}"
    await bot.setup_webhook(webhook_url)
    logger.info(f"MAX лид-бот вебхук: {webhook_url}")

    # APScheduler: follow-up каждый час
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _job_followup,
        trigger="interval",
        hours=1,
        id="max_lead_followup",
        replace_existing=True,
        next_run_time=datetime.utcnow(),  # первый запуск сразу
    )
    scheduler.start()
    logger.info("MAX лид-бот follow-up scheduler запущен")
