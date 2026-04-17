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

# БЛОК 4: ПОЛЬЗОВАТЕЛИ (отключено, правим users.json вручную)
# from handlers.users import router as users_router
# dp.include_router(users_router)

# ============================================================
# КОНСТАНТЫ
# ============================================================
MORGUE_NAMES = {"morgue1": "Первомайская 13", "morgue2": "Мира 11"}

# ============================================================
# ПЛАНИРОВЩИК (ФОНОВЫЕ ЗАДАЧИ)
# ============================================================
from database.storage import UsersStorage, MorgueStorage
from database.archive import archive_weekly, archive_monthly, archive_quarterly, is_quarter_end, get_week_number

async def scheduler():
    """Фоновая задача: напоминания, авто-закрытие и архивация"""
    logger.info("🕒 Планировщик запущен")
    
    users_db = UsersStorage()
    morgue1_db = MorgueStorage("morgue1")
    morgue2_db = MorgueStorage("morgue2")
    DBS = {"morgue1": morgue1_db, "morgue2": morgue2_db}
    
    sent_reminders = {} # Чтобы не спамить
    weekly_archived = set()  # Архивированные недели
    monthly_archived = set() # Архивированные месяцы
    quarterly_archived = set() # Архивированные кварталы

    while True:
        try:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            date_key = now.strftime("%Y-%m-%d")
            week_key = f"{now.year}-W{get_week_number(now):02d}"
            month_key = f"{now.year}-{now.month:02d}"
            quarter_key = f"{now.year}-Q{(now.month - 1) // 3 + 1}"

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

            # --- 3. НЕДЕЛЬНАЯ АРХИВАЦИЯ (Воскресенье 23:00) ---
            if now.weekday() == 6 and now.hour == 23 and now.minute == 0:  # Воскресенье
                if week_key not in weekly_archived:
                    weekly_archived.add(week_key)
                    logger.info(f"📦 Начинаю недельную архивацию {week_key}")
                    try:
                        r1 = archive_weekly("morgue1")
                        r2 = archive_weekly("morgue2")
                        if r1 and r2:
                            logger.info(f"✅ Недельный архив {week_key} создан")
                        else:
                            logger.warning(f"⚠️ Недельный архив {week_key} частично выполнен")
                    except Exception as e:
                        logger.error(f"❌ Ошибка недельной архивации: {e}")

            # --- 4. КОНТРОЛЬ НЕДЕЛЬНОГО БЭКАПА (Воскресенье 23:50) ---
            if now.weekday() == 6 and now.hour == 23 and now.minute == 50:
                if week_key not in weekly_archived:
                    logger.warning(f"⚠️ Недельный бэкап {week_key} не создан, пропускаем")

            # --- 5. МЕСЯЧНАЯ АРХИВАЦИЯ (1-е число 00:10) ---
            if now.day == 1 and now.hour == 0 and now.minute == 10:
                if month_key not in monthly_archived:
                    monthly_archived.add(month_key)
                    logger.info(f"📦 Начинаю месячную архивацию {month_key}")
                    try:
                        r1 = archive_monthly("morgue1")
                        r2 = archive_monthly("morgue2")
                        if r1 and r2:
                            logger.info(f"✅ Месячный архив {month_key} создан")
                        else:
                            logger.warning(f"⚠️ Месячный архив {month_key} частично выполнен")
                    except Exception as e:
                        logger.error(f"❌ Ошибка месячной архивации: {e}")

            # --- 6. КВАРТАЛЬНАЯ АРХИВАЦИЯ (Конец квартала 00:10) ---
            if is_quarter_end(now) and now.hour == 0 and now.minute == 10:
                if quarter_key not in quarterly_archived:
                    quarterly_archived.add(quarter_key)
                    logger.info(f"📦 Начинаю квартальную архивацию {quarter_key}")
                    try:
                        r1 = archive_quarterly("morgue1")
                        r2 = archive_quarterly("morgue2")
                        if r1 and r2:
                            logger.info(f"✅ Квартальный архив {quarter_key} создан")
                        else:
                            logger.warning(f"⚠️ Квартальный архив {quarter_key} частично выполнен")
                    except Exception as e:
                        logger.error(f"❌ Ошибка квартальной архивации: {e}")

            # Сброс старых ключей (раз в сутки)
            if now.hour == 0 and now.minute == 1:
                sent_reminders.clear()

            await asyncio.sleep(45) # Проверка каждые 45 секунд
        except Exception as e:
            logger.error(f"Ошибка планировщика: {e}")
            await asyncio.sleep(60)

# ============================================================
# ТЕСТ РОЛЕЙ (ТОЛЬКО ДЛЯ АДМИНА)
# ============================================================
@dp.message(F.text.startswith("/role"))
async def cmd_test_role(message: types.Message, state: FSMContext):
    # Работает только для твоего ID
    if message.from_user.id != 747600306:
        return 

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /role agent_morg1 (или admin, manager_morg1, manager_morg2, agent_morg2)")
        return

    new_role = parts[1]
    valid_roles = ["admin", "manager_morg1", "manager_morg2", "agent_morg1", "agent_morg2"]
    if new_role not in valid_roles:
        await message.answer(f"Доступные роли: {', '.join(valid_roles)}")
        return

    from database.storage import set_test_role
    set_test_role(message.from_user.id, new_role)
    await message.answer(f"✅ Твоя тестовая роль теперь: {new_role}. Нажми /start, чтобы обновить меню.")

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
