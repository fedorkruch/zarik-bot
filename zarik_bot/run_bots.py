"""
run_bots.py — запускает лид-бот и программный бот одновременно через asyncio.
Используется как точка входа на Railway (один сервис, два процесса polling).
"""
import asyncio
import logging
import signal

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def main():
    from lead_bot import build_app as build_lead
    from program_bot import build_app as build_program

    lead_app = build_lead()
    prog_app = build_program()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    async with lead_app, prog_app:
        await lead_app.updater.start_polling(drop_pending_updates=True)
        await prog_app.updater.start_polling(drop_pending_updates=True)

        logger.info("🦥 Лид-бот и программный бот запущены")
        await stop_event.wait()

        await lead_app.updater.stop()
        await prog_app.updater.stop()

    logger.info("Боты остановлены")


if __name__ == "__main__":
    asyncio.run(main())
