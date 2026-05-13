"""
keyboards.py — inline-клавиатуры бота Зарик
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


TASK_LABELS = [
    ("💧", "Вода"),
    ("🏃", "Зарядка"),
    ("🚶", "Активность"),
    ("🌙", "Вечер"),
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


def tasks_keyboard(day: int, completed: set) -> InlineKeyboardMarkup:
    """
    Клавиатура с 4 кнопками задач.
    Выполненные — зелёные ✅, невыполненные — серые ☐
    """
    buttons = []
    for i, (icon, label) in enumerate(TASK_LABELS):
        if i in completed:
            text = f"✅ {icon} {label}"
        else:
            text = f"☐ {icon} {label}"
        buttons.append([
            InlineKeyboardButton(
                text=text,
                callback_data=f"task:{day}:{i}"
            )
        ])

    # Кнопка прогресса внизу
    done = len(completed)
    progress = "▓" * done + "░" * (4 - done)
    buttons.append([
        InlineKeyboardButton(
            text=f"{progress} {done}/4",
            callback_data="noop"
        )
    ])

    return InlineKeyboardMarkup(buttons)


def progress_keyboard() -> InlineKeyboardMarkup:
    """Кнопка для просмотра прогресса"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Мой прогресс", callback_data="progress"),
        InlineKeyboardButton("🗓 Программа", callback_data="program"),
    ]])


def all_done_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура после выполнения всех задач"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Мой прогресс", callback_data="progress"),
    ]])


def timezone_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора часового пояса"""
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"tz:{tz}")]
        for label, tz in TIMEZONES
    ]
    return InlineKeyboardMarkup(buttons)
