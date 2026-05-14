"""
run_bots.py — запускает лид-бот и программный бот в отдельных потоках.
Каждый бот получает собственный asyncio event loop — это надёжнее чем shared loop.
"""
import asyncio
import logging
import threading

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def run_bot(build_func, name: str):
    """Запускает бота в отдельном потоке с собственным event loop."""
    async def _run():
        app = build_func()
        async with app:
            await app.updater.start_polling(drop_pending_updates=True)
            logger.info(f"🦥 {name} запущен")
            await asyncio.Event().wait()  # ждём вечно — Railway сам остановит контейнер

    asyncio.run(_run())


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
        args=(build_program, "Программный бот @myeasystartbot"),
        daemon=True,
        name="program-bot",
    )

    lead_thread.start()
    prog_thread.start()

    logger.info("🦥 Оба потока запущены")

    lead_thread.join()
    prog_thread.join()
