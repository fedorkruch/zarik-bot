"""
claude_client.py — обёртка над Anthropic API для бота ТЕО.
Поддерживает tool use: save_user_name, save_goals, save_weekly_plan.
"""
import os
import logging
from anthropic import AsyncAnthropic
import database as db
from system_prompt import build_system_prompt

logger = logging.getLogger(__name__)

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY не задан")
        _client = AsyncAnthropic(api_key=api_key)
    return _client


# ── Инструменты которые Claude может вызывать ─────────────────────────────────

TOOLS = [
    {
        "name": "save_user_name",
        "description": (
            "Сохрани предпочтительное имя пользователя сразу как он его назвал. "
            "Вызывай один раз при знакомстве."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Имя так как хочет пользователь — Иван, Ваня, Настя и т.п.",
                }
            },
            "required": ["name"],
        },
    },
    {
        "name": "save_goals",
        "description": (
            "Сохрани финальные цели пользователя после того как разобрались с истинными желаниями. "
            "Вызывай только когда уверен что цели сформулированы точно (максимум 3 цели)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goals": {
                    "type": "array",
                    "description": "Список целей (1-3 штуки)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "area": {
                                "type": "string",
                                "description": "Сфера: карьера, здоровье, отношения, финансы, саморазвитие, другое",
                            },
                            "goal_text": {
                                "type": "string",
                                "description": "Как пользователь сам формулирует цель",
                            },
                            "true_goal": {
                                "type": "string",
                                "description": "Истинная цель за формулировкой — что человек на самом деле хочет почувствовать/получить",
                            },
                        },
                        "required": ["goal_text", "true_goal"],
                    },
                }
            },
            "required": ["goals"],
        },
    },
    {
        "name": "save_weekly_plan",
        "description": (
            "Сохрани план задач на первую неделю после того как обсудили и согласовали его с пользователем. "
            "Задачи должны быть конкретными (не 'заниматься спортом', а 'три раза выйти на прогулку 30 минут'). "
            "Не больше 3 задач суммарно в день."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "goal_index": {
                                "type": "integer",
                                "description": "Индекс цели из сохранённых (0, 1, 2)",
                            },
                            "task_text": {
                                "type": "string",
                                "description": "Конкретная задача",
                            },
                            "day_offset": {
                                "type": "integer",
                                "description": "День: 0=сегодня, 1=завтра, 2=послезавтра и т.д.",
                            },
                        },
                        "required": ["task_text", "day_offset"],
                    },
                }
            },
            "required": ["tasks"],
        },
    },
]


# ── Основная функция ───────────────────────────────────────────────────────────

async def chat(user_id: int, user_message: str) -> tuple[str, list[dict]]:
    """
    Отправляет сообщение пользователя в Claude, обрабатывает tool use.
    Возвращает (текст_ответа, список_вызванных_инструментов).
    """
    user = db.get_user(user_id)
    if not user:
        return "Что-то пошло не так. Попробуй /start.", []

    goals = db.get_goals(user_id)
    today_tasks = db.get_today_tasks(user_id)
    memory = db.get_memory(user_id)

    # История сообщений (последние 8 — экономим токены)
    recent = db.get_recent_messages(user_id, limit=8)
    message_count = len(recent)

    system = build_system_prompt(user, goals, today_tasks, memory, message_count=message_count)

    messages = [{"role": m["role"], "content": m["content"]} for m in recent]
    messages.append({"role": "user", "content": user_message})

    # Сохраняем сообщение пользователя
    db.save_message(user_id, "user", user_message)

    client = get_client()
    tool_calls_made: list[dict] = []

    # Первый вызов Claude (Haiku — в 12x дешевле Sonnet при том же качестве диалога)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=system,
        messages=messages,
        tools=TOOLS,
    )

    # Цикл обработки tool use
    while response.stop_reason == "tool_use":
        tool_results = []
        text_before_tools = ""

        for block in response.content:
            if block.type == "text" and block.text:
                text_before_tools += block.text
            elif block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input
                tool_calls_made.append({"name": tool_name, "input": tool_input})

                logger.info(f"[TOOL] user={user_id} tool={tool_name} input={tool_input}")

                result_text = _execute_tool(user_id, tool_name, tool_input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

        # Продолжаем разговор с результатами инструментов
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=system,
            messages=messages,
            tools=TOOLS,
        )

    # Извлекаем финальный текст
    final_text = ""
    for block in response.content:
        if hasattr(block, "text") and block.text:
            final_text += block.text

    if final_text:
        db.save_message(user_id, "assistant", final_text)

    return final_text, tool_calls_made


# ── Выполнение инструментов ────────────────────────────────────────────────────

def _execute_tool(user_id: int, name: str, input_data: dict) -> str:
    try:
        if name == "save_user_name":
            db.set_preferred_name(user_id, input_data["name"])
            return f"Имя «{input_data['name']}» сохранено."

        elif name == "save_goals":
            goals = input_data.get("goals", [])
            db.save_goals(user_id, goals)
            return f"Сохранено {len(goals)} цел(и)."

        elif name == "save_weekly_plan":
            tasks = input_data.get("tasks", [])
            db.save_tasks(user_id, tasks)
            return f"Сохранено {len(tasks)} задач на первую неделю."

        else:
            return f"Инструмент «{name}» не найден."

    except Exception as e:
        logger.error(f"[TOOL ERROR] user={user_id} tool={name}: {e}")
        return f"Ошибка при выполнении: {e}"
