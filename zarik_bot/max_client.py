"""
max_client.py — асинхронный клиент для Bot API Мессенджера MAX.
Документация: https://dev.max.ru/docs-api

Авторизация: заголовок  Authorization: <token>
Базовый URL:  https://platform-api.max.ru
"""
import logging
from pathlib import Path
from typing import Any

import aiohttp

BASE_URL = "https://platform-api.max.ru"
logger = logging.getLogger(__name__)


def _btn_callback(text: str, payload: str) -> dict:
    """Кнопка, отправляющая callback-событие боту."""
    return {"type": "callback", "text": text, "payload": payload}


def _btn_link(text: str, url: str) -> dict:
    """Кнопка-ссылка, открывающая URL."""
    return {"type": "link", "text": text, "url": url}


def _btn_msg(text: str, msg: str) -> dict:
    """Кнопка, отправляющая текстовое сообщение боту."""
    return {"type": "message", "text": text, "payload": msg}


class MaxClient:
    """
    Тонкая async-обёртка над REST API MAX.

    Использование:
        client = MaxClient(token)
        await client.send_message(user_id, "Привет!", buttons=[[_btn_callback("Да", "yes")]])
        await client.close()
    """

    def __init__(self, token: str):
        self.token = token
        self._session: aiohttp.ClientSession | None = None

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": self.token,
            "Content-Type": "application/json",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json: Any = None,
    ) -> dict:
        session = await self._get_session()
        url = f"{BASE_URL}{path}"
        try:
            async with session.request(
                method, url,
                headers=self._headers,
                params=params,
                json=json,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    logger.error(f"MAX API {method} {path} → {resp.status}: {data}")
                return data or {}
        except Exception as e:
            logger.exception(f"MAX API request failed {method} {path}: {e}")
            return {}

    # ── Отправка / редактирование сообщений ──────────────────

    async def send_message(
        self,
        user_id: int,
        text: str,
        buttons: list[list[dict]] | None = None,
        fmt: str = "markdown",
    ) -> dict:
        """Отправляет текстовое сообщение пользователю."""
        body: dict[str, Any] = {"text": text, "format": fmt}
        if buttons:
            body["attachments"] = [{
                "type": "inline_keyboard",
                "payload": {"buttons": buttons},
            }]
        return await self._request("POST", "/messages", params={"user_id": user_id}, json=body)

    async def edit_message(
        self,
        message_id: str,
        text: str,
        buttons: list[list[dict]] | None = None,
        fmt: str = "markdown",
    ) -> dict:
        """Редактирует ранее отправленное сообщение."""
        body: dict[str, Any] = {"text": text, "format": fmt}
        if buttons:
            body["attachments"] = [{
                "type": "inline_keyboard",
                "payload": {"buttons": buttons},
            }]
        return await self._request("PUT", "/messages", params={"message_id": message_id}, json=body)

    async def send_photo(
        self,
        user_id: int,
        photo_path: Path,
        caption: str | None = None,
        buttons: list[list[dict]] | None = None,
        fmt: str = "markdown",
    ) -> dict:
        """Загружает изображение и отправляет с подписью."""
        token = await self._upload_image(photo_path)
        if not token:
            # Фолбэк: просто текст
            return await self.send_message(user_id, caption or "—", buttons=buttons, fmt=fmt)

        attachments: list[dict] = [{"type": "image", "payload": {"token": token}}]
        if buttons:
            attachments.append({
                "type": "inline_keyboard",
                "payload": {"buttons": buttons},
            })
        body: dict[str, Any] = {"attachments": attachments, "format": fmt}
        if caption:
            body["text"] = caption
        return await self._request("POST", "/messages", params={"user_id": user_id}, json=body)

    async def answer_callback(self, callback_id: str, text: str | None = None) -> dict:
        """Отвечает на callback (убирает индикатор загрузки у кнопки)."""
        if not callback_id:
            return {}
        # callback_id передаётся как query-param, а не в теле
        params: dict[str, Any] = {"callback_id": callback_id}
        body: dict[str, Any] = {}
        if text:
            body["notification"] = text
        return await self._request("POST", "/answers", params=params, json=body or None)

    # ── Загрузка файлов ───────────────────────────────────────

    async def _upload_image(self, photo_path: Path) -> str:
        """
        Двухшаговая загрузка изображения:
        1) POST /uploads?type=image  → {url}
        2) Multipart POST на url      → {token}
        Возвращает token или '' при ошибке.
        """
        try:
            resp = await self._request("POST", "/uploads", params={"type": "image"})
            upload_url: str = resp.get("url", "")
            if not upload_url:
                logger.error("MAX upload: no URL in response")
                return ""

            session = await self._get_session()
            content_type = "image/png" if photo_path.suffix.lower() == ".png" else "image/jpeg"
            data = aiohttp.FormData()
            data.add_field(
                "file",
                open(photo_path, "rb"),
                filename=photo_path.name,
                content_type=content_type,
            )
            async with session.post(
                upload_url,
                data=data,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as up_resp:
                result = await up_resp.json(content_type=None)
                # Ответ: {"photos": {"<hash>": {"token": "..."}}}
                photos = result.get("photos", {})
                token: str = ""
                if photos:
                    first_key = next(iter(photos))
                    token = photos[first_key].get("token", "")
                if not token:
                    # Фолбэк на случай плоской структуры
                    token = result.get("token", "")
                if not token:
                    logger.error(f"MAX upload: no token in response: {result}")
                return token
        except Exception as e:
            logger.exception(f"MAX image upload failed: {e}")
            return ""

    # ── Вебхук / служебные ───────────────────────────────────

    async def setup_webhook(
        self,
        url: str,
        update_types: list[str] | None = None,
    ) -> dict:
        """
        Регистрирует вебхук.
        update_types по умолчанию: все события.
        """
        body: dict[str, Any] = {"url": url}
        if update_types:
            body["update_types"] = update_types
        result = await self._request("POST", "/subscriptions", json=body)
        logger.info(f"MAX webhook setup → {result}")
        return result

    async def get_me(self) -> dict:
        return await self._request("GET", "/me")

    async def get_chat_member(self, chat_id: int, user_id: int) -> dict | None:
        """
        Проверяет членство пользователя в чате/канале.
        GET /chats/{chatId}/members?user_ids={userId}
        Возвращает первый ChatMember или None если пользователь не найден.
        Бот должен быть участником/администратором канала.
        """
        result = await self._request(
            "GET", f"/chats/{chat_id}/members",
            params={"user_ids": str(user_id)},
        )
        logger.info(f"get_chat_member chat={chat_id} user={user_id} → {result}")
        members = result.get("members", [])
        return members[0] if members else None

    async def get_chats(self) -> dict:
        """GET /chats — список чатов, в которых состоит бот."""
        return await self._request("GET", "/chats")
