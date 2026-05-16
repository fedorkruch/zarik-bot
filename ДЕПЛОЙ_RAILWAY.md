# Деплой бота Зарик на Railway

## Что уже сделано
- Токены перенесены в переменные окружения (bot.py, database.py)
- Созданы файлы: Procfile, railway.toml, requirements.txt, .gitignore
- База данных будет храниться на Railway Volume (постоянно)

---

## Шаг 1 — Git (в твоём терминале)

```bash
cd ~/Documents/Claude/Projects/Бот\ наставник\ маленьких\ шагов

git init
git branch -m main
git add requirements.txt Procfile railway.toml .env.example .gitignore zarik_bot/
git commit -m "🦥 Zarik bot v1.0"
```

---

## Шаг 2 — GitHub

1. Зайди на https://github.com/new
2. Создай приватный репозиторий — например `zarik-bot`
3. Выполни в терминале:

```bash
git remote add origin https://github.com/ВАШ_НИК/zarik-bot.git
git push -u origin main
```

---

## Шаг 3 — Railway

1. Зайди на https://railway.app → войди через GitHub
2. **New Project → Deploy from GitHub repo** → выбери `zarik-bot`
3. Railway автоматически найдёт `railway.toml` и `Procfile`

### Добавь переменные окружения (Variables):
| Ключ | Значение |
|------|----------|
| `BOT_TOKEN` | `8621043688:AAHZcG65-vR7nO996S3hkEUfl1e8gu-T0Z0` |
| `PROVIDER_TOKEN` | `390540012:LIVE:95406` |
| `PARTICIPATION_FEE_KOPECKS` | `5000` (тест 50₽, потом 499000) |
| `STAKE_MIN_RUB` | `10` (тест, потом 500) |
| `DATA_DIR` | `/data` |

### Добавь Volume (постоянное хранилище для БД):
1. В проекте → **+ New** → **Volume**
2. Mount path: `/data`
3. Готово — база данных будет сохраняться между перезапусками

---

## Шаг 4 — Запуск

После добавления переменных Railway автоматически задеплоит бота.

Посмотреть логи: вкладка **Deployments → View Logs**

Должна появиться строка:
```
🦥 Зарик запущен!
```

---

## Локальный запуск (для разработки)

Создай файл `.env` в папке `Бот наставник маленьких шагов/`:
```
BOT_TOKEN=8621043688:AAHZcG65-vR7nO996S3hkEUfl1e8gu-T0Z0
PROVIDER_TOKEN=390540012:LIVE:95406
PARTICIPATION_FEE_KOPECKS=5000
STAKE_MIN_RUB=10
```

Запуск:
```bash
cd zarik_bot
export $(cat ../.env | xargs) && python3 bot.py
```

Или проще — задай переменные напрямую в терминале перед запуском:
```bash
export BOT_TOKEN="8621043688:AAHZcG65-vR7nO996S3hkEUfl1e8gu-T0Z0"
export PROVIDER_TOKEN="390540012:LIVE:95406"
export PARTICIPATION_FEE_KOPECKS=5000
export STAKE_MIN_RUB=10
cd zarik_bot && python3 bot.py
```

---

## После деплоя — поменять на боевые значения

В Railway Variables:
- `PARTICIPATION_FEE_KOPECKS` → `499000`
- `STAKE_MIN_RUB` → `500`
