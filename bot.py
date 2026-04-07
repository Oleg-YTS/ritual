"""
Telegram-бот для учёта морга и ритуальных услуг
Версия: 5.0 — Роли, Похороны/Кремация, Карточки
"""

import os
import logging
import base64
import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv
from github import Github, GithubException

# ============================================================
# ИНИЦИАЛИЗАЦИЯ
# ============================================================

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN не найден!")
    exit(1)

# Бот и диспетчер
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# GitHub
repo = None
if GITHUB_TOKEN and REPO_NAME:
    try:
        github = Github(GITHUB_TOKEN)
        repo = github.get_repo(REPO_NAME)
        logger.info(f"GitHub: {REPO_NAME}")
    except Exception as e:
        logger.error(f"GitHub ошибка: {e}")

# ============================================================
# ГЛОБАЛЬНЫЕ ДАННЫЕ
# ============================================================

# Смена морга
current_shift = {"location": None, "date": None, "bodies": []}
last_driver_route = None

# Текущий заказ (временный)
current_order = {}

# Пользователи (захардкожены для надёжности)
users_cache = {
    747600306: {"role": "super_admin", "name": "Евсеев", "location": "Мира 11"},
    7819002363: {"role": "manager", "name": "Семенов", "location": "Первомайская 13"},
    387529965: {"role": "agent", "name": "Жуков", "location": ""},
}

# ============================================================
# FSM СОСТОЯНИЯ
# ============================================================

class Morg(StatesGroup):
    location = State()  # Выбор морга
    surname = State()
    type = State()
    source = State()
    closing = State()
    org = State()

# Общий стейт для ритуалок (и похороны, и кремация)
class Ritual(StatesGroup):
    event_date = State()  # Дата события (первое поле!)
    customer = State()
    phone = State()
    deceased = State()
    
    # Похороны
    coffin = State()
    temple = State()
    cemetery = State()
    
    # Кремация
    urn_type = State()    # Картон / Пластик
    urn_color = State()   # Если пластик
    extras = State()      # Доп. услуги (множественный выбор)
    temple_cremation = State()  # Храм для кремации (без зала)

# ============================================================
# GITHUB ФУНКЦИИ
# ============================================================

def github_upload(path, content, msg="Авто"):
    if not repo:
        _local_save(path, content)
        return False
    try:
        try:
            f = repo.get_contents(path)
            repo.update_file(f.path, msg, content, f.sha, branch="main")
        except GithubException:
            repo.create_file(path, msg, content, branch="main")
        return True
    except Exception as e:
        logger.error(f"GitHub: {e}")
        _local_save(path, content)
        return False

def _local_save(path, content):
    try:
        d = os.path.dirname(path)
        if d: os.makedirs(d, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(content + "\n")
    except Exception as e:
        logger.error(f"Local save: {e}")

def github_read(path):
    if not repo: return None
    try:
        f = repo.get_contents(path)
        return f.decoded_content.decode('utf-8')
    except GithubException: return None

def read_file(path):
    c = github_read(path)
    if not c and os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f: return f.read()
        except: pass
    return c

def github_append(path, row, headers):
    if not repo:
        _local_save(path, row)
        return False
    try:
        existing = ""
        try:
            f = repo.get_contents(path)
            existing = f.decoded_content.decode('utf-8')
        except GithubException:
            existing = ",".join(headers) + "\n"
        new = existing + row + "\n"
        try:
            f = repo.get_contents(path)
            repo.update_file(f.path, f"Add {path}", new, f.sha, branch="main")
        except GithubException:
            repo.create_file(path, f"Create {path}", new, branch="main")
        return True
    except Exception as e:
        logger.error(f"CSV: {e}")
        _local_save(path, row)
        return False

# ============================================================
# ПОЛЬЗОВАТЕЛИ И РОЛИ
# ============================================================

def load_users():
    global users_cache
    users_cache = {}
    try:
        # Сначала пробуем локальный файл (Render)
        with open("users.csv", 'r', encoding='utf-8') as f:
            for line in f.read().strip().split("\n")[1:]:
                parts = line.split(",")
                if len(parts) >= 2:
                    uid = int(parts[0].strip())
                    users_cache[uid] = {
                        "role": parts[1].strip(),
                        "name": parts[2].strip() if len(parts) > 2 else "User",
                        "location": parts[3].strip() if len(parts) > 3 else ""
                    }
        logger.info(f"Загружено пользователей: {len(users_cache)}")
        for uid, info in users_cache.items():
            logger.info(f"  {uid} -> {info['role']} ({info['name']})")
    except Exception as e:
        logger.error(f"Load users error: {e}")
        # Fallback: пробуем GitHub
        content = github_read("users.csv")
        if content:
            for line in content.strip().split("\n")[1:]:
                parts = line.split(",")
                if len(parts) >= 2:
                    uid = int(parts[0].strip())
                    users_cache[uid] = {
                        "role": parts[1].strip(),
                        "name": parts[2].strip() if len(parts) > 2 else "User",
                        "location": parts[3].strip() if len(parts) > 3 else ""
                    }
            logger.info(f"Загружено из GitHub: {len(users_cache)}")

def get_user_role(user_id):
    if user_id in users_cache:
        return users_cache[user_id]["role"]
    
    # Перезагрузим из файла если не нашли
    content = read_file("users.csv")
    if content:
        for line in content.strip().split("\n")[1:]:
            parts = line.split(",")
            if len(parts) >= 2 and int(parts[0]) == user_id:
                users_cache[user_id] = {"role": parts[1], "name": parts[2] if len(parts)>2 else "User"}
                return users_cache[user_id]["role"]
    
    # Если нет в списке — просим зарегистрироваться
    return None

def register_user(user_id, name):
    # Заглушка для регистрации: просто отправляем сообщение админу в будущем
    # Сейчас просто возвращаем None (нет доступа)
    return None

# ============================================================
# СТАТИСТИКА МОРГА
# ============================================================

def get_weekly():
    today = datetime.now()
    week = today - timedelta(days=7)
    m = {"total":0,"paid":0,"unpaid":0,"income":0,"sanitars":0,"transport":0,"profit":0}

    for i in range(8):
        d = week + timedelta(days=i)
        content = read_file(f"morg/{d.strftime('%Y-%m')}/{d.strftime('%Y-%m-%d')}.csv")
        if content:
            for line in content.strip().split("\n")[1:]:
                p = line.split(",")
                if len(p) < 4: continue
                m["total"] += 1
                if p[3].strip().upper() in ("ДА","YES","TRUE","1"):
                    m["paid"] += 1
                    if p[1].strip() == "Стандарт":
                        m["income"] += 8000; m["sanitars"] += 6500; m["transport"] += 1500
                    else:
                        m["income"] += 10000; m["sanitars"] += 8000; m["transport"] += 2000
                else:
                    m["unpaid"] += 1

    m["profit"] = m["income"] - m["sanitars"] - m["transport"]
    return m

# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def main_kb_admin():
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="🔄 Новая смена"), KeyboardButton(text="➕ Добавить тело"))
    b.row(KeyboardButton(text="🔒 Подвести смену"), KeyboardButton(text="🕯️ Ритуал"))
    b.row(KeyboardButton(text="🚕 Водителю"), KeyboardButton(text="📊 Отчёт"))
    b.row(KeyboardButton(text="👥 Пользователи"))
    return b.as_markup(resize_keyboard=True)

def main_kb_agent():
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="🕯️ Ритуал"))
    b.row(KeyboardButton(text="🚕 Водителю"))
    return b.as_markup(resize_keyboard=True)

def ritual_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚰️ Похороны", callback_data="ord_funeral")],
        [InlineKeyboardButton(text="🔥 Кремация", callback_data="ord_cremation")]
    ])

def urn_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Картон", callback_data="urn_cardboard")],
        [InlineKeyboardButton(text="🏺 Пластик", callback_data="urn_plastic")]
    ])

def urn_color_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚪ Белый", callback_data="col_white"), InlineKeyboardButton(text="⚫ Чёрный", callback_data="col_black")],
        [InlineKeyboardButton(text="🟢 Зелёный", callback_data="col_green"), InlineKeyboardButton(text="🔵 Синий", callback_data="col_blue")]
    ])

def extras_kb(selected=None):
    if selected is None: selected = []
    b = InlineKeyboardBuilder()
    
    opts = {"box_pol": "Гроб полированный", "large": "Крупное тело", "hall": "Зал+отпевание", "urgent": "Срочная"}
    for k, v in opts.items():
        mark = "✅" if k in selected else "⬜"
        b.row(InlineKeyboardButton(text=f"{mark} {v}", callback_data=f"extra_{k}"))
    
    b.row(InlineKeyboardButton(text="ДАЛЕЕ ➡️", callback_data="extra_done"))
    return b.as_markup()

def type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Стандарт (8000₽)", callback_data="type_std")],
        [InlineKeyboardButton(text="Не стандарт (10000₽)", callback_data="type_non")]
    ])

def location_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏥 Первомайская 13", callback_data="loc_perv")],
        [InlineKeyboardButton(text="🏥 Мира 11", callback_data="loc_mira")]
    ])

def source_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отделение", callback_data="src_dep")],
        [InlineKeyboardButton(text="Амбулаторно", callback_data="src_amb")]
    ])

def pay_kb(bodies):
    btns = []
    for i, b in enumerate(bodies):
        s = "✅" if b.get("paid") else "❌"
        btns.append([InlineKeyboardButton(text=f"{s} {b['surname']} ({b['type']})", callback_data=f"pay_{i}")])
    btns.append([InlineKeyboardButton(text="💰 РАССЧИТАТЬ", callback_data="calc")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

# ============================================================
# ХЕЛПЕРЫ ДЛЯ СООБЩЕНИЙ
# ============================================================

def main_kb_super_admin():
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="🔄 Новая смена"), KeyboardButton(text="➕ Добавить тело"))
    b.row(KeyboardButton(text="🔒 Подвести смену"), KeyboardButton(text="📋 Смена за сегодня"))
    b.row(KeyboardButton(text="🕯️ Ритуал"))
    b.row(KeyboardButton(text="🚕 Водителю"), KeyboardButton(text="📊 Отчёт"))
    b.row(KeyboardButton(text="👥 Пользователи"))
    return b.as_markup(resize_keyboard=True)

def main_kb_manager():
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="🔄 Новая смена"), KeyboardButton(text="➕ Добавить тело"))
    b.row(KeyboardButton(text="🔒 Подвести смену"), KeyboardButton(text="📋 Смена за сегодня"))
    b.row(KeyboardButton(text="🕯️ Ритуал"))
    b.row(KeyboardButton(text="🚕 Водителю"), KeyboardButton(text="📊 Отчёт"))
    return b.as_markup(resize_keyboard=True)

def main_kb_agent():
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="🕯️ Ритуал"))
    b.row(KeyboardButton(text="🚕 Водителю"))
    return b.as_markup(resize_keyboard=True)

def get_menu(role):
    if role == "super_admin": return main_kb_super_admin()
    if role == "manager": return main_kb_manager()
    return main_kb_agent()

# ============================================================
# ОБРАБОТЧИКИ
# ============================================================

@dp.message(F.text == "/start")
async def cmd_start(m: types.Message):
    uid = m.from_user.id
    logger.info(f"/start от {uid}")
    
    if uid not in users_cache:
        await m.answer(f"⚠️ Вас нет в списке. Ваш ID: {uid}. Обратитесь к администратору.")
        return

    user = users_cache[uid]
    role = user["role"]
    name = user["name"]
    loc = user.get("location", "")
    
    logger.info(f"Доступ разрешён: {name} ({role})")
    
    current_shift["date"] = datetime.now()
    current_shift["bodies"] = []
    
    menu = get_menu(role)
    loc_text = f" | {loc}" if loc else ""
    await m.answer(f"👋 {name} ({role}{loc_text})\n\n📋 Меню:", reply_markup=menu)

# --- АДМИН КОМАНДЫ ---

@dp.message(F.text == "👥 Пользователи")
async def users_menu(m: types.Message):
    role = get_user_role(m.from_user.id)
    if role != "admin": return
    await m.answer("Функция в разработке. Правьте users.csv вручную.")

# --- РИТУАЛ (ОБЩИЙ) ---

@dp.message(F.text == "🕯️ Ритуал")
async def ritual_menu(m: types.Message):
    await m.answer("Выбери тип заказа:", reply_markup=ritual_type_kb())

@dp.callback_query(F.data.in_(["ord_funeral", "ord_cremation"]))
async def start_order(cb: types.CallbackQuery, state: FSMContext):
    order_type = "funeral" if cb.data == "ord_funeral" else "cremation"
    await state.update_data(type=order_type)
    await state.update_data(extras=[])
    
    txt = "⚰️ ПОХОРОНЫ\n\nДата события (дд.мм.гггг):" if order_type == "funeral" else "🔥 КРЕМАЦИЯ\n\nДата события (дд.мм.гггг):"
    await cb.message.edit_text(txt)
    await cb.answer()
    await state.set_state(Ritual.event_date)

@dp.message(Ritual.event_date)
async def r_date(m: types.Message, state: FSMContext):
    await state.update_data(event_date=m.text.strip())
    await m.answer("ФИО заказчика:")
    await state.set_state(Ritual.customer)

@dp.message(Ritual.customer)
async def r_customer(m: types.Message, state: FSMContext):
    if not m.text.strip(): await m.answer("⚠️ Введи ФИО:"); return
    await state.update_data(customer=m.text.strip().upper())
    await m.answer("Телефон:")
    await state.set_state(Ritual.phone)

@dp.message(Ritual.phone)
async def r_phone(m: types.Message, state: FSMContext):
    await state.update_data(phone=m.text.strip())
    await m.answer("ФИО усопшего + адрес морга:")
    await state.set_state(Ritual.deceased)

@dp.message(Ritual.deceased)
async def r_deceased(m: types.Message, state: FSMContext):
    await state.update_data(deceased=m.text.strip().upper())
    
    data = await state.get_data()
    if data["type"] == "funeral":
        await m.answer("Гроб:")
        await state.set_state(Ritual.coffin)
    else:
        # Кремация -> выбор урны
        await m.answer("Урна:", reply_markup=urn_kb())
        await state.set_state(Ritual.urn_type)

# --- ВЕТКА ПОХОРОН ---

@dp.message(Ritual.coffin)
async def r_coffin(m: types.Message, state: FSMContext):
    await state.update_data(coffin=m.text.strip().upper())
    await m.answer("Храм:")
    await state.set_state(Ritual.temple)

@dp.message(Ritual.temple)
async def r_temple(m: types.Message, state: FSMContext):
    await state.update_data(temple=m.text.strip().upper())
    await m.answer("Кладбище:")
    await state.set_state(Ritual.cemetery)

@dp.message(Ritual.cemetery)
async def r_cemetery(m: types.Message, state: FSMContext):
    await state.update_data(cemetery=m.text.strip().upper())
    await save_ritual_order(m, state)

@dp.message(Ritual.temple_cremation)
async def cremation_temple(m: types.Message, state: FSMContext):
    await state.update_data(temple=m.text.strip().upper())
    await state.update_data(cemetery="Крематорий")
    await save_ritual_order(m, state)

# --- ВЕТКА КРЕМАЦИИ ---

@dp.callback_query(F.data.in_(["urn_cardboard", "urn_plastic"]))
async def urn_selected(cb: types.CallbackQuery, state: FSMContext):
    urn = "Картон" if cb.data == "urn_cardboard" else "Пластик"
    await state.update_data(urn_type=urn)
    await cb.answer()
    
    if urn == "Пластик":
        await cb.message.edit_text("Цвет пластика:", reply_markup=urn_color_kb())
        await state.set_state(Ritual.urn_color)
    else:
        await cb.message.edit_text("Доп. услуги:")
        await state.update_data(extras=[])
        await cb.message.answer("Выбери услуги (можно несколько):", reply_markup=extras_kb([]))
        await state.set_state(Ritual.extras)

@dp.callback_query(F.data.startswith("col_"))
async def color_selected(cb: types.CallbackQuery, state: FSMContext):
    cols = {"col_white":"Белый", "col_black":"Чёрный", "col_green":"Зелёный", "col_blue":"Синий"}
    await state.update_data(urn_color=cols.get(cb.data, "Неизвестно"))
    await state.set_state(Ritual.extras)
    await cb.message.edit_text(f"Цвет: {cols[cb.data]}\n\nДоп. услуги:")
    await cb.message.answer("Выбери услуги:", reply_markup=extras_kb([]))
    await cb.answer()

@dp.callback_query(F.data.startswith("extra_"))
async def extras_handler(cb: types.CallbackQuery, state: FSMContext):
    key = cb.data.split("_")[1]
    data = await state.get_data()
    extras = data.get("extras", [])

    if key == "done":
        # Переход дальше
        if "hall" in extras:
            # Если зал+отпевание, храм пропускаем
            await state.update_data(temple="Зал отпевания")
            await state.update_data(cemetery="Крематорий")
            await cb.answer("Услуги сохранены")
            await save_ritual_order(cb.message, state)
        else:
            await cb.answer("Услуги сохранены")
            await cb.message.answer("Храм:")
            # Переходим в стейт temple, но после него cemetery будет Крематорий
            await state.set_state(Ritual.temple_cremation)
        return

    if key in extras:
        extras.remove(key)
    else:
        extras.append(key)
    
    await state.update_data(extras=extras)
    await cb.message.edit_reply_markup(reply_markup=extras_kb(extras))
    await cb.answer()

# ============================================================
# СОХРАНЕНИЕ И ВЫВОД РЕЗУЛЬТАТА
# ============================================================

async def save_ritual_order(m, state: FSMContext):
    data = await state.get_data()
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    o_type = data["type"]
    
    details = ""
    extras = "; ".join(data.get("extras", []))
    
    if o_type == "funeral":
        details = data.get("coffin", "")
    else:
        urn = data.get("urn_type", "")
        if urn == "Пластик": urn += f" ({data.get('urn_color', '')})"
        details = urn

    row = f"{now},{data['event_date']},{o_type},{data['customer']},{data['phone']},{data['deceased']},{details},{extras},{data.get('temple','')},{data['cemetery']}"
    
    ok = github_append("ritual/orders.csv", row, "Дата_записи,Дата_события,Тип,Заказчик,Тел,Усопший,Детали,Допы,Храм,Кладбище")
    
    # 1. Маршрут водителю
    route = build_driver_route(data)
    global last_driver_route
    last_driver_route = route
    
    txt = "✅ Заказ сохранён\n\n" + "📋 СКОПИРУЙ ВОДИТЕЛЮ:\n" + route
    
    # 2. Если кремация — шлём карточку админу (и пользователю для инфо)
    if o_type == "cremation":
        card = build_crematorium_card(data)
        txt += "\n\n🔥 КАРТОЧКА В КРЕМАТОРИЙ:\n" + card
        # Тут в идеале переслать админу, но пока просто покажем
        
    await m.answer(txt)
    await state.clear()

def build_driver_route(data):
    t = data["type"]
    txt = f"🚕 ЗАКАЗ ВОДИТЕЛЮ\nДата: {data.get('event_date','')}\nТип: {'Похороны' if t=='funeral' else 'Кремация'}\n"
    txt += f"Усопший: {data['deceased']}\n"
    
    if t == "funeral":
        txt += f"Гроб: {data.get('coffin','')}\n"
        txt += f"Храм: {data.get('temple','')}\n"
        txt += f"Кладбище: {data.get('cemetery','')}\n"
    else:
        extras = data.get("extras", [])
        if "hall" in extras:
            txt += "Тип: Кремация (зал+отпевание)\n"
            txt += f"Кладбище: Крематорий\n"
        else:
            txt += f"Храм: {data.get('temple','')}\n"
            txt += f"Кладбище: Крематорий\n"
            
    txt += f"☎️ {data['phone']}"
    return txt

def build_crematorium_card(data):
    urn = data.get('urn_type', '')
    if urn == 'Пластик': urn += f" ({data.get('urn_color','')})"
    
    # Переводим ключи допов в русский текст
    extras_map = {
        "box_pol": "Гроб полированный",
        "large": "Крупное тело",
        "hall": "Зал+отпевание",
        "urgent": "Срочная"
    }
    extras_raw = data.get("extras", [])
    extras_ru = [extras_map.get(e, e) for e in extras_raw]
    extras_text = "; ".join(extras_ru) if extras_ru else "НЕТ"
    
    return (
        f"🔥 КРЕМАЦИЯ\n"
        f"Усопший: {data['deceased']}\n"
        f"Урна: {urn}\n"
        f"Допы: {extras_text}\n\n"
        f"Все стандартно, оплата наличными, оформлю в день кремации."
    )

# ============================================================
# ВОДИТЕЛЮ И ОТЧЁТ
# ============================================================

@dp.message(F.text == "🚕 Водителю")
async def driver_route(m: types.Message):
    global last_driver_route
    if not last_driver_route:
        await m.answer("⚠️ Нет маршрутов.")
        return
    await m.answer(last_driver_route)

@dp.message(F.text == "📊 Отчёт")
async def report_menu(m: types.Message):
    await m.answer("⏳ Загружаю...")
    mg = get_weekly()
    today = datetime.now().strftime("%d.%m.%Y")
    wk = (datetime.now() - timedelta(days=7)).strftime("%d.%m.%Y")
    txt = (f"📊 ОТЧЁТ ({wk} — {today})\n\n"
           f"⚰️ МОРГ:\nВсего: {mg['total']}\n"
           f"✅ {mg['paid']} | ❌ {mg['unpaid']}\n"
           f"Доход: {mg['income']}₽\n"
           f"🧑‍⚕️ Санитары: {mg['sanitars']}₽\n"
           f"🚚 Перевозка: {mg['transport']}₽\n"
           f"💰 Прибыль: {mg['profit']}₽")
    await m.answer(txt)

# ============================================================
# МОРГ (Оставляем старую логику, она работает)
# ============================================================

@dp.message(F.text == "➕ Добавить тело")
async def add_body(m: types.Message, state: FSMContext):
    if not current_shift["date"]:
        current_shift["date"] = datetime.now()
        current_shift["bodies"] = []
        await m.answer("📝 Смена начата\n\nФамилия:")
    else:
        await m.answer("Фамилия:")
    await state.set_state(Morg.surname)

@dp.message(F.text == "🔄 Новая смена")
async def new_shift(m: types.Message, state: FSMContext):
    await m.answer("Выбери морг:", reply_markup=location_kb())
    await state.set_state(Morg.location)

@dp.message(F.text == "🔒 Подвести смену")
async def close_shift(m: types.Message, state: FSMContext):
    if not current_shift["bodies"]:
        await m.answer("⚠️ Смена пуста.")
        return
    await state.update_data(bodies=current_shift["bodies"].copy())
    await m.answer("📋 Нажми на фамилию:", reply_markup=pay_kb(current_shift["bodies"]))
    await state.set_state(Morg.closing)

@dp.message(F.text == "📋 Смена за сегодня")
async def today_report(m: types.Message):
    today = datetime.now()
    ym = today.strftime("%Y-%m")
    d = today.strftime("%Y-%m-%d")
    
    txt = ""
    total_san = 0; total_trn = 0; total_bodies = 0
    
    for loc, name in [("perv", "Первомайская 13"), ("mira", "Мира 11")]:
        content = read_file(f"morg/{loc}/{ym}/{d}.csv")
        if content:
            loc_san = 0; loc_trn = 0; loc_bodies = 0
            for line in content.strip().split("\n")[1:]:
                p = line.split(",")
                if len(p) < 4: continue
                loc_bodies += 1
                if p[3].strip().upper() in ("ДА","YES","TRUE","1"):
                    s = 6500 if p[1].strip()=="Стандарт" else 8000
                    t = 1500 if p[1].strip()=="Стандарт" else 2000
                    loc_san += s; loc_trn += t
            total_san += loc_san; total_trn += loc_trn; total_bodies += loc_bodies
            txt += f"🏥 {name}: {loc_bodies} тел | 💰 {loc_san}₽ сан. | 🚚 {loc_trn}₽ пер.\n\n"
    
    if not txt:
        txt = "Смен за сегодня нет.\n\n"
    
    txt += f"📊 ИТОГО:\nВсего тел: {total_bodies}\nСанитары: {total_san}₽\nПеревозка: {total_trn}₽"
    await m.answer(txt)

# ============================================================
# МОРГ — ПОЛНАЯ ВЕРСИЯ
# ============================================================

@dp.callback_query(F.data.in_(["loc_perv", "loc_mira"]))
async def morg_location(cb: types.CallbackQuery, state: FSMContext):
    loc = "perv" if cb.data == "loc_perv" else "mira"
    loc_name = "Первомайская 13" if cb.data == "loc_perv" else "Мира 11"
    
    current_shift["location"] = loc
    current_shift["date"] = datetime.now()
    current_shift["bodies"] = []
    
    await cb.message.edit_text(f"🏥 {loc_name}\n\n📝 Смена начата\n\nФамилия:")
    await cb.answer()
    await state.set_state(Morg.surname)

@dp.message(Morg.surname)
async def morg_surname(m: types.Message, state: FSMContext):
    s = m.text.strip().upper()
    if not s: await m.answer("⚠️ Введи фамилию:"); return
    await state.update_data(surname=s)
    await m.answer("Тип:", reply_markup=type_kb())
    await state.set_state(Morg.type)

@dp.callback_query(F.data.in_(["type_std", "type_non"]))
async def morg_type(cb: types.CallbackQuery, state: FSMContext):
    t = "Стандарт" if cb.data == "type_std" else "Не стандарт"
    await state.update_data(body_type=t)
    await cb.message.edit_text(f"Тип: {t}\n\nИсточник:", reply_markup=source_kb())
    await cb.answer()
    await state.set_state(Morg.source)

@dp.callback_query(F.data.in_(["src_dep", "src_amb"]))
async def morg_source(cb: types.CallbackQuery, state: FSMContext):
    src = "Отделение" if cb.data == "src_dep" else "Амбулаторно"
    await state.update_data(source=src)
    data = await state.get_data()
    body = {"surname": data["surname"], "type": data["body_type"], "source": src, "paid": None, "org": ""}
    current_shift["bodies"].append(body)
    n = len(current_shift["bodies"])
    await cb.message.edit_text(f"✅ {body['surname']} ({body['type']}, {src})\nТел: {n}")
    await cb.answer()
    await cb.message.answer("Следующая фамилия (или 🔒):")
    await state.set_state(Morg.surname)

@dp.callback_query(F.data.startswith("pay_"))
async def toggle_pay(cb: types.CallbackQuery, state: FSMContext):
    i = int(cb.data.split("_")[1])
    data = await state.get_data()
    bodies = data.get("bodies", [])
    if i >= len(bodies): return
    bodies[i]["paid"] = not bodies[i].get("paid", False)
    await state.update_data(bodies=bodies)
    await cb.message.edit_reply_markup(reply_markup=pay_kb(bodies))
    await cb.answer()

@dp.callback_query(F.data == "calc")
async def calc_shift(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    bodies = data.get("bodies", [])
    # Пропускаем проверку орг для упрощения (как в v4.0)
    # Если нужна логика с org - добавить её сюда
    await cb.answer()
    await show_calc(cb.message, bodies, state)

@dp.message(Morg.org)
async def morg_org(m: types.Message, state: FSMContext):
    await m.answer("Логика орг не активна в этом режиме.")

async def show_calc(m, bodies, state):
    if not current_shift["date"]: current_shift["date"] = datetime.now()
    
    loc = current_shift.get("location", "perv")
    loc_name = "Первомайская 13" if loc == "perv" else "Мира 11"
    
    san = 0; trn = 0
    txt = f"📊 СМЕНА {loc_name} | {current_shift['date'].strftime('%d.%m.%Y')}\n"
    for i, b in enumerate(bodies, 1):
        if b.get("paid"):
            s = 6500 if b["type"]=="Стандарт" else 8000
            t = 1500 if b["type"]=="Стандарт" else 2000
            san += s; trn += t
            txt += f"{i}. {b['surname']} — {s}\n"
    txt += f"\n━━━━━━━━━━\n🧑‍⚕️ {san}₽\n🚚 {trn}₽"
    
    # Сохранение в папку морга
    lines = ["Фамилия,Тип,Источник,Оплачено,Организация"]
    for b in bodies:
        lines.append(f"{b['surname']},{b['type']},{b['source']},{'ДА' if b['paid'] else 'НЕТ'},")
    
    ym = current_shift["date"].strftime("%Y-%m")
    d = current_shift["date"].strftime("%Y-%m-%d")
    github_upload(f"morg/{loc}/{ym}/{d}.csv", "\n".join(lines))
    
    txt += "\n✅ GitHub"
    await m.answer(txt)
    current_shift["date"] = None
    current_shift["location"] = None
    current_shift["bodies"] = []
    await state.clear()

# ============================================================
# ЗАПУСК
# ============================================================

@dp.errors()
async def errors_handler(e):
    logger.error(f"Error: {e}")
    return True

async def on_startup():
    url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
    if not url and host: url = f"https://{host}"
    if not url:
        logger.error("RENDER_EXTERNAL_URL не задан!")
        return
    secret = WEBHOOK_SECRET if WEBHOOK_SECRET else None
    try:
        await bot.set_webhook(f"{url}{WEBHOOK_PATH}", secret_token=secret)
        logger.info(f"Webhook: {url}{WEBHOOK_PATH}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    
    logger.info(f"Пользователи: {len(users_cache)}")
    for uid, info in users_cache.items():
        logger.info(f"  {uid} -> {info['role']} ({info['name']})")

def main():
    dp.startup.register(on_startup)
    app = web.Application()
    app.router.add_get("/health", lambda r: web.Response(text="OK"))
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    SimpleRequestHandler(dp, bot, secret_token=WEBHOOK_SECRET if WEBHOOK_SECRET else None).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    port = int(os.getenv("PORT", 10000))
    logger.info(f"Запуск на порту {port}")
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
