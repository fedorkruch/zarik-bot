"""
workout.py — персональная программа тренировок для Зарика (77-дневный челлендж)

Логика:
  - Пользователь указывает стартовое количество повторений на онбординге (S)
  - Каждый день прибавляется +1 повторение за круг (рост с дня 1 по день 56)
  - С дня 57 количество повторений фиксируется на уровне дня 56: S + 55
  - Никакого верхнего потолка нет — у продвинутого участника будет больше повторений
  - Количество кругов зависит от фазы:
      Фаза 1 (дни 1–14):  1 круг
      Фаза 2 (дни 15–56): 2 круга
      Фаза 3 (дни 57–77): 3 круга
"""

GROWTH_STOP_DAY = 56  # С дня 57 повторения фиксируются на уровне дня 56


def get_circles(day: int) -> int:
    """Количество кругов по фазам — одинаково для всех пользователей."""
    if day <= 14:
        return 1
    elif day <= 56:
        return 2
    else:
        return 3


def get_reps_per_circle(start: int, day: int) -> int:
    """
    Повторений за круг для данного пользователя в данный день.

    Рост идёт +1 в день с дня 1 по день 56 включительно.
    С дня 57 значение замерзает на уровне дня 56 (start + 55).
    Верхнего потолка нет.

    Примеры:
      база 10, день 56: 10 + 55 = 65
      база 10, день 57: 65 (заморожено)
      база 100, день 56: 100 + 55 = 155
      база 100, день 57: 155 (заморожено)
    """
    effective_day = min(day, GROWTH_STOP_DAY)
    return start + (effective_day - 1)


def get_workout(user: dict, day: int) -> dict:
    """
    Возвращает полное описание тренировки для пользователя в день D.

    user — строка из БД (или dict) с ключами:
        pushup_start: int  — стартовые отжимания
        squat_start:  int  — стартовые приседания
        abs_start:    int  — стартовый пресс

    Возвращает dict:
        description: str  — готовый текст для чеклиста (3 строки)
        circles:     int  — количество кругов
        pushup:      dict — reps, total
        squat:       dict — reps, total
        abs:         dict — reps, total
    """
    circles = get_circles(day)
    rounds_word = _circles_word(circles)

    pushup_reps = get_reps_per_circle(user['pushup_start'], day)
    squat_reps  = get_reps_per_circle(user['squat_start'],  day)
    abs_reps    = get_reps_per_circle(user['abs_start'],    day)

    description = (
        f"   Отжимания: {pushup_reps} × {circles} {rounds_word} = {pushup_reps * circles}\n"
        f"   Приседания: {squat_reps} × {circles} {rounds_word} = {squat_reps * circles}\n"
        f"   Пресс: {abs_reps} × {circles} {rounds_word} = {abs_reps * circles}"
    )

    return {
        "description": description,
        "circles": circles,
        "pushup": {"reps": pushup_reps, "total": pushup_reps * circles},
        "squat":  {"reps": squat_reps,  "total": squat_reps  * circles},
        "abs":    {"reps": abs_reps,    "total": abs_reps    * circles},
    }


def get_workout_summary(user: dict, day: int) -> str:
    """
    Короткий текст тренировки для отображения в чеклисте.
    Пример:
        Отжимания: 21 × 2 круга = 42
        Приседания: 25 × 2 круга = 50
        Пресс: 18 × 2 круга = 36
    """
    return get_workout(user, day)["description"]


def get_total_reps(user: dict, day: int) -> dict:
    """Итоговое количество повторений по каждому упражнению за день."""
    w = get_workout(user, day)
    return {
        "pushup": w["pushup"]["total"],
        "squat":  w["squat"]["total"],
        "abs":    w["abs"]["total"],
    }


# ── helpers ──────────────────────────────────────────────────────────────────

def _circles_word(n: int) -> str:
    """Склонение слова «круг» для 1, 2, 3."""
    if n == 1:
        return "круг"
    elif n in (2, 3, 4):
        return "круга"
    else:
        return "кругов"
