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
import logging
import os

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
# ID канала из https://max.ru/id781109203385_biz
MAX_CHANNEL_ID      = int(os.environ.get("MAX_CHANNEL_ID", "781109203385"))

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
    Проверяет, подписан ли пользователь на канал MAX_CHANNEL_ID.
    Бот должен быть добавлен в канал как участник/администратор.
    При любой ошибке API возвращает False (не пропускаем).
    """
    if not MAX_CHANNEL_ID:
        return True  # если канал не задан — не блокируем
    try:
        bot = get_client()
        member = await bot.get_chat_member(MAX_CHANNEL_ID, max_user_id)
        logger.info(f"MAX sub check: user={max_user_id} member={member}")
        return member is not None
    except Exception as e:
        logger.error(f"MAX sub check error user={max_user_id}: {e}")
        return False


# ── Тексты ────────────────────────────────────────────────────

SUBSCRIBE_TEXT = (
    "🦥 Привет!\n\n"
    "Я подготовил тебе подарок — интерактивный трекер для достижения твоих задач.\n\n"
    "Чтобы получить его, подпишись на канал — там всё самое важное о программе.\n\n"
    "👉 t.me/kabanovofficial\n\n"
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
    "*iPhone:* нажми ··· → Открыть в Safari → Поделиться → На экран Домой\n"
    "*Android:* открой в Chrome → меню ⋮ → Добавить на главный экран"
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
    "Нажми кнопку — и вперёд 👇"
)

NO_PRESSURE_TEXT = (
    "🦥 Никакого давления.\n\n"
    "Ты можешь вернуться в любой момент — кнопка доступна выше."
)

FOLLOWUP_TEXTS = {
    2: (
        "🦥 Как ты?\n\n"
        "Вчера смотрел трекер — решил попробовать?\n\n"
        "77 дней начинаются с одного шага.\n"
        "Цена пока 1990 ₽ 👇"
    ),
    3: (
        "📊 Три дня, как ты видел трекер.\n\n"
        "Знаешь что объединяет тех, кто прошёл 77 дней?\n"
        "Они просто **начали**.\n\n"
        "Не «когда будет время». Не «с понедельника».\n"
        "Сегодня. Прямо сейчас 👇"
    ),
    7: (
        "🏁 Прошла неделя.\n\n"
        "Ты видел трекер, читал о программе.\n\n"
        "Это последнее напоминание — я не хочу быть навязчивым.\n\n"
        "Если решишь начать — кнопка ниже.\n"
        "Если нет — всё равно желаю тебе результата 🦥"
    ),
}

FAREWELL_TEXT = (
    "🦥 Окей, не буду больше напоминать.\n\n"
    "Если захочешь вернуться — просто напиши /start.\n\n"
    "Удачи тебе, что бы ты ни выбрал 🙌"
)

PURCHASED_TEXT = (
    "🎉 Ура, ты в игре!\n\n"
    "Переходи в основной бот — там тебя уже ждут 👇"
)

# ── Кнопки ────────────────────────────────────────────────────

def _subscribe_buttons() -> list[list[dict]]:
    return [[_btn_callback("✅ Я подписался", "sub_check")]]


def _tracker_buttons() -> list[list[dict]]:
    tracker_url = f"{WEBAPP_URL.rstrip('/')}/tracker" if WEBAPP_URL else "https://t.me/shagov77_bot"
    return [[_btn_link("📊 Открыть трекер", tracker_url)]]


def _tracker_question_buttons(attempt: int = 1) -> list[list[dict]]:
    return [[
        _btn_callback("✅ Да, всё отлично!", "tracker_ok"),
        _btn_callback("😕 Нет, не вышло", f"tracker_fail_{attempt}"),
    ]]


def _offer_buttons() -> list[list[dict]]:
    if PAYMENT_URL:
        return [[_btn_link("🚀 Начать — 1990 ₽", PAYMENT_URL)]]
    return [[_btn_callback("🚀 Начать", "buy_now")]]


def _follow_buttons() -> list[list[dict]]:
    if PAYMENT_URL:
        return [[_btn_link("Присоединиться — 1990 ₽", PAYMENT_URL)]]
    return []


def _program_bot_buttons() -> list[list[dict]]:
    if MAX_PROGRAM_BOT_URL:
        return [[_btn_link("Открыть основной бот 🦥", MAX_PROGRAM_BOT_URL)]]
    return []


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

    await bot.send_message(max_user_id, SUBSCRIBE_TEXT, buttons=_subscribe_buttons())


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
                "Подпишись — и нажми кнопку снова 👇\n\n"
                "👉 max.ru/id781109203385_biz",
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

    # ── Покупка (fallback без PAYMENT_URL) ────────────────────
    elif payload == "buy_now":
        await bot.answer_callback(callback_id, "Переходи по ссылке для оплаты!")


async def on_message(max_user_id: int, text: str, username: str, first_name: str):
    """Текстовые сообщения."""
    bot = get_client()
    cmd = text.strip().lower().split()[0] if text.strip() else ""

    # /start — любой пользователь
    if cmd in ("/start", "start", "старт"):
        await on_bot_started(max_user_id, username, first_name)
        return

    # Только для администратора
    if max_user_id != MAX_ADMIN_USER_ID:
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
            f"Всего лидов:          {t:>4}  (100%)",
            f"Подписались:          {f['subscribed']:>4}  ({pct(f['subscribed'], t)}){drop(f['subscribed'], t)}",
            f"Получили трекер:      {f['tracker_sent']:>4}  ({pct(f['tracker_sent'], t)}){drop(f['tracker_sent'], f['subscribed'])}",
            f"Увидели вопрос:       {f['question_sent']:>4}  ({pct(f['question_sent'], t)}){drop(f['question_sent'], f['tracker_sent'])}",
            f"Ответили:             {f['question_replied']:>4}  ({pct(f['question_replied'], t)}) → ✅ {f['replied_yes']} / 😕 {f['replied_no']}",
            f"Знакомство:           {f['intro_sent']:>4}  ({pct(f['intro_sent'], t)}){drop(f['intro_sent'], f['question_replied'])}",
            f"Получили оффер:       {f['offer_sent']:>4}  ({pct(f['offer_sent'], t)}){drop(f['offer_sent'], f['intro_sent'])}",
            f"Оплатили:             {f['purchased']:>4}  ({pct(f['purchased'], t)}){drop(f['purchased'], f['offer_sent'])}",
            "",
            f"Follow-up 2д:  {f['follow_2']:>3} | 3д: {f['follow_3']:>3} | 7д: {f['follow_7']:>3}",
            f"Прощание:      {f['final_sent']:>3}",
            "",
            f"🎯 Конверсия: **{pct(f['purchased'], f['tracker_sent'])}** (оплатили / трекер)",
        ]
        await bot.send_message(max_user_id, "\n".join(lines))

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
