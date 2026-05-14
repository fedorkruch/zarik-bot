"""
keyboards.py — inline-клавиатуры бота Зарик (77-дневный челлендж)
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


# 5 задач: 0=тренировка, 1=вода, 2=чтение, 3=питание, 4=алкоголь
TASK_LABELS = [
    ("💪", "Тренировка"),
    ("💧", "Вода · 2 литра / 8 стаканов"),
    ("📚", "Чтение · 10 страниц"),
    ("🥗", "Без фастфуда, чипсов и снеков"),
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

# Цифровая клавиатура для ввода количества повторений
DIGIT_ROWS = [
    ["1", "2", "3", "4", "5"],
    ["6", "7", "8", "9", "10"],
    ["12", "15", "20", "25", "30"],
    ["35", "40", "50", "60", "75"],
]


def tasks_keyboard(day: int, completed: set) -> InlineKeyboardMarkup:
    """
    Клавиатура с 5 кнопками задач + строка прогресса.
    Выполненные — ✅, невыполненные — ⬜
    """
    total = len(TASK_LABELS)
    buttons = []
    for i, (icon, label) in enumerate(TASK_LABELS):
        mark = "✅" if i in completed else "⬜"
        buttons.append([
            InlineKeyboardButton(
                text=f"{mark} {icon} {label}",
                callback_data=f"task:{day}:{i}"
            )
        ])

    # Прогресс-строка
    done = len(completed)
    progress = "▓" * done + "░" * (total - done)
    buttons.append([
        InlineKeyboardButton(
            text=f"{progress} {done}/{total}",
            callback_data="noop"
        )
    ])

    return InlineKeyboardMarkup(buttons)


def reps_keyboard(exercise_key: str) -> InlineKeyboardMarkup:
    """
    Клавиатура для выбора количества повторений при онбординге.
    exercise_key: 'pushup' | 'squat' | 'abs'
    """
    buttons = []
    for row in DIGIT_ROWS:
        buttons.append([
            InlineKeyboardButton(str(n), callback_data=f"reps:{exercise_key}:{n}")
            for n in row
        ])
    return InlineKeyboardMarkup(buttons)


def timezone_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора часового пояса"""
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"tz:{tz}")]
        for label, tz in TIMEZONES
    ]
    return InlineKeyboardMarkup(buttons)


def progress_keyboard() -> InlineKeyboardMarkup:
    """Кнопка просмотра прогресса"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Мой прогресс", callback_data="progress"),
    ]])


def all_done_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура после выполнения всех задач"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Мой прогресс", callback_data="progress"),
    ]])


# Главное меню (Reply-кнопки)
MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["📋 Мои задачи на сегодня"],
        ["📊 Мой прогресс", "❓ Помощь"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)
