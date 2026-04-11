"""
Telegram-бот для учёта моргов и ритуальных услуг
Версия: 9.0 — Модульная архитектура (возврат к стабильной версии)
"""

import os
import sys
import logging
import asyncio
import argparse
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv

# ============================================================
# КОНФИГ
# ============================================================
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN не найден!"); sys.exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ============================================================
# ПОДКЛЮЧЕНИЕ РОУТЕРОВ
# ============================================================
sys.path.insert(0, os.path.dirname(__file__))

from handlers.morgue import router as morgue_router
dp.include_router(morgue_router)

from handlers.ritual import router as ritual_router
dp.include_router(ritual_router)

from handlers.stats import router as stats_router
dp.include_router(stats_router)

from handlers.users import router as users_router
dp.include_router(users_router)

# ============================================================
# КОНСТАНТЫ
# ============================================================
MORGUE_NAMES = {"morgue1": "Первомайская 13", "morgue2": "Мира 11"}

# ============================================================
# ПЛАНИРОВЩИК (ФОНОВЫЕ ЗАДАЧИ)
# ============================================================
from database.storage import UsersStorage, MorgueStorage

async def scheduler():
    """Фоновая задача: напоминания и авто-закрытие"""
    logger.info("🕒 Планировщик запущен")
    
    users_db = UsersStorage()
    morgue1_db = MorgueStorage("morgue1")
    morgue2_db = MorgueStorage("morgue2")
    DBS = {"morgue1": morgue1_db, "morgue2": morgue2_db}
    
    sent_reminders = {} # Чтобы не спамить

    while True:
        try:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            date_key = now.strftime("%Y-%m-%d")

            # --- 1. НАПОМИНАНИЯ (14:30, 15:00, 15:20) ---
            if current_time in ["14:30", "15:00", "15:20"]:
                reminder_key = f"rem_{current_time}"
                if reminder_key not in sent_reminders:
                    sent_reminders[reminder_key] = True
                    
                    # Находим всех менеджеров
                    all_users = users_db.get_all_users()
                    for uid_str, udata in all_users.items():
                        role = udata.get("role", "")
                        if role.startswith("manager_morg"):
                            mid = "morgue1" if "morg1" in role else "morgue2"
                            # Проверяем открытую смену
                            db = DBS[mid]
                            shift = db.get_active_shift()
                            
                            if shift and not shift.get("closed"):
                                await bot.send_message(
                                    int(uid_str), 
                                    f"⚠️ <b>Напоминание!</b>\nДо закрытия смены осталось немного.\nПроверь оплаты по телам!"
                                )

            # --- 2. АВТО-ЗАКРЫТИЕ (15:30) ---
            if now.hour == 15 and now.minute == 30:
                close_key = f"close_{date_key}"
                if close_key not in sent_reminders:
                    sent_reminders[close_key] = True
                    
                    for mid in ["morgue1", "morgue2"]:
                        db = DBS[mid]
                        shift = db.get_active_shift()
                        if shift:
                            db.close_shift(shift["shift_id"], 0, "Автозакрытие (15:30)")
                            logger.info(f"🔒 Смена {mid} закрыта автоматически.")
                            
                            # Уведомляем менеджера
                            all_users = users_db.get_all_users()
                            for uid_str, udata in all_users.items():
                                role = udata.get("role", "")
                                if ("morg1" in role and mid == "morgue1") or ("morg2" in role and mid == "morgue2"):
                                    await bot.send_message(int(uid_str), f"🔒 Смена в {MORGUE_NAMES[mid]} закрыта автоматически (15:30).")

            # Сброс старых ключей (раз в сутки)
            if now.hour == 0 and now.minute == 1:
                sent_reminders.clear()

            await asyncio.sleep(45) # Проверка каждые 45 секунд
        except Exception as e:
            logger.error(f"Ошибка планировщика: {e}")
            await asyncio.sleep(60)

# ============================================================
# ЗАПУСК
# ============================================================

async def on_startup():
    url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
    if not url and host: url = f"https://{host}"
    if not url:
        logger.warning("Webhook URL не установлен"); return

    webhook_url = f"{url}{WEBHOOK_PATH}"
    secret = WEBHOOK_SECRET if WEBHOOK_SECRET else None

    max_retries = 5
    for attempt in range(max_retries):
        try:
            await bot.set_webhook(webhook_url, secret_token=secret)
            logger.info(f"✅ Webhook установлен: {webhook_url}"); return
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                logger.warning(f"Webhook не установлен ({attempt+1}/{max_retries}): {e}")
                await asyncio.sleep(wait)
            else: logger.error(f"Не удалось установить webhook: {e}")


def create_app() -> web.Application:
    app = web.Application()
    async def health_handler(request): return web.Response(text="OK")
    app.router.add_get("/health", health_handler)
    app.router.add_get("/", health_handler)
    SimpleRequestHandler(dp, bot, secret_token=WEBHOOK_SECRET if WEBHOOK_SECRET else None).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    return app


async def polling_main():
    try:
        await bot.delete_webhook()
        logger.info("Webhook удалён")
    except Exception as e: logger.warning(f"Ошибка удаления webhook: {e}")
    
    # Запускаем планировщик в фоне
    asyncio.create_task(scheduler())
    
    await dp.start_polling(bot, skip_updates=True)


def main():
    parser = argparse.ArgumentParser(description="MorgueBot")
    parser.add_argument("--polling", action="store_true", help="Режим polling")
    args = parser.parse_args()
    dp.startup.register(on_startup)

    if args.polling:
        logger.info("🚀 POLLING режим...")
        asyncio.run(polling_main())
    else:
        logger.info("🚀 WEBHOOK режим...")
        app = create_app()
        port = int(os.getenv("PORT", 10000))
        web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
