"""
run_bots.py — запускает лид-бот, программный бот, Mini App сервер и бот ТЕО.
Каждый бот получает собственный asyncio event loop — это надёжнее чем shared loop.
ТЕО запускается как дочерний процесс (изоляция зависимостей и автоперезапуск).
"""
import asyncio
import logging
import os
import subprocess
import sys
import threading
import time

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def run_bot(build_func, name: str):
    """Запускает бота в отдельном потоке с собственным event loop."""
    async def _run():
        try:
            app = build_func()
            async with app:
                await app.start()                                           # ← запускает диспетчер
                await app.updater.start_polling(drop_pending_updates=True)  # ← получаем апдейты
                logger.info(f"🦥 {name} запущен")
                await asyncio.Event().wait()                                # ← держим поток живым
        except Exception:
            logger.exception(f"❌ {name} упал с ошибкой")

    asyncio.run(_run())


def run_webapp():
    """Запускает aiohttp Mini App сервер в отдельном потоке."""
    async def _run():
        try:
            from webapp_server import run_server
            await run_server()
        except Exception:
            logger.exception("❌ Mini App сервер упал с ошибкой")

    asyncio.run(_run())


def run_teo():
    """
    Запускает бот ТЕО как отдельный дочерний процесс с автоперезапуском.
    Отдельный процесс = изоляция зависимостей, нет конфликтов модулей.
    """
    sasha_dir = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sasha_bot")
    )
    while True:
        try:
            proc = subprocess.Popen(
                [sys.executable, "run.py"],
                cwd=sasha_dir,
            )
            logger.info(f"🌿 Бот ТЕО запущен (pid={proc.pid})")
            proc.wait()
            logger.warning("⚠️ Бот ТЕО завершился, перезапуск через 3 сек...")
        except Exception:
            logger.exception("❌ Бот ТЕО не удалось запустить")
        time.sleep(3)


if __name__ == "__main__":
    from lead_bot import build_app as build_lead
    from program_bot import build_app as build_program

    lead_thread = threading.Thread(
        target=run_bot,
        args=(build_lead, "Лид-бот @Shagov77_bot"),
        daemon=True,
        name="lead-bot",
    )
    prog_thread = threading.Thread(
        target=run_bot,
        args=(build_program, "Программный бот @Zarik_Lazy_Bot"),
        daemon=True,
        name="program-bot",
    )
    webapp_thread = threading.Thread(
        target=run_webapp,
        daemon=True,
        name="webapp-server",
    )
    teo_thread = threading.Thread(
        target=run_teo,
        daemon=True,
        name="teo-bot",
    )

    lead_thread.start()
    prog_thread.start()
    webapp_thread.start()
    teo_thread.start()

    logger.info("🦥 Боты Зарика, Mini App сервер и бот ТЕО запущены")

    lead_thread.join()
    prog_thread.join()
    webapp_thread.join()
    teo_thread.join()
