"""
Устанавливает zarik.png как аватар Telegram-бота.
Использует только встроенные библиотеки Python — ничего устанавливать не нужно.
Запуск: python3 set_avatar.py YOUR_BOT_TOKEN
"""
import sys
import os
import urllib.request
import uuid
import json
import ssl

# Фикс SSL на macOS
ssl._create_default_https_context = ssl._create_unverified_context

def set_bot_avatar(token: str, photo_path: str):
    url = f"https://api.telegram.org/bot{token}/setMyPhoto"

    print(f"📂 Загружаю файл: {photo_path}")

    with open(photo_path, "rb") as f:
        photo_data = f.read()

    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="zarik.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                print("✅ Аватар успешно установлен! Зарик теперь смотрит из всех чатов.")
            else:
                print(f"❌ Ошибка Telegram: {result.get('description')}")
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()
        print(f"❌ HTTP {e.code}: {body_err}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python3 set_avatar.py YOUR_BOT_TOKEN")
        sys.exit(1)

    token = sys.argv[1]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    photo_path = os.path.join(script_dir, "zarik.png")

    if not os.path.exists(photo_path):
        print(f"❌ Файл не найден: {photo_path}")
        sys.exit(1)

    set_bot_avatar(token, photo_path)
