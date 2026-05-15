"""
keyboards.py — клавиатуры бота Зарик (77-дневный челлендж)
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

# 5 задач: 0=тренировка, 1=вода, 2=чтение, 3=питание, 4=алкоголь
TASK_LABELS = [
    ("💪", "Тренировка"),
    ("💧", "Вода · 2 л / 8 стаканов"),
    ("📚", "Чтение · 10 страниц"),
    ("🥗", "Без фастфуда и снеков"),
    ("🚫", "День без алкоголя"),
]

# Часовые пояса СНГ и ближнего зарубежья
TIMEZONES = [
    ("UTC+2 · Калининград",      "Europe/Kaliningrad"),
    ("UTC+3 · Москва",           "Europe/Moscow"),
    ("UTC+3 · Минск / Киев",     "Europe/Minsk"),
    ("UTC+4 · Самара / Баку",    "Europe/Samara"),
    ("UTC+5 · Екатеринбург",     "Asia/Yekaterinburg"),
    ("UTC+5 · Ташкент / Астана", "Asia/Tashkent"),
    ("UTC+6 · Омск",             "Asia/Omsk"),
    ("UTC+7 · Красноярск / Нск", "Asia/Krasnoyarsk"),
    ("UTC+8 · Иркутск",          "Asia/Irkutsk"),
    ("UTC+9 · Якутск",           "Asia/Yakutsk"),
    ("UTC+10 · Владивосток",     "Asia/Vladivostok"),
    ("UTC+11 · Магадан",         "Asia/Magadan"),
    ("UTC+12 · Камчатка",        "Asia/Kamchatka"),
]

DIGIT_ROWS = [
    ["1", "2", "3", "4", "5"],
    ["6", "7", "8", "9", "10"],
    ["12", "15", "20", "25", "30"],
    ["35", "40", "50", "60", "75"],
]

# ── Вкладки (таб-бар) ────────────────────────────────────────

_TABS = [
    ("tasks",        "☀️", "Сегодня"),
    ("progress",     "📊", "Итоги"),
    ("week",         "📅", "Неделя"),
    ("achievements", "🏆", "Ачивки"),
]


def tab_bar(active: str) -> list[InlineKeyboardButton]:
    """Строка вкладок — последняя строка в каждом главном экране."""
    buttons = []
    for key, icon, label in _TABS:
        if key == active:
            text = f"· {icon} {label} ·"
        else:
            text = f"{icon} {label}"
        buttons.append(InlineKeyboardButton(text, callback_data=f"tab:{key}"))
    return buttons


def tab_only_keyboard(active: str) -> InlineKeyboardMarkup:
    """Клавиатура только из таб-бара (для экранов без задач)."""
    return InlineKeyboardMarkup([tab_bar(active)])


# ── Клавиатура задач ─────────────────────────────────────────

def tasks_keyboard(day: int, completed: set, active_tab: str = "tasks") -> InlineKeyboardMarkup:
    """
    Таб-бар сверху (всегда виден) + кнопки 5 задач + прогресс-строка.
    Выполненные — ✅, невыполненные — ⬜.
    """
    buttons = [tab_bar(active_tab)]  # таб-бар первым — всегда виден

    for i, (icon, label) in enumerate(TASK_LABELS):
        mark = "✅" if i in completed else "⬜"
        buttons.append([InlineKeyboardButton(
            text=f"{mark}  {icon}  {label}",
            callback_data=f"task:{day}:{i}"
        )])

    done = len(completed)
    progress = "●" * done + "·" * (5 - done)
    buttons.append([InlineKeyboardButton(
        text=f"{progress}  {done} / 5",
        callback_data="noop"
    )])

    return InlineKeyboardMarkup(buttons)


def all_done_keyboard(active_tab: str = "tasks") -> InlineKeyboardMarkup:
    """Клавиатура после выполнения всех задач — только прогресс + таб-бар."""
    buttons = [
        [InlineKeyboardButton("●●●●●  5 / 5  ✅", callback_data="noop")],
        tab_bar(active_tab),
    ]
    return InlineKeyboardMarkup(buttons)


# ── Онбординг ────────────────────────────────────────────────

def reps_keyboard(exercise_key: str) -> InlineKeyboardMarkup:
    buttons = []
    for row in DIGIT_ROWS:
        buttons.append([
            InlineKeyboardButton(str(n), callback_data=f"reps:{exercise_key}:{n}")
            for n in row
        ])
    return InlineKeyboardMarkup(buttons)


def timezone_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"tz:{tz}")]
        for label, tz in TIMEZONES
    ]
    return InlineKeyboardMarkup(buttons)


# ── Вспомогательные ───────────────────────────────────────────

def progress_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Итоги", callback_data="tab:progress"),
    ]])


# Главное меню (Reply-кнопки) — постоянная клавиатура внизу
MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Мои задачи на сегодня"],
        ["📊 Прогресс", "🏆 Ачивки"],
        ["❓ Помощь"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# Кнопка старта — до завершения онбординга
START_MENU = ReplyKeyboardMarkup(
    [["🦥 Начать"]],
    resize_keyboard=True,
    is_persistent=True,
)
