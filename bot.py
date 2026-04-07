"""
Telegram-бот для учёта морга и ритуальных услуг
Версия: 4.0 — Чистая архитектура, webhook для Render
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
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv
from github import Github, GithubException
from github.auth import Token as GithubToken

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
        github = Github(auth=GithubToken(GITHUB_TOKEN))
        repo = github.get_repo(REPO_NAME)
        logger.info(f"GitHub: {REPO_NAME}")
    except Exception as e:
        logger.error(f"GitHub ошибка: {e}")

# ============================================================
# ГЛОБАЛЬНЫЕ ДАННЫЕ
# ============================================================

current_shift = {"date": None, "bodies": []}
last_driver_route = None
current_order = {}

# ============================================================
# FSM СОСТОЯНИЯ
# ============================================================

class Morg(StatesGroup):
    surname = State()
    type = State()
    source = State()
    closing = State()
    org = State()

class Ritual(StatesGroup):
    customer = State()
    phone = State()
    deceased = State()
    coffin = State()
    temple = State()
    cemetery = State()
    salary = State()

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
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(content + "\n")
    except Exception as e:
        logger.error(f"Local save: {e}")


def github_read(path):
    if not repo:
        return None
    try:
        f = repo.get_contents(path)
        return f.decoded_content.decode('utf-8')
    except GithubException:
        return None


def read_file(path):
    """GitHub → локальный fallback"""
    content = github_read(path)
    if not content and os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except:
            pass
    return content


def save_shift(date, bodies):
    ym = date.strftime("%Y-%m")
    d = date.strftime("%Y-%m-%d")
    path = f"morg/{ym}/{d}.csv"
    lines = ["Фамилия,Тип,Источник,Оплачено,Организация"]
    for b in bodies:
        lines.append(f"{b['surname']},{b['type']},{b['source']},{'ДА' if b['paid'] else 'НЕТ'},{b.get('org','')}")
    return github_upload(path, "\n".join(lines), f"Смена {d}")


def save_order(data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    row = f"{now},{data['customer']},{data['phone']},{data['deceased']},{data['coffin']},{data['temple']},{data['cemetery']},{data['salary']}"
    return github_append("ritual/orders.csv", row,
        "Дата,Заказчик,Телефон,Усопший_адрес,Гроб,Храм,Кладбище,Зарплата_агента")


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
# СТАТИСТИКА
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
                if len(p) < 4:
                    continue
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

    r = {"orders":0,"agents":0}
    content = read_file("ritual/orders.csv")
    if content:
        for line in content.strip().split("\n")[1:]:
            p = line.split(",")
            if len(p) >= 8:
                try:
                    od = datetime.strptime(p[0], "%Y-%m-%d %H:%M")
                    if week <= od <= today:
                        r["orders"] += 1; r["agents"] += int(p[7])
                except:
                    pass
    return m, r

# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def main_kb():
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="🔄 Новая смена"), KeyboardButton(text="➕ Добавить тело"))
    b.row(KeyboardButton(text="🔒 Подвести смену"), KeyboardButton(text="🕯️ Ритуал"))
    b.row(KeyboardButton(text="🚕 Водителю"), KeyboardButton(text="📊 Отчёт"))
    return b.as_markup(resize_keyboard=True)

def type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Стандарт (8000₽)", callback_data="type_std")],
        [InlineKeyboardButton(text="Не стандарт (10000₽)", callback_data="type_non")]
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
# ГЛАВНОЕ МЕНЮ
# ============================================================

@dp.message(F.text == "/start")
async def cmd_start(m: types.Message):
    current_shift["date"] = datetime.now()
    current_shift["bodies"] = []
    await m.answer("👋 Бот для учёта морга и ритуальных услуг\n\n📋 Кнопки внизу:", reply_markup=main_kb())

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
    current_shift["date"] = datetime.now()
    current_shift["bodies"] = []
    await m.answer("📝 Новая смена\n\nФамилия:")
    await state.set_state(Morg.surname)

@dp.message(F.text == "🔒 Подвести смену")
async def close_shift(m: types.Message, state: FSMContext):
    if not current_shift["bodies"]:
        await m.answer("⚠️ Смена пуста. Добавь тела сначала.")
        return
    await state.update_data(bodies=current_shift["bodies"].copy())
    await m.answer("📋 Нажми на фамилию для переключения оплаты:", reply_markup=pay_kb(current_shift["bodies"]))
    await state.set_state(Morg.closing)

@dp.message(F.text == "🕯️ Ритуал")
async def ritual_menu(m: types.Message, state: FSMContext):
    current_order.clear()
    await m.answer("📋 Ритуальный заказ\n\nФИО заказчика:")
    await state.set_state(Ritual.customer)

@dp.message(F.text == "🚕 Водителю")
async def driver_route(m: types.Message):
    if not last_driver_route:
        await m.answer("⚠️ Нет маршрутов. Оформите заказ.")
        return
    await m.answer(last_driver_route)

@dp.message(F.text == "📊 Отчёт")
async def report_menu(m: types.Message):
    await m.answer("⏳ Загружаю...")
    mg, rt = get_weekly()
    today = datetime.now().strftime("%d.%m.%Y")
    wk = (datetime.now() - timedelta(days=7)).strftime("%d.%m.%Y")
    txt = (f"📊 ОТЧЁТ ({wk} — {today})\n\n"
           f"⚰️ МОРГ:\nВсего: {mg['total']}\n"
           f"✅ {mg['paid']} | ❌ {mg['unpaid']}\n"
           f"Доход: {mg['income']}₽\n"
           f"🧑‍⚕️ Санитары: {mg['sanitars']}₽\n"
           f"🚚 Перевозка: {mg['transport']}₽\n"
           f"💰 Прибыль: {mg['profit']}₽\n\n"
           f"🕯️ РИТУАЛ:\nЗаказов: {rt['orders']}\n"
           f"Агенты: {rt['agents']}₽")
    await m.answer(txt)

# ============================================================
# МОРГ — ДОБАВЛЕНИЕ
# ============================================================

@dp.message(Morg.surname)
async def morg_surname(m: types.Message, state: FSMContext):
    s = m.text.strip().upper()
    if not s:
        await m.answer("⚠️ Введи фамилию:")
        return
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
    await cb.message.answer("Следующая фамилия (или 🔒 Подвести смену):")
    await state.set_state(Morg.surname)

# ============================================================
# МОРГ — ЗАКРЫТИЕ
# ============================================================

@dp.callback_query(F.data.startswith("pay_"))
async def toggle_pay(cb: types.CallbackQuery, state: FSMContext):
    i = int(cb.data.split("_")[1])
    data = await state.get_data()
    bodies = data.get("bodies", [])
    if i >= len(bodies):
        await cb.answer("Ошибка")
        return
    bodies[i]["paid"] = not bodies[i].get("paid", False)
    await state.update_data(bodies=bodies)
    await cb.message.edit_reply_markup(reply_markup=pay_kb(bodies))
    await cb.answer()

@dp.callback_query(F.data == "calc")
async def calc_shift(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    bodies = data.get("bodies", [])
    for i, b in enumerate(bodies):
        if not b.get("paid") and not b.get("org"):
            await state.update_data(bodies=bodies, idx=i)
            await cb.message.edit_text(f"Кто вывез {b['surname']}? (организация):")
            await cb.answer()
            await state.set_state(Morg.org)
            return
    await cb.answer()
    await show_calc(cb.message, bodies, state)

@dp.message(Morg.org)
async def org_input(m: types.Message, state: FSMContext):
    data = await state.get_data()
    bodies = data.get("bodies", [])
    i = data.get("idx", 0)
    bodies[i]["org"] = m.text.strip().upper()
    await state.update_data(bodies=bodies)
    for j, b in enumerate(bodies):
        if not b.get("paid") and not b.get("org"):
            await state.update_data(idx=j)
            await m.answer(f"Кто вывез {b['surname']}? (организация):")
            return
    await show_calc(m, bodies, state)

async def show_calc(m, bodies, state):
    if not current_shift["date"]:
        current_shift["date"] = datetime.now()

    stat = [b for b in bodies if b["source"] == "Отделение"]
    amb = [b for b in bodies if b["source"] == "Амбулаторно"]
    san = 0; trn = 0
    txt = f"📊 СМЕНА {current_shift['date'].strftime('%d.%m.%Y')}"

    for title, lst, icon in [("🏥 СТАЦИОНАР", stat, ""), ("🚗 АМБУЛАТОРНО", amb, "")]:
        if lst:
            txt += f"\n\n{title}:\n"
            for i, b in enumerate(lst, 1):
                if b.get("paid"):
                    s = 6500 if b["type"] == "Стандарт" else 8000
                    t = 1500 if b["type"] == "Стандарт" else 2000
                    san += s; trn += t
                    txt += f"{i}. {b['surname']} — {s}\n"
                else:
                    txt += f"{i}. {b['surname']} — 0 → {b.get('org','НЕТ')}\n"

    txt += f"\n━━━━━━━━━━━━━━━\n🧑‍⚕️ Санитары: {san}₽\n🚚 Перевозка: {trn}₽"

    ok = save_shift(current_shift["date"], bodies)
    txt += "\n\n✅ GitHub" if ok else "\n\n⚠️ Локально"
    await m.answer(txt)

    current_shift["date"] = None
    current_shift["bodies"] = []
    await state.clear()

# ============================================================
# РИТУАЛ
# ============================================================

@dp.message(Ritual.customer)
async def r_customer(m: types.Message, state: FSMContext):
    c = m.text.strip().upper()
    if not c:
        await m.answer("⚠️ Введи ФИО:")
        return
    current_order["customer"] = c
    await m.answer("Телефон:")
    await state.set_state(Ritual.phone)

@dp.message(Ritual.phone)
async def r_phone(m: types.Message, state: FSMContext):
    current_order["phone"] = m.text.strip()
    await m.answer("ФИО усопшего и адрес:")
    await state.set_state(Ritual.deceased)

@dp.message(Ritual.deceased)
async def r_deceased(m: types.Message, state: FSMContext):
    current_order["deceased"] = m.text.strip().upper()
    await m.answer("Гроб:")
    await state.set_state(Ritual.coffin)

@dp.message(Ritual.coffin)
async def r_coffin(m: types.Message, state: FSMContext):
    current_order["coffin"] = m.text.strip().upper()
    await m.answer("Храм:")
    await state.set_state(Ritual.temple)

@dp.message(Ritual.temple)
async def r_temple(m: types.Message, state: FSMContext):
    current_order["temple"] = m.text.strip().upper()
    await m.answer("Кладбище:")
    await state.set_state(Ritual.cemetery)

@dp.message(Ritual.cemetery)
async def r_cemetery(m: types.Message, state: FSMContext):
    current_order["cemetery"] = m.text.strip().upper()
    await m.answer("Зарплата агента (число):")
    await state.set_state(Ritual.salary)

@dp.message(Ritual.salary)
async def r_salary(m: types.Message, state: FSMContext):
    try:
        current_order["salary"] = int(m.text.strip())
    except ValueError:
        await m.answer("⚠️ Введи число!")
        return

    save_order(current_order)

    global last_driver_route
    parts = current_order["customer"].split()
    short = f"{parts[0]} {parts[1][0]}." if len(parts) >= 2 else current_order["customer"]

    last_driver_route = (
        "🚕 ЗАКАЗ ВОДИТЕЛЮ\n"
        f"Усопший: {current_order['deceased']}\n"
        f"Гроб: {current_order['coffin']}\n"
        f"Храм: {current_order['temple']}\n"
        f"Кладбище: {current_order['cemetery']}\n"
        f"Заказчик: {short} ☎️ {current_order['phone']}"
    )
    await m.answer(f"✅ Сохранён\n\n📋 MAX:\n\n{last_driver_route}")
    current_order.clear()
    await state.clear()

# ============================================================
# ЗАПУСК
# ============================================================

@dp.errors()
async def errors_handler(e):
    logger.error(f"Error: {e}")
    return True

async def on_startup(dp: Dispatcher):
    url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
    if not url and host:
        url = f"https://{host}"
    if not url:
        logger.error("RENDER_EXTERNAL_URL не задан!")
        return
    secret = WEBHOOK_SECRET if WEBHOOK_SECRET else None
    try:
        await dp.bot.set_webhook(f"{url}{WEBHOOK_PATH}", secret_token=secret)
        logger.info(f"Webhook: {url}{WEBHOOK_PATH}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")

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
