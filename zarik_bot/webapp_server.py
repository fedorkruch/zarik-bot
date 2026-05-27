"""
webapp_server.py — aiohttp сервер для Telegram Mini App.
Отдаёт miniapp.html и REST-эндпоинты для работы с данными пользователя.
"""
import collections
import hashlib
import hmac
import json
import logging
import os
import time as _time
import urllib.parse
from pathlib import Path

import aiohttp
from aiohttp import web

import database as db
import content as ct
from workout import get_workout

logger = logging.getLogger(__name__)

PROGRAM_BOT_TOKEN = os.environ.get("PROGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
PORT              = int(os.environ.get("PORT", 8080))
ADMIN_ID          = int(os.environ["ADMIN_ID"])
# Тест-пользователи загружаются из env — никаких ID в репозитории
_test_ids_raw     = os.environ.get("TEST_USER_IDS", "")
TEST_USER_IDS     = {int(x) for x in _test_ids_raw.split(",") if x.strip().isdigit()}
# DEV-байпас доступен только если ENV != 'production'
_IS_DEV           = os.environ.get("ENV", "production").lower() != "production"
MINIAPP_HTML        = Path(__file__).parent / "miniapp.html"
TRACKER_GIFT_HTML   = Path(__file__).parent / "tracker_gift.html"
APP_ICON            = Path(__file__).parent / "app_icon.jpg"
HAPPY_IMG           = Path(__file__).parent / "Happy.png"
NORM_IMG            = Path(__file__).parent / "Norm.png"
SAD_IMG             = Path(__file__).parent / "Sad.png"

WEBAPP_URL = os.environ.get("WEBAPP_URL", "")

# ── Rate limiting (sliding window, in-memory) ─────────────────

_rl: dict[str, collections.deque] = {}

# (limit, window_sec) для каждого маршрута
_RL_RULES: dict[str, tuple[int, int]] = {
    "/api/task":  (20, 60),   # 20 req/min на пользователя
    "/api/close": (5,  60),   # 5 req/min
    "/api/state": (60, 60),   # 60 req/min
    "/api/mode":  (10, 60),   # 10 req/min
}
_RL_IP_LIMIT  = (200, 60)    # 200 req/min с одного IP (общий фоллбэк)


def _rate_ok(key: str, limit: int, window: int = 60) -> bool:
    """Sliding window rate limiter. Возвращает True если запрос разрешён."""
    now  = _time.time()
    q    = _rl.setdefault(key, collections.deque())
    cutoff = now - window
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


# ── Security headers + rate-limit middleware ──────────────────

@web.middleware
async def _security_middleware(request: web.Request, handler):
    ip   = request.remote or "unknown"
    path = request.path

    # Глобальный rate limit по IP
    if not _rate_ok(f"ip:{ip}", *_RL_IP_LIMIT):
        return web.json_response({"error": "rate_limited"}, status=429)

    # Per-user rate limit для API-маршрутов
    if path in _RL_RULES:
        # Используем первые 32 байта X-Init-Data как ключ (не раскрываем данные)
        user_key = (request.headers.get("X-Init-Data", "")[:32]
                    or request.rel_url.query.get("uid", ip))
        if not _rate_ok(f"api:{user_key}:{path}", *_RL_RULES[path]):
            return web.json_response({"error": "rate_limited"}, status=429)

    resp = await handler(request)

    # Security headers
    resp.headers.setdefault("X-Content-Type-Options",  "nosniff")
    resp.headers.setdefault("X-XSS-Protection",        "1; mode=block")
    resp.headers.setdefault("Referrer-Policy",          "strict-origin-when-cross-origin")
    # X-Frame-Options не ставим: Mini App работает внутри Telegram iframe
    return resp


TASK_LABELS = [
    ("💪", "Тренировка"),
    ("💧", "Вода · 2 л / 8 стаканов"),
    ("📚", "Чтение · 10 страниц"),
    ("🥗", "Без фастфуда и снеков"),
    ("🚫", "День без алкоголя"),
]

# Короткие подписи — совпадают с keyboards.py TASK_SHORT
_TASK_SHORT = [
    "💪 Тренировка",
    "💧 Вода — 8 стаканов",
    "📚 Чтение — 10 страниц",
    "🥗 Без фастфуда",
    "🚫 Без алкоголя сегодня",
]


async def _edit_tracker_keyboard(uid: int, day: int, completed: set) -> None:
    """
    Редактирует inline-клавиатуру трекер-сообщения в Telegram чтобы отразить
    текущее состояние задач (вызывается когда пользователь отмечает задачи в мини-апп).
    Если message_id не сохранён или токен недоступен — молча пропускает.
    """
    if not PROGRAM_BOT_TOKEN:
        return
    msg_id = db.get_tracker_message_id(uid, day)
    if not msg_id:
        return

    if len(completed) >= 5:
        # Все задачи выполнены
        markup = {
            "inline_keyboard": [
                [{"text": "🎉  День завершён!", "callback_data": "noop"}]
            ]
        }
    else:
        keyboard = []
        for i in range(5):
            mark = "✅" if i in completed else "⬜"
            keyboard.append([{
                "text": f"{mark}  {_TASK_SHORT[i]}",
                "callback_data": f"task:{day}:{i}"
            }])
        if WEBAPP_URL:
            keyboard.append([{
                "text": "📱 Открыть в мини-апп",
                "web_app": {"url": WEBAPP_URL}
            }])
        markup = {"inline_keyboard": keyboard}

    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"https://api.telegram.org/bot{PROGRAM_BOT_TOKEN}/editMessageReplyMarkup",
                json={
                    "chat_id": uid,
                    "message_id": msg_id,
                    "reply_markup": markup,
                },
                timeout=aiohttp.ClientTimeout(total=5),
            )
    except Exception as exc:
        logger.warning(f"edit_tracker_keyboard uid={uid}: {exc}")


# ── Авторизация через Telegram initData ──────────────────────

def _parse_init_data_params(raw: str) -> dict:
    """Разбирает initData в словарь (без проверки подписи)."""
    params = {}
    for item in raw.split("&"):
        k, _, v = item.partition("=")
        params[urllib.parse.unquote_plus(k)] = urllib.parse.unquote_plus(v)
    return params


_INIT_DATA_MAX_AGE = 24 * 3600   # initData действителен 24 часа


def validate_init_data(raw: str) -> dict | None:
    """Проверяет подпись и свежесть initData от Telegram WebApp. Возвращает user-dict или None."""
    try:
        params = _parse_init_data_params(raw)
        hash_recv = params.pop("hash", None)
        params.pop("signature", None)   # Bot API 8.0+: signature не входит в data-check-string
        if not hash_recv:
            logger.warning("initData: нет hash")
            return None

        # Проверяем свежесть auth_date (защита от replay-атак)
        auth_date = params.get("auth_date", "")
        if auth_date.isdigit():
            age = _time.time() - int(auth_date)
            if age > _INIT_DATA_MAX_AGE:
                logger.warning(f"initData: устарел на {int(age / 3600)} ч (auth_date={auth_date})")
                return None
        else:
            logger.warning("initData: нет auth_date — отклоняем")
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

    # Для тест-юзеров — парсим без проверки подписи (только в dev-окружении)
    if _IS_DEV:
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
                ).hexdigest()
                if hmac.compare_digest(expected, sig_q):
                    logger.info(f"Auth via signed URL token: uid={uid}")
                    return uid
                else:
                    logger.warning(f"Signed token HMAC mismatch for uid={uid}")
            else:
                logger.warning(f"Signed token expired for uid={uid_q}")
        except Exception as e:
            logger.warning(f"Token validation error: {e}")

    # Последний фоллбек: ?uid=XXX без подписи (только для TEST_USER_IDS в dev-окружении)
    if _IS_DEV and uid_q.isdigit():
        uid = int(uid_q)
        if uid in TEST_USER_IDS:
            logger.warning(f"DEV fallback: test user {uid} via unsigned ?uid param")
            return uid

    return None


# ── Хелперы для формирования данных ──────────────────────────

def _make_week_bar(week_done: int, total: int = 7, width: int = 7) -> str:
    filled = round(week_done / total * width) if total else 0
    return "●" * filled + "·" * (width - filled)


def _make_progress_bar(day: int, total: int = 77, width: int = 20) -> tuple[str, int]:
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
    week_end   = week_num * 7
    all_compl  = db.get_completed_days_set(user_id)
    week_done  = len({d for d in all_compl if week_start <= d <= day})

    # Статистика для шаблона weekly header
    _wt = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    _w_push = _w_sq = _w_abs = 0
    _t_push = _t_sq = _t_abs = 0
    _user_row_dict = dict(user_row) if user_row else {}
    for _d in all_compl:
        _tasks = db.get_completed_tasks(user_id, _d)
        if 0 in _tasks:
            try:
                _wo = get_workout(_user_row_dict, _d)
                _t_push += _wo["pushup"]["total"]
                _t_sq   += _wo["squat"]["total"]
                _t_abs  += _wo["abs"]["total"]
                if week_start <= _d <= week_end:
                    _w_push += _wo["pushup"]["total"]
                    _w_sq   += _wo["squat"]["total"]
                    _w_abs  += _wo["abs"]["total"]
            except Exception:
                pass
        if week_start <= _d <= week_end:
            for _t in _tasks:
                if _t in _wt:
                    _wt[_t] += 1
    _uc = db.get_task_completion_counts(user_id)
    week_header = ct.format_weekly_header(
        week_num,
        train=_wt[0], pushups=_w_push, abs=_w_abs, squats=_w_sq,
        water=_wt[1] * 2, pages=_wt[2] * 20, nojunk=_wt[3], noalc=_wt[4],
        total_pushups=_t_push, total_abs=_t_abs, total_squats=_t_sq,
        total_water=_uc.get(1, 0) * 2, total_pages=_uc.get(2, 0) * 20,
        total_nojunk=_uc.get(3, 0), total_noalc=_uc.get(4, 0),
    )

    # Группа
    group = db.get_group_stats() or {}

    # Ачивки — разблокирована если засчитанных дней >= порога,
    # либо если запись уже есть в БД (на случай ручного награждения)
    achievements = []
    for ach_id, threshold in ct.ACHIEVEMENT_ORDER:
        ach = ct.ACHIEVEMENTS[ach_id]
        is_unlocked = days_done >= threshold or db.has_achievement(user_id, ach_id)
        achievements.append({
            "id":        ach_id,
            "icon":      ach["icon"],
            "name":      ach["name"],
            "threshold": threshold,
            "unlocked":  is_unlocked,
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
    pct_label = "чуть меньше процента" if (day == 1 and pct_num == 0) else f"{pct_num}%"
    share_texts = []
    if day > 0:
        share_texts = [
            f"День {day}/77 🦥 Иду к {percentile} планеты. Сегодня — {n_done}/5 задач. Кто ещё в игре? t.me/shagov77_bot",
            f"{pct_label} пути пройдено. Без алкоголя, со спортом и книгами. Ленивец гордится. 🦥 t.me/shagov77_bot",
            f"Факт: только 6% людей читают 10 страниц в день. Я — один из них уже {day} дней подряд. t.me/shagov77_bot",
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
        db.toggle_task(uid, day, task_index)   # поддерживает и установку, и снятие галочки
        completed_set = db.get_completed_tasks(uid, day)
        # Синхронизируем трекер-сообщение в Telegram
        await _edit_tracker_keyboard(uid, day, completed_set)
        return web.json_response({"completed_tasks": sorted(completed_set), "day": day})
    except Exception as e:
        logger.exception(f"task error for {uid}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_mood_img(request: web.Request) -> web.Response:
    """Отдаёт картинку настроения: /img/happy | /img/norm | /img/sad"""
    name = request.match_info.get("name", "norm")
    paths = {"happy": HAPPY_IMG, "norm": NORM_IMG, "sad": SAD_IMG}
    img_path = paths.get(name, NORM_IMG)
    if img_path.exists():
        return web.Response(body=img_path.read_bytes(), content_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})
    return web.Response(status=404)


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


async def handle_debug(request: web.Request) -> web.Response:
    """GET /debug — диагностическая страница (только в dev-окружении)."""
    if not _IS_DEV:
        return web.Response(status=404)
    html = """<!DOCTYPE html>
<html lang="ru">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Debug</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
body{background:#0f0f13;color:#fff;font-family:monospace;padding:16px;font-size:12px}
h2{color:#e5a012;margin-bottom:12px}
.row{margin-bottom:8px;word-break:break-all}
.k{color:rgba(255,255,255,.45)}
.v{color:#4cd964}
.empty{color:#ff5c5c}
</style>
</head>
<body>
<h2>🦥 Mini App Debug</h2>
<div id="out"></div>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script>
const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const rows = [
  ['tg available',   tg ? 'YES' : 'NO'],
  ['tg.version',     tg?.version || '—'],
  ['tg.platform',    tg?.platform || '—'],
  ['tg.initData',    tg?.initData || 'EMPTY'],
  ['initData.len',   (tg?.initData||'').length],
  ['unsafe.user',    JSON.stringify(tg?.initDataUnsafe?.user) || '—'],
  ['location.href',  location.href],
  ['location.search',location.search || 'EMPTY'],
  ['location.hash',  location.hash || 'EMPTY'],
];
const out = document.getElementById('out');
rows.forEach(([k,v]) => {
  const d = document.createElement('div');
  d.className = 'row';
  const empty = !v || v === 'EMPTY' || v === '—' || v === '0';
  d.innerHTML = '<span class="k">' + k + ': </span><span class="' + (empty?'empty':'v') + '">' + v + '</span>';
  out.appendChild(d);
});
</script>
</body></html>"""
    return web.Response(text=html, content_type="text/html")


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
    app = web.Application(
        middlewares=[_security_middleware],
        client_max_size=512 * 1024,   # 512 КБ — максимальный размер тела запроса
    )
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
    app.router.add_get("/img/{name}",          handle_mood_img)
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
