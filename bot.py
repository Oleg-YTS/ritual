"""
Telegram-бот для учёта морга и ритуальных услуг
Версия: 3.0 — Постоянные кнопки, визуальный расчёт
"""

import os
import logging
import base64
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from dotenv import load_dotenv
from github import Github, GithubException

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN не найден в .env файле!")
    exit(1)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Инициализация GitHub (опционально)
github_client = None
repo = None
if GITHUB_TOKEN and REPO_NAME:
    try:
        github_client = Github(GITHUB_TOKEN)
        repo = github_client.get_repo(REPO_NAME)
        logger.info(f"Подключено к репозиторию: {REPO_NAME}")
    except Exception as e:
        logger.error(f"Ошибка подключения к GitHub: {e}")
        repo = None

# ==========================================
# FSM СОСТОЯНИЯ
# ==========================================

class MorgShift(StatesGroup):
    waiting_for_surname = State()
    waiting_for_type = State()
    waiting_for_source = State()
    # Закрытие смены
    closing_mark_payment = State()  # Выбор тела для отметки
    closing_organization = State()  # Ввод организации

class RitualOrder(StatesGroup):
    waiting_for_customer = State()
    waiting_for_phone = State()
    waiting_for_deceased_address = State()
    waiting_for_coffin = State()
    waiting_for_temple = State()
    waiting_for_cemetery = State()
    waiting_for_agent_salary = State()

# ==========================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ==========================================

# Текущая смена
current_shift = {
    "date": None,
    "bodies": []  # [{"surname": "", "type": "", "source": "", "paid": None, "organization": ""}]
}

# Последний маршрут для водителя
last_driver_route = None

# Ритуальный заказ
current_order = {}

# ==========================================
# ФУНКЦИИ РАБОТЫ С GITHUB
# ==========================================

def upload_to_github(path, content, message="Автосохранение"):
    if repo is None:
        save_local_fallback(path, content)
        return False
    try:
        try:
            file = repo.get_contents(path)
            repo.update_file(path=file.path, message=message, content=content, sha=file.sha, branch="main")
        except GithubException:
            repo.create_file(path=path, message=message, content=content, branch="main")
        return True
    except Exception as e:
        logger.error(f"Ошибка GitHub: {e}")
        save_local_fallback(path, content)
        return False


def append_csv_github(path, row, headers=None):
    if repo is None:
        save_local_fallback(path, row)
        return False
    try:
        existing_content = ""
        try:
            file = repo.get_contents(path)
            content_decoded = base64.b64decode(file.content).decode('utf-8')
            existing_content = content_decoded
        except GithubException:
            if headers:
                existing_content = ",".join(headers) + "\n"
        
        new_content = existing_content + row + "\n"
        
        try:
            file = repo.get_contents(path)
            repo.update_file(path=file.path, message=f"Добавлена строка в {path}", content=new_content, sha=file.sha, branch="main")
        except GithubException:
            repo.create_file(path=path, message=f"Создан {path}", content=new_content, branch="main")
        return True
    except Exception as e:
        logger.error(f"Ошибка CSV: {e}")
        save_local_fallback(path, row)
        return False


def save_local_fallback(path, content):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(content + "\n")
        logger.info(f"Сохранено локально: {path}")
    except Exception as e:
        logger.error(f"Ошибка локального сохранения: {e}")


def get_file_from_github(path):
    if repo is None:
        return None
    try:
        file = repo.get_contents(path)
        content = base64.b64decode(file.content).decode('utf-8')
        return content
    except GithubException:
        return None


def save_morg_shift(date, bodies):
    year_month = date.strftime("%Y-%m")
    day = date.strftime("%Y-%m-%d")
    path = f"morg/{year_month}/{day}.csv"
    
    headers = ["Фамилия", "Тип", "Источник", "Оплачено", "Организация"]
    csv_content = ",".join(headers) + "\n"
    
    for body in bodies:
        row = [body["surname"], body["type"], body["source"], "ДА" if body["paid"] else "НЕТ", body.get("organization", "")]
        csv_content += ",".join(row) + "\n"
    
    return upload_to_github(path, csv_content, f"Смена {day}")


def save_ritual_order(order_data):
    path = "ritual/orders.csv"
    headers = ["Дата", "Заказчик", "Телефон", "Усопший_адрес", "Гроб", "Храм", "Кладбище", "Зарплата_агента"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    row = [now, order_data["customer"], order_data["phone"], order_data["deceased_address"],
           order_data["coffin"], order_data["temple"], order_data["cemetery"], str(order_data["agent_salary"])]
    return append_csv_github(path, ",".join(row), headers)


def get_weekly_stats():
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    
    morg_stats = {"total": 0, "paid": 0, "unpaid": 0, "income": 0, "sanitars": 0, "transport": 0, "profit": 0}
    
    # Проверяем последние 7 дней включая сегодня (8 дней)
    for i in range(8):
        date = week_ago + timedelta(days=i)
        path = f"morg/{date.strftime('%Y-%m')}/{date.strftime('%Y-%m-%d')}.csv"
        
        # Сначала пробуем GitHub, потом локально
        content = get_file_from_github(path)
        if not content and os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except:
                content = None
        
        if content:
            for line in content.strip().split("\n")[1:]:
                parts = line.split(",")
                if len(parts) >= 4:
                    morg_stats["total"] += 1
                    is_paid = parts[3].strip().upper() in ("ДА", "YES", "TRUE", "1")
                    if is_paid:
                        morg_stats["paid"] += 1
                        if parts[1].strip() == "Стандарт":
                            morg_stats["income"] += 8000
                            morg_stats["sanitars"] += 6500
                            morg_stats["transport"] += 1500
                        else:
                            morg_stats["income"] += 10000
                            morg_stats["sanitars"] += 8000
                            morg_stats["transport"] += 2000
                    else:
                        morg_stats["unpaid"] += 1
    
    morg_stats["profit"] = morg_stats["income"] - morg_stats["sanitars"] - morg_stats["transport"]
    
    ritual_stats = {"orders": 0, "agent_salary": 0}
    ritual_path = "ritual/orders.csv"
    ritual_content = get_file_from_github(ritual_path)
    if not ritual_content and os.path.exists(ritual_path):
        try:
            with open(ritual_path, 'r', encoding='utf-8') as f:
                ritual_content = f.read()
        except:
            ritual_content = None
    
    if ritual_content:
        for line in ritual_content.strip().split("\n")[1:]:
            parts = line.split(",")
            if len(parts) >= 8:
                try:
                    order_date = datetime.strptime(parts[0], "%Y-%m-%d %H:%M")
                    if week_ago <= order_date <= today:
                        ritual_stats["orders"] += 1
                        ritual_stats["agent_salary"] += int(parts[7])
                except:
                    pass
    
    return morg_stats, ritual_stats


def get_last_orders(limit=10):
    content = get_file_from_github("ritual/orders.csv")
    if not content:
        return []
    lines = content.strip().split("\n")[1:]
    orders = []
    for line in lines:
        parts = line.split(",")
        if len(parts) >= 8:
            orders.append({"date": parts[0], "customer": parts[1], "phone": parts[2],
                          "deceased_address": parts[3], "coffin": parts[4],
                          "temple": parts[5], "cemetery": parts[6], "agent_salary": parts[7]})
    return orders[-limit:]

# ==========================================
# КЛАВИАТУРЫ
# ==========================================

def get_main_keyboard():
    """Постоянная клавиатура — всегда внизу"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🔄 Новая смена"), KeyboardButton(text="➕ Добавить тело"))
    builder.row(KeyboardButton(text="🔒 Подвести смену"), KeyboardButton(text="🕯️ Ритуал"))
    builder.row(KeyboardButton(text="🚕 Водителю"), KeyboardButton(text="📊 Отчёт"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="Выберите действие:")


def get_type_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Стандарт (8000₽)", callback_data="type_standard")],
        [InlineKeyboardButton(text="Не стандарт (10000₽)", callback_data="type_nonstandard")]
    ])


def get_source_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отделение", callback_data="source_department")],
        [InlineKeyboardButton(text="Амбулаторно", callback_data="source_outpatient")]
    ])


def get_payment_keyboard(index, bodies):
    """Клавиатура для отметки оплаты — список тел с +/-"""
    buttons = []
    for i, body in enumerate(bodies):
        status = "✅" if body["paid"] else "❌"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {body['surname']} ({body['type']})",
            callback_data=f"toggle_pay_{i}"
        )])
    buttons.append([InlineKeyboardButton(text="💰 РАССЧИТАТЬ", callback_data="calculate_shift")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==========================================
# ГЛАВНОЕ МЕНЮ
# ==========================================

@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    current_shift["date"] = datetime.now()
    current_shift["bodies"] = []
    await message.answer(
        "👋 Бот для учёта морга и ритуальных услуг\n\n"
        "📋 Используй кнопки внизу для работы:",
        reply_markup=get_main_keyboard()
    )


@dp.message(F.text == "➕ Добавить тело")
async def add_body_menu(message: types.Message, state: FSMContext):
    """Добавить тело в текущую смену"""
    if not current_shift["date"]:
        current_shift["date"] = datetime.now()
        current_shift["bodies"] = []
        await message.answer("📝 Новая смена начата\n\nВведи фамилию усопшего:")
    else:
        await message.answer("Введи фамилию усопшего:")
    await state.set_state(MorgShift.waiting_for_surname)


@dp.message(F.text == "🔄 Новая смена")
async def new_shift_menu(message: types.Message, state: FSMContext):
    """Начать новую смену (сбросить предыдущую)"""
    if current_shift["bodies"]:
        await message.answer(
            f"⚠️ В текущей смене {len(current_shift['bodies'])} тел.\n"
            f"Если начнёшь новую — предыдущая будет потеряна.\n\n"
            f"Точно начать заново?"
        )
    current_shift["date"] = datetime.now()
    current_shift["bodies"] = []
    await message.answer("📝 Новая смена начата\n\nВведи фамилию усопшего:")
    await state.set_state(MorgShift.waiting_for_surname)


@dp.message(F.text == "🔒 Подвести смену")
async def close_shift_menu(message: types.Message, state: FSMContext):
    """Показать список тел для отметки оплаты"""
    if not current_shift["bodies"]:
        await message.answer("⚠️ Смена пуста. Сначала добавь тела кнопкой «➕ Добавить тело»")
        return
    
    await state.set_state(MorgShift.closing_mark_payment)
    await state.update_data(closing_bodies=current_shift["bodies"].copy())
    
    keyboard = get_payment_keyboard(0, current_shift["bodies"])
    await message.answer("📋 Отметь оплату для каждого тела (нажми на фамилию):\n", reply_markup=keyboard)


@dp.message(F.text == "🕯️ Ритуал")
async def ritual_menu(message: types.Message, state: FSMContext):
    current_order.clear()
    await message.answer("📋 Ритуальный заказ\n\nВведи ФИО заказчика:")
    await state.set_state(RitualOrder.waiting_for_customer)


@dp.message(F.text == "🚕 Водителю")
async def driver_route(message: types.Message):
    """Показать последний маршрут для водителя"""
    global last_driver_route
    if not last_driver_route:
        await message.answer("⚠️ Пока нет маршрутов. Оформите ритуальный заказ.")
        return
    await message.answer(last_driver_route)


@dp.message(F.text == "📊 Отчёт")
async def report_menu(message: types.Message):
    await message.answer("⏳ Загружаю данные...")
    morg, ritual = get_weekly_stats()
    
    today = datetime.now().strftime("%d.%m.%Y")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%d.%m.%Y")
    
    report = (
        f"📊 ОТЧЁТ ({week_ago} — {today})\n\n"
        f"⚰️ МОРГ:\n"
        f"Всего тел: {morg['total']}\n"
        f"✅ Оплачено: {morg['paid']} | ❌ Нет: {morg['unpaid']}\n"
        f"Доход: {morg['income']}₽\n"
        f"🧑‍⚕️ Санитары: {morg['sanitars']}₽\n"
        f"🚚 Перевозка: {morg['transport']}₽\n"
        f"💰 Прибыль: {morg['profit']}₽\n\n"
        f"🕯️ РИТУАЛ:\n"
        f"Заказов: {ritual['orders']}\n"
        f"Агенты: {ritual['agent_salary']}₽"
    )
    await message.answer(report)

# ==========================================
# МОДУЛЬ "МОРГ" — ДОБАВЛЕНИЕ
# ==========================================

@dp.message(MorgShift.waiting_for_surname)
async def morg_surname(message: types.Message, state: FSMContext):
    surname = message.text.strip().upper()
    if not surname:
        await message.answer("⚠️ Фамилия не может быть пустой:")
        return
    await state.update_data(surname=surname)
    await message.answer("Выбери тип:", reply_markup=get_type_keyboard())
    await state.set_state(MorgShift.waiting_for_type)


@dp.callback_query(F.data.in_(["type_standard", "type_nonstandard"]))
async def morg_type(callback: types.CallbackQuery, state: FSMContext):
    body_type = "Стандарт" if callback.data == "type_standard" else "Не стандарт"
    price = "8000₽" if body_type == "Стандарт" else "10000₽"
    await state.update_data(body_type=body_type)
    await callback.message.edit_text(f"Тип: {body_type} ({price})\n\nВыбери источник:", reply_markup=get_source_keyboard())
    await callback.answer()
    await state.set_state(MorgShift.waiting_for_source)


@dp.callback_query(F.data.in_(["source_department", "source_outpatient"]))
async def morg_source(callback: types.CallbackQuery, state: FSMContext):
    source = "Отделение" if callback.data == "source_department" else "Амбулаторно"
    await state.update_data(source=source)
    
    data = await state.get_data()
    surname = data["surname"]
    body_type = data["body_type"]
    
    body = {"surname": surname, "type": body_type, "source": source, "paid": None, "organization": ""}
    current_shift["bodies"].append(body)
    
    count = len(current_shift["bodies"])
    await callback.message.edit_text(f"✅ {surname} ({body_type}, {source})\nТел в смене: {count}")
    await callback.answer()
    
    # Сразу спрашиваем следующую фамилию
    await callback.message.answer("Введи следующую фамилию (или нажми 🔒 Подвести смену):")
    await state.set_state(MorgShift.waiting_for_surname)


# ==========================================
# МОДУЛЬ "МОРГ" — ЗАКРЫТИЕ СМЕНЫ
# ==========================================

@dp.callback_query(F.data.startswith("toggle_pay_"))
async def toggle_payment(callback: types.CallbackQuery, state: FSMContext):
    """Переключение статуса оплаты по клику на фамилию"""
    index = int(callback.data.split("_")[-1])
    data = await state.get_data()
    bodies = data.get("closing_bodies", [])
    
    if index >= len(bodies):
        await callback.answer("Ошибка индекса")
        return
    
    # Переключаем статус
    bodies[index]["paid"] = not bodies[index].get("paid", False)
    await state.update_data(closing_bodies=bodies)
    
    # Обновляем клавиатуру
    keyboard = get_payment_keyboard(index, bodies)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "calculate_shift")
async def calculate_callback(callback: types.CallbackQuery, state: FSMContext):
    """Нажата кнопка РАССЧИТАТЬ"""
    data = await state.get_data()
    bodies = data.get("closing_bodies", [])
    
    # Проверяем есть ли неоплаченные без организации
    for i, body in enumerate(bodies):
        if not body.get("paid") and not body.get("organization"):
            # Запрашиваем организацию
            await state.update_data(closing_bodies=bodies, pending_org_index=i)
            await callback.message.edit_text(f"Кто вывез тело {body['surname']}? (введи организацию):")
            await callback.answer()
            await state.set_state(MorgShift.closing_organization)
            return
    
    # Все отмечены — считаем
    await callback.answer()
    await show_calculation(callback.message, bodies, state)


@dp.message(MorgShift.closing_organization)
async def org_handler(message: types.Message, state: FSMContext):
    org = message.text.strip().upper()
    data = await state.get_data()
    bodies = data.get("closing_bodies", [])
    index = data.get("pending_org_index", 0)
    
    bodies[index]["organization"] = org
    await state.update_data(closing_bodies=bodies)
    
    # Проверяем остальные
    for i, body in enumerate(bodies):
        if not body.get("paid") and not body.get("organization"):
            await state.update_data(pending_org_index=i)
            await message.answer(f"Кто вывез тело {body['surname']}? (введи организацию):")
            return
    
    # Все готовы к расчёту
    await show_calculation(message, bodies, state)


async def show_calculation(message, bodies, state):
    """Визуальный расчёт смены"""
    
    if not current_shift["date"]:
        current_shift["date"] = datetime.now()
    
    # Разделяем на стационар и амбулаторно
    stationary = [b for b in bodies if b["source"] == "Отделение"]
    ambulatory = [b for b in bodies if b["source"] == "Амбулаторно"]
    
    sanitars_total = 0
    transport_total = 0
    
    report = f"📊 СМЕНА {current_shift['date'].strftime('%d.%m.%Y')}\n"
    
    # Стационар
    if stationary:
        report += "\n🏥 СТАЦИОНАР:\n"
        for i, b in enumerate(stationary, 1):
            if b.get("paid"):
                salary = 6500 if b["type"] == "Стандарт" else 8000
                transport = 1500 if b["type"] == "Стандарт" else 2000
                sanitars_total += salary
                transport_total += transport
                report += f"{i}. {b['surname']} — {salary}\n"
            else:
                report += f"{i}. {b['surname']} — 0 → {b.get('organization', 'НЕ УКАЗАНО')}\n"
    
    # Амбулаторно
    if ambulatory:
        report += "\n🚗 АМБУЛАТОРНО:\n"
        for i, b in enumerate(ambulatory, 1):
            if b.get("paid"):
                salary = 6500 if b["type"] == "Стандарт" else 8000
                transport = 1500 if b["type"] == "Стандарт" else 2000
                sanitars_total += salary
                transport_total += transport
                report += f"{i}. {b['surname']} — {salary}\n"
            else:
                report += f"{i}. {b['surname']} — 0 → {b.get('organization', 'НЕ УКАЗАНО')}\n"
    
    report += (
        f"\n━━━━━━━━━━━━━━━━━\n"
        f"🧑‍⚕️ Санитары: {sanitars_total}₽\n"
        f"🚚 Перевозка: {transport_total}₽"
    )
    
    # Сохраняем в GitHub
    success = save_morg_shift(current_shift["date"], bodies)
    if success:
        report += "\n\n✅ Сохранено в GitHub"
    else:
        report += "\n\n⚠️ Сохранено локально"
    
    await message.answer(report)
    
    # Очищаем
    current_shift["date"] = None
    current_shift["bodies"] = []
    await state.clear()

# ==========================================
# МОДУЛЬ "РИТУАЛЬНЫЕ УСЛУГИ"
# ==========================================

@dp.message(RitualOrder.waiting_for_customer)
async def ritual_customer(message: types.Message, state: FSMContext):
    customer = message.text.strip().upper()
    if not customer:
        await message.answer("⚠️ ФИО не может быть пустым:")
        return
    current_order["customer"] = customer
    await message.answer("Введи телефон (8...):")
    await state.set_state(RitualOrder.waiting_for_phone)


@dp.message(RitualOrder.waiting_for_phone)
async def ritual_phone(message: types.Message, state: FSMContext):
    current_order["phone"] = message.text.strip()
    await message.answer("Введи ФИО усопшего и адрес (откуда забирать):")
    await state.set_state(RitualOrder.waiting_for_deceased_address)


@dp.message(RitualOrder.waiting_for_deceased_address)
async def ritual_deceased(message: types.Message, state: FSMContext):
    current_order["deceased_address"] = message.text.strip().upper()
    await message.answer("Введи описание гроба:")
    await state.set_state(RitualOrder.waiting_for_coffin)


@dp.message(RitualOrder.waiting_for_coffin)
async def ritual_coffin(message: types.Message, state: FSMContext):
    current_order["coffin"] = message.text.strip().upper()
    await message.answer("Введи храм:")
    await state.set_state(RitualOrder.waiting_for_temple)


@dp.message(RitualOrder.waiting_for_temple)
async def ritual_temple(message: types.Message, state: FSMContext):
    current_order["temple"] = message.text.strip().upper()
    await message.answer("Введи кладбище:")
    await state.set_state(RitualOrder.waiting_for_cemetery)


@dp.message(RitualOrder.waiting_for_cemetery)
async def ritual_cemetery(message: types.Message, state: FSMContext):
    current_order["cemetery"] = message.text.strip().upper()
    await message.answer("Введи зарплату агента (цифрой):")
    await state.set_state(RitualOrder.waiting_for_agent_salary)


@dp.message(RitualOrder.waiting_for_agent_salary)
async def ritual_salary(message: types.Message, state: FSMContext):
    try:
        current_order["agent_salary"] = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введи число!")
        return
    
    save_ritual_order(current_order)
    
    # Маршрут для водителя
    global last_driver_route
    cust = current_order["customer"].split()
    cust_short = f"{cust[0]} {cust[1][0]}." if len(cust) >= 2 else current_order["customer"]
    
    last_driver_route = (
        "🚕 ЗАКАЗ ВОДИТЕЛЮ\n"
        f"Усопший: {current_order['deceased_address']}\n"
        f"Гроб: {current_order['coffin']}\n"
        f"Храм: {current_order['temple']}\n"
        f"Кладбище: {current_order['cemetery']}\n"
        f"Заказчик: {cust_short} ☎️ {current_order['phone']}"
    )
    
    await message.answer(f"✅ Заказ сохранён\n\n📋 СКОПИРУЙ И ОТПРАВЬ В MAX:\n\n{last_driver_route}")
    current_order.clear()
    await state.clear()

# ==========================================
# ЗАПУСК
# ==========================================

USE_WEBHOOK = os.getenv("USE_WEBHOOK", "False").lower() == "true"
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

@dp.errors()
async def errors_handler(exception):
    logger.error(f"Ошибка: {exception}")
    return True


async def on_startup(bot: Bot):
    """При старте на Render — регистрируем вебхук в Telegram"""
    # Render автоматически задаёт эти переменные
    external_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")

    if not external_url and hostname:
        external_url = f"https://{hostname}"

    logger.info(f"RENDER_EXTERNAL_URL={os.getenv('RENDER_EXTERNAL_URL')}")
    logger.info(f"RENDER_EXTERNAL_HOSTNAME={hostname}")

    if not external_url:
        logger.error("RENDER_EXTERNAL_URL не установлен! Вебхук не зарегистрирован!")
        return

    # Telegram разрешает только A-Z a-z 0-9 - _
    secret = WEBHOOK_SECRET if WEBHOOK_SECRET and all(c.isalnum() or c in "-_" for c in WEBHOOK_SECRET) else None

    url = f"{external_url}{WEBHOOK_PATH}"
    try:
        await bot.set_webhook(url=url, secret_token=secret)
        logger.info(f"✅ Вебхук установлен: {url}")
    except Exception as e:
        logger.error(f"❌ Ошибка установки вебхука: {e}")


async def on_shutdown(bot: Bot):
    """При остановке — НЕ удаляем вебхук (Render перезапускается часто)"""
    pass


def run_webhook():
    """Render.com — webhook + aiohttp сервер"""
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import (
        SimpleRequestHandler,
        setup_application,
    )

    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN не установлен!")
        return

    bot = Bot(token=token)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()

    # Health check
    async def health(request):
        return web.Response(text="OK", status=200)

    app.router.add_get("/health", health)
    app.router.add_get("/", health)

    # Обработчик вебхука
    secret = WEBHOOK_SECRET if WEBHOOK_SECRET else None
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=secret,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    port = int(os.getenv("PORT", 10000))
    logger.info(f"Бот запущен (webhook) на порту {port}!")

    web.run_app(app, host="0.0.0.0", port=port)


def run_polling():
    """Локальный режим — polling"""
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN не установлен!")
        return

    async def _main():
        bot = Bot(token=token)
        
        # Удалить вебхук при локальном запуске
        await bot.delete_webhook()
        
        # Команды меню
        await bot.set_my_commands([
            types.BotCommand(command="start", description="Запустить бота"),
        ])

        try:
            logger.info("Бот запущен (polling)!")
            await dp.start_polling(bot)
        except Exception as e:
            logger.error(f"Ошибка: {e}")
        finally:
            await bot.session.close()

    asyncio.run(_main())


if __name__ == "__main__":
    if USE_WEBHOOK:
        run_webhook()
    else:
        try:
            run_polling()
        except KeyboardInterrupt:
            logger.info("Остановлен")
