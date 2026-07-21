"""
run.py — точка входа бота ТЕО.
"""
import asyncio
import logging

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def main():
    from bot import build_app
    app = build_app()

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("🌿 Бот ТЕО запущен")
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
