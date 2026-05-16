"""
webapp_server.py — aiohttp сервер для Telegram Mini App.
Отдаёт miniapp.html и REST-эндпоинты для работы с данными пользователя.
"""
import hashlib
import hmac
import json
import logging
import os
import time as _time
import urllib.parse
from pathlib import Path

from aiohttp import web

import database as db
import content as ct
from workout import get_workout

logger = logging.getLogger(__name__)

PROGRAM_BOT_TOKEN = os.environ.get("PROGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
PORT              = int(os.environ.get("PORT", 8080))
ADMIN_ID          = int(os.environ.get("ADMIN_ID", "283760217"))
TEST_USER_IDS     = {283760217, 262479340}   # те же что в program_bot
MINIAPP_HTML        = Path(__file__).parent / "miniapp.html"
TRACKER_GIFT_HTML   = Path(__file__).parent / "tracker_gift.html"
APP_ICON            = Path(__file__).parent / "app_icon.jpg"

TASK_LABELS = [
    ("💪", "Тренировка"),
    ("💧", "Вода · 2 л / 8 стаканов"),
    ("📚", "Чтение · 10 страниц"),
    ("🥗", "Без фастфуда и снеков"),
    ("🚫", "День без алкоголя"),
]


# ── Авторизация через Telegram initData ──────────────────────

def _parse_init_data_params(raw: str) -> dict:
    """Разбирает initData в словарь (без проверки подписи)."""
    params = {}
    for item in raw.split("&"):
        k, _, v = item.partition("=")
        params[urllib.parse.unquote_plus(k)] = urllib.parse.unquote_plus(v)
    return params


def validate_init_data(raw: str) -> dict | None:
    """Проверяет подпись initData от Telegram WebApp. Возвращает user-dict или None."""
    try:
        params = _parse_init_data_params(raw)
        hash_recv = params.pop("hash", None)
        if not hash_recv:
            logger.warning("initData: нет hash")
            return None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret = hmac.new(b"WebAppData", PROGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, hash_recv):
            logger.warning(
                f"initData: HMAC не совпал. token_len={len(PROGRAM_BOT_TOKEN)} "
                f"recv={hash_recv[:8]}… comp={computed[:8]}…"
            )
            return None
        return json.loads(params.get("user", "{}"))
    except Exception as e:
        logger.warning(f"initData validation error: {e}")
        return None


def get_user_id_from_request(request: web.Request) -> int | None:
    raw = request.headers.get("X-Init-Data") or request.rel_url.query.get("init_data", "")
    if not raw:
        return None

    # Полная валидация (HMAC)
    user = validate_init_data(raw)
    if user and "id" in user:
        return int(user["id"])

    # Для тест-юзеров — парсим без проверки подписи (на случай неверного токена в env)
    try:
        params = _parse_init_data_params(raw)
        uid = int(json.loads(params.get("user", "{}")).get("id", 0))
        if uid in TEST_USER_IDS:
            logger.warning(f"DEV bypass: tест-юзер {uid} без HMAC (проверь PROGRAM_BOT_TOKEN в env)")
            return uid
    except Exception:
        pass

    # Подписанный токен от бота: ?uid=X&ts=Y&sig=Z (работает для всех пользователей)
    uid_q = request.rel_url.query.get("uid", "")
    ts_q  = request.rel_url.query.get("ts",  "")
    sig_q = request.rel_url.query.get("sig", "")
    if uid_q.isdigit() and ts_q.isdigit() and sig_q:
        try:
            uid = int(uid_q)
            ts  = int(ts_q)
            # Токен действителен 10 минут
            if abs(_time.time() - ts) <= 600:
                expected = hmac.new(
                    PROGRAM_BOT_TOKEN.encode(),
                    f"{uid}:{ts}".encode(),
                    hashlib.sha256,
                ).hexdigest()[:20]
                if hmac.compare_digest(expected, sig_q):
                    logger.info(f"Auth via signed URL token: uid={uid}")
                    return uid
                else:
                    logger.warning(f"Signed token HMAC mismatch for uid={uid}")
            else:
                logger.warning(f"Signed token expired for uid={uid_q}")
        except Exception as e:
            logger.warning(f"Token validation error: {e}")

    # Последний фоллбек: ?uid=XXX без подписи (только для TEST_USER_IDS)
    if uid_q.isdigit():
        uid = int(uid_q)
        if uid in TEST_USER_IDS:
            logger.warning(f"DEV fallback: test user {uid} via unsigned ?uid param")
            return uid

    return None


# ── Хелперы для формирования данных ──────────────────────────

def _make_week_bar(week_done: int, total: int = 7, width: int = 7) -> str:
    filled = round(week_done / total * width) if total else 0
    return "●" * filled + "·" * (width - filled)


def _make_progress_bar(day: int, total: int = 77, width: int = 20) -> str:
    if total == 0:
        return "·" * width
    pct = round(day / total * 100)
    filled = round(day / total * width)
    return "●" * filled + "·" * (width - filled), pct


def build_full_state(user_id: int) -> dict:
    user_row = db.get_user(user_id)
    started  = db.is_program_started(user_id)
    day      = db.get_current_day(user_id) if started else 0
    completed = sorted(db.get_completed_tasks(user_id, day)) if started else []
    stats    = db.get_stats(user_id)

    days_done  = stats["days_completed"]
    streak     = stats["streak"]
    percentile, pct_ctx = ct.get_planet_percentile(days_done)
    next_m     = ct.get_next_percentile_milestone(days_done)

    # Неделя
    week_num   = (day - 1) // 7 + 1 if day > 0 else 1
    week_start = (week_num - 1) * 7 + 1
    all_compl  = db.get_completed_days_set(user_id)
    week_done  = len({d for d in all_compl if week_start <= d <= day})
    week_header = ct.get_weekly_header(week_num)

    # Группа
    group = db.get_group_stats() or {}

    # Ачивки
    achievements = []
    for ach_id, threshold in ct.ACHIEVEMENT_ORDER:
        ach = ct.ACHIEVEMENTS[ach_id]
        achievements.append({
            "id":        ach_id,
            "icon":      ach["icon"],
            "name":      ach["name"],
            "threshold": threshold,
            "unlocked":  db.has_achievement(user_id, ach_id),
        })

    bar_chars, pct_num = _make_progress_bar(day - 1 if day > 0 else 0)

    # Утреннее / вечернее сообщения
    morning = ct.get_morning(day) if day > 0 else "🦥 Программа скоро начнётся!"
    evening = ct.get_evening(day, all_done=len(completed) == 5) if day > 0 else ""

    # Тренировка
    try:
        w = get_workout(dict(user_row), day) if (day > 0 and user_row) else {}
        workout_sub = (
            f"Отж: {w['pushup']['total']} · Присед: {w['squat']['total']} · Пресс: {w['abs']['total']}"
            if w else "Персональная тренировка"
        )
    except Exception:
        workout_sub = "Персональная тренировка"

    # Выживаемость группы
    g_total  = group.get("total", 0)
    g_active = group.get("active", 0)
    group_survival_pct = round(g_active / max(g_total, 1) * 100)

    # Тексты для шаринга
    n_done = len(completed)
    share_texts = []
    if day > 0:
        share_texts = [
            f"День {day}/77 🦥 Иду к {percentile} планеты. Сегодня — {n_done}/5 задач. Кто ещё в игре?",
            f"{pct_num}% пути пройдено в #77SoftChallenge. Без алкоголя, со спортом и книгами. Ленивец гордится. 🦥",
            f"Факт: только 6% людей читают 10 страниц в день. Я — один из них уже {day} дней подряд. #77Soft",
        ]

    return {
        "started":      started,
        "day":          day,
        "total_days":   77,
        "pct_num":      pct_num,
        "bar":          bar_chars,
        "completed_tasks": completed,
        "all_tasks_done": len(completed) == 5,
        "days_done":    days_done,
        "streak":       streak,
        "percentile":   percentile,
        "pct_ctx":      pct_ctx,
        "next_days":    next_m[0] if next_m else None,
        "next_pct":     next_m[1] if next_m else None,
        "week_num":     week_num,
        "week_done":    week_done,
        "week_bar":     _make_week_bar(week_done),
        "week_header":  week_header,
        "group_total":  g_total,
        "group_active": g_active,
        "group_dropped": g_total - g_active,
        "group_survival_pct": group_survival_pct,
        "achievements": achievements,
        "morning":      morning,
        "evening":      evening,
        "workout_sub":  workout_sub,
        "share_texts":  share_texts,
    }


# ── Маршруты ─────────────────────────────────────────────────

async def handle_index(request: web.Request) -> web.Response:
    html = MINIAPP_HTML.read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html")


async def handle_tracker(request: web.Request) -> web.Response:
    """Публичный интерактивный трекер — подарок лидам из @Shagov77_bot (PWA)."""
    html = TRACKER_GIFT_HTML.read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html")


async def handle_manifest(request: web.Request) -> web.Response:
    """PWA manifest.json для трекера."""
    manifest = {
        "name": "Трекер задач · Зарик",
        "short_name": "Трекер задач",
        "description": "Трекер целей от Зарика-Ленивца",
        "start_url": "/tracker",
        "display": "standalone",
        "background_color": "#0d0d12",
        "theme_color": "#0d0d12",
        "orientation": "portrait",
        "icons": [
            {"src": "/apple-touch-icon.png", "sizes": "192x192", "type": "image/jpeg"},
        ],
    }
    import json as _json
    return web.Response(
        text=_json.dumps(manifest, ensure_ascii=False),
        content_type="application/manifest+json",
    )


async def handle_sw(request: web.Request) -> web.Response:
    """Service Worker для офлайн-кэширования трекера."""
    sw_js = """
const CACHE = 'zarik-tracker-v1';
const URLS  = ['/tracker'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(URLS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.url.includes('/api/')) return;
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
""".strip()
    return web.Response(
        text=sw_js,
        content_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


async def handle_apple_icon(request: web.Request) -> web.Response:
    """Apple Touch Icon — app_icon.jpg как иконка для сохранения на экран."""
    if APP_ICON.exists():
        data = APP_ICON.read_bytes()
        return web.Response(body=data, content_type="image/jpeg")
    # Фоллбэк — пустой 1×1 PNG
    import base64
    png1x1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    return web.Response(body=png1x1, content_type="image/png")


async def handle_state(request: web.Request) -> web.Response:
    uid = get_user_id_from_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    # Тест-юзеры и админ проходят без проверки оплаты
    if uid not in TEST_USER_IDS and not db.is_payment_confirmed(uid):
        return web.json_response({"error": "not_paid"}, status=403)
    try:
        state = build_full_state(uid)
        return web.json_response(state)
    except Exception as e:
        logger.exception(f"state error for {uid}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_task(request: web.Request) -> web.Response:
    uid = get_user_id_from_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
        task_index = int(body.get("task_index", -1))
        if task_index not in range(5):
            return web.json_response({"error": "invalid task"}, status=400)
        day = db.get_current_day(uid)
        db.complete_task(uid, day, task_index)
        completed = sorted(db.get_completed_tasks(uid, day))
        return web.json_response({"completed_tasks": completed, "day": day})
    except Exception as e:
        logger.exception(f"task error for {uid}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_close_day(request: web.Request) -> web.Response:
    uid = get_user_id_from_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        day = db.get_current_day(uid)
        completed = db.get_completed_tasks(uid, day)
        if len(completed) < 5:
            return web.json_response(
                {"error": "not_all_done", "remaining": 5 - len(completed)},
                status=400
            )
        # Фиксируем ачивки
        stats = db.get_stats(uid)
        new_achs = ct.check_achievements(stats["days_completed"])
        unlocked = []
        for ach_id in new_achs:
            if not db.has_achievement(uid, ach_id):
                db.award_achievement(uid, ach_id)
                unlocked.append({
                    "id":   ach_id,
                    "name": ct.ACHIEVEMENTS[ach_id]["name"],
                    "icon": ct.ACHIEVEMENTS[ach_id]["icon"],
                })
        return web.json_response({"ok": True, "day": day, "new_achievements": unlocked})
    except Exception as e:
        logger.exception(f"close_day error for {uid}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_set_mode(request: web.Request) -> web.Response:
    """POST /api/set_mode {"miniapp": false} — переключает режим отображения."""
    uid = get_user_id_from_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    use_mini = bool(body.get("miniapp", True))
    db.set_miniapp_mode(uid, use_mini)
    return web.json_response({"ok": True, "miniapp": use_mini})


# ── Сборка ───────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/",                    handle_index)
    app.router.add_get("/app",                 handle_index)
    app.router.add_get("/tracker",             handle_tracker)
    app.router.add_get("/manifest.json",       handle_manifest)
    app.router.add_get("/sw.js",               handle_sw)
    app.router.add_get("/apple-touch-icon.png", handle_apple_icon)
    app.router.add_get("/icon-192.png",        handle_apple_icon)
    app.router.add_get("/icon-512.png",        handle_apple_icon)
    app.router.add_get("/api/state",           handle_state)
    app.router.add_post("/api/task",           handle_task)
    app.router.add_post("/api/close",          handle_close_day)
    app.router.add_post("/api/mode",           handle_set_mode)
    return app


async def run_server():
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🌐 WebApp сервер запущен на :{PORT}")
    import asyncio
    await asyncio.Event().wait()
