"""
Telegram-бот для учёта моргов и ритуальных услуг
Версия: 9.0 — Модульная архитектура (роутеры)
"""

import os
import sys
import logging
import asyncio
import argparse

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv

# ============================================================
# КОНФИГ
# ============================================================
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN не найден!")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ============================================================
# ПОДКЛЮЧЕНИЕ РОУТЕРОВ
# ============================================================
sys.path.insert(0, os.path.dirname(__file__))

# БЛОК 1: МОРГ (критичный)
from handlers.morgue import router as morgue_router
dp.include_router(morgue_router)

# БЛОК 2: РИТУАЛКА (позже)
# from handlers.ritual import router as ritual_router
# dp.include_router(ritual_router)

# БЛОК 3: КАРТОЧКИ (позже)
# from handlers.cards import router as cards_router
# dp.include_router(cards_router)

# БЛОК 4: СТАТИСТИКА (позже)
# from handlers.stats import router as stats_router
# dp.include_router(stats_router)

# ============================================================
# ЗАПУСК
# ============================================================

async def on_startup():
    """Установка webhook с retry"""
    url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
    if not url and host:
        url = f"https://{host}"
    if not url:
        logger.warning("Webhook URL не установлен")
        return

    webhook_url = f"{url}{WEBHOOK_PATH}"
    secret = WEBHOOK_SECRET if WEBHOOK_SECRET else None

    max_retries = 5
    for attempt in range(max_retries):
        try:
            await bot.set_webhook(webhook_url, secret_token=secret)
            logger.info(f"✅ Webhook установлен: {webhook_url}")
            return
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                logger.warning(f"Webhook не установлен (попытка {attempt+1}/{max_retries}): {e}")
                logger.info(f"Повтор через {wait} сек...")
                await asyncio.sleep(wait)
            else:
                logger.error(f"Не удалось установить webhook: {e}")


def create_app() -> web.Application:
    app = web.Application()

    async def health_handler(request):
        return web.Response(text="OK")

    app.router.add_get("/health", health_handler)
    app.router.add_get("/", health_handler)

    SimpleRequestHandler(
        dp, bot,
        secret_token=WEBHOOK_SECRET if WEBHOOK_SECRET else None
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)
    return app


def main():
    parser = argparse.ArgumentParser(description="MorgueBot")
    parser.add_argument("--polling", action="store_true", help="Режим polling")
    args = parser.parse_args()

    dp.startup.register(on_startup)

    if args.polling:
        logger.info("🚀 POLLING режим...")
        asyncio.run(dp.start_polling(bot, skip_updates=True))
    else:
        logger.info("🚀 WEBHOOK режим...")
        app = create_app()
        port = int(os.getenv("PORT", 10000))
        web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
