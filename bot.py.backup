"""
Telegram-бот для учёта морга и ритуальных услуг
Версия: 7.0 — Упрощение, Мира11 расходы, Роли v2
"""

import os, logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                            KeyboardButton)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv
from github import Github, GithubException

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
    logger.error("BOT_TOKEN не найден!"); exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# GitHub
repo = None
if GITHUB_TOKEN and REPO_NAME:
    try:
        repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        logger.info(f"GitHub: {REPO_NAME}")
    except Exception as e:
        logger.error(f"GitHub: {e}")

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
# Тестовые роли (для отладки)
test_roles = {}

# Раздельные смены и конфиги
shifts = {
    "perv": {"date": None, "bodies": []},
    "mira": {"date": None, "bodies": []},
}

# Список кнопок меню, чтобы не попадали в ввод данных
MENU_BTNS = [
    "➕ Добавить тело", "📋 Смена за сегодня",
    "🕯️ Ритуал", "🚕 Водителю", "📊 Отчёт", "👥 Пользователи"
]

MORG_CONFIG = {
    "perv": {
        "name": "Первомайская 13",
        "std_san": 5500, "std_trn": 0, "std_inc": 8000,
        "nstd_san": 8000, "nstd_trn": 0, "nstd_inc": 10000,
    },
    "mira": {
        "name": "Мира 11",
        "std_san": 6500, "std_trn": 1500, "std_inc": 0,
        "nstd_san": 8000, "nstd_trn": 2000, "nstd_inc": 0,
    },
}

users_cache = {
    747600306: {"role": "super_admin", "name": "Евсеев", "location": "mira"},
    7819002363: {"role": "manager", "name": "Семенов", "location": "perv"},
    387529965: {"role": "agent", "name": "Жуков", "location": None},
}

last_orders = []

# ============================================================
# FSM
# ============================================================
class Morg(StatesGroup):
    location = State()
    surname = State()
    type = State()
    source = State()
    closing = State()
    org = State()
    mira_salary = State()  # ЗП для Мира 11
    mira_bonus = State()   # Бонус для Мира 11

class Ritual(StatesGroup):
    event_date = State()
    customer = State()
    phone = State()
    deceased = State()
    coffin = State()
    temple = State()
    cemetery = State()
    urn_type = State()
    urn_color = State()
    extras = State()
    temple_cremation = State()

# ============================================================
# GITHUB
# ============================================================
def gh_upload(path, content, msg="Авто"):
    if not repo: 
        try:
            d = os.path.dirname(path)
            if d: os.makedirs(d, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f: f.write(content)
            logger.info(f"Local write: {path}")
        except: pass
        return False
    try:
        try:
            f = repo.get_contents(path)
            repo.update_file(f.path, msg, content, f.sha, branch="main")
        except: repo.create_file(path, msg, content, branch="main")
        return True
    except Exception as e:
        logger.error(f"GH: {e}"); return False

def gh_read(path):
    if not repo: 
        try:
            with open(path, 'r', encoding='utf-8') as f: return f.read()
        except: return None
    try: return repo.get_contents(path).decoded_content.decode('utf-8')
    except: return None

def read_file(path):
    return gh_read(path)

def gh_append(path, row, headers):
    if not repo: 
        try:
            d = os.path.dirname(path)
            if d: os.makedirs(d, exist_ok=True)
            if not os.path.exists(path):
                with open(path, 'w', encoding='utf-8') as f: f.write(",".join(headers)+"\n")
            with open(path, 'a', encoding='utf-8') as f: f.write(row+"\n")
            return True
        except: return False
    try:
        ex = ""
        try: ex = repo.get_contents(path).decoded_content.decode('utf-8')
        except: ex = ",".join(headers) + "\n"
        new = ex + row + "\n"
        try:
            f = repo.get_contents(path)
            repo.update_file(f.path, f"Add {path}", new, f.sha, branch="main")
        except: repo.create_file(path, f"Create {path}", new, branch="main")
        return True
    except: return False

# ============================================================
# КЛАВИАТУРЫ
# ============================================================
def kb_menu(role):
    b = ReplyKeyboardBuilder()
    if role == "super_admin":
        b.row(KeyboardButton(text="➕ Добавить тело"), KeyboardButton(text="📋 Смена за сегодня"))
        b.row(KeyboardButton(text="🕯️ Ритуал"), KeyboardButton(text="🚕 Водителю"))
        b.row(KeyboardButton(text="📊 Отчёт"), KeyboardButton(text="👥 Пользователи"))
    elif role == "manager":
        b.row(KeyboardButton(text="➕ Добавить тело"), KeyboardButton(text="📋 Смена за сегодня"))
        b.row(KeyboardButton(text="🕯️ Ритуал"), KeyboardButton(text="🚕 Водителю"))
    elif role == "agent":
        b.row(KeyboardButton(text="🕯️ Ритуал"), KeyboardButton(text="🚕 Водителю"))
    return b.as_markup(resize_keyboard=True)

def kb_locations():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏥 Первомайская 13", callback_data="loc_perv")],
        [InlineKeyboardButton(text="🏥 Мира 11", callback_data="loc_mira")]
    ])

def kb_type():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Стандарт", callback_data="type_std")],
        [InlineKeyboardButton(text="Не стандарт", callback_data="type_non")]
    ])

def kb_source():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отделение", callback_data="src_dep")],
        [InlineKeyboardButton(text="Амбулаторно", callback_data="src_amb")]
    ])

def kb_pay(bodies):
    btns = []
    for i, b in enumerate(bodies):
        s = "✅" if b.get("paid") else "❌"
        btns.append([InlineKeyboardButton(text=f"{s} {b['surname']}", callback_data=f"pay_{i}")])
    btns.append([InlineKeyboardButton(text="💰 РАССЧИТАТЬ", callback_data="calc")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def kb_ritual_type():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚰️ Похороны", callback_data="ord_funeral")],
        [InlineKeyboardButton(text="🔥 Кремация", callback_data="ord_cremation")]
    ])

def kb_urn():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Картон", callback_data="urn_cardboard")],
        [InlineKeyboardButton(text="🏺 Пластик", callback_data="urn_plastic")]
    ])

def kb_color():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚪ Белый", callback_data="col_white"), InlineKeyboardButton(text="⚫ Чёрный", callback_data="col_black")],
        [InlineKeyboardButton(text="🟢 Зелёный", callback_data="col_green"), InlineKeyboardButton(text="🔵 Синий", callback_data="col_blue")]
    ])

def kb_extras(sel=None):
    if sel is None: sel = []
    b = InlineKeyboardBuilder()
    for k,v in {"box_pol":"Гроб полированный","large":"Крупное тело","hall":"Зал+отпевание","urgent":"Срочная"}.items():
        mark = "✅" if k in sel else "⬜"
        b.row(InlineKeyboardButton(text=f"{mark} {v}", callback_data=f"extra_{k}"))
    b.row(InlineKeyboardButton(text="ДАЛЕЕ ➡️", callback_data="extra_done"))
    return b.as_markup()

def kb_order_select(orders):
    b = InlineKeyboardBuilder()
    for i, o in enumerate(orders):
        label = "🔥" if o["type"]=="cremation" else "⚰️"
        b.row(InlineKeyboardButton(text=f"{label} {o['deceased']}", callback_data=f"sel_ord_{i}"))
    return b.as_markup()

def kb_order_actions(order_idx):
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🚕 Водителю", callback_data=f"send_driver_{order_idx}"))
    b.row(InlineKeyboardButton(text="🔥 Крематорий", callback_data=f"send_crem_{order_idx}"))
    return b.as_markup()

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def find_user_loc(uid):
    return users_cache.get(uid, {}).get("location")

def find_active_shift(uid):
    loc = find_user_loc(uid)
    if loc and shifts[loc]["date"]: return loc, shifts[loc]
    # Если у юзера нет локации, ищем любую активную
    for l, s in shifts.items():
        if s["date"]: return l, s
    return None, None

# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================
@dp.message(F.text == "/start")
async def cmd_start(m: types.Message):
    uid = m.from_user.id
    if uid not in users_cache:
        await m.answer(f"⚠️ Вас нет в списке. ID: {uid}"); return
    
    role = test_roles.get(uid, users_cache[uid]["role"])
    name = users_cache[uid]["name"]
    
    # Сброс смен при старте
    for s in shifts.values(): s["date"]=None; s["bodies"]=[]

    test_note = f"\n🧪 Тест: {role}" if role != users_cache[uid]["role"] else ""
    await m.answer(f"👋 {name}{test_note}\n\n📋 Меню:", reply_markup=kb_menu(role))

@dp.message(F.text == "/role")
async def change_role(m: types.Message):
    uid = m.from_user.id
    # Проверка прав
    r = users_cache.get(uid, {}).get("role")
    if r != "super_admin":
        await m.answer("⚠️ Только super_admin"); return

    parts = m.text.split()
    if len(parts) < 2 or parts[1] not in ("super_admin", "manager", "agent"):
        await m.answer("Использование: /role <role>"); return
    
    test_roles[uid] = parts[1]
    await m.answer(f"🧪 Роль изменена на {parts[1]}. Нажмите /start")

@dp.message(F.text == "👥 Пользователи")
async def users_menu(m: types.Message):
    if users_cache.get(m.from_user.id, {}).get("role") != "super_admin": return
    await m.answer("Управление пользователями через файл users.csv")

# ============================================================
# МОРГ: ДОБАВЛЕНИЕ
# ============================================================
@dp.message(F.text == "➕ Добавить тело")
async def add_body(m: types.Message, state: FSMContext):
    loc, shift = find_active_shift(m.from_user.id)
    
    # Если смены нет — выбираем морг
    if not loc:
        await m.answer("Выбери морг для начала смены:", reply_markup=kb_locations())
        await state.set_state(Morg.location)
        return

    # Если смена есть — сразу фамилию
    await m.answer("Фамилия:")
    await state.update_data(loc=loc)
    await state.set_state(Morg.surname)

@dp.callback_query(F.data.in_(["loc_perv","loc_mira"]))
async def morg_location(cb: types.CallbackQuery, state: FSMContext):
    loc = "perv" if cb.data=="loc_perv" else "mira"
    shifts[loc]["date"] = datetime.now()
    shifts[loc]["bodies"] = []
    
    await state.update_data(loc=loc)
    await cb.message.edit_text(f"🏥 {MORG_CONFIG[loc]['name']}\n\nФамилия:")
    await cb.answer()
    await state.set_state(Morg.surname)

@dp.message(Morg.surname, ~F.text.in_(MENU_BTNS))
async def morg_surname(m: types.Message, state: FSMContext):
    s = m.text.strip().upper()
    if not s: await m.answer("⚠️ Введи фамилию:"); return
    await state.update_data(surname=s)
    await m.answer("Тип:", reply_markup=kb_type())
    await state.set_state(Morg.type)

@dp.callback_query(F.data.in_(["type_std","type_non"]))
async def morg_type(cb: types.CallbackQuery, state: FSMContext):
    t = "Стандарт" if cb.data=="type_std" else "Не стандарт"
    await state.update_data(body_type=t)
    await cb.message.edit_text(f"Тип: {t}\n\nИсточник:", reply_markup=kb_source())
    await cb.answer()
    await state.set_state(Morg.source)

@dp.callback_query(F.data.in_(["src_dep","src_amb"]))
async def morg_source(cb: types.CallbackQuery, state: FSMContext):
    src = "Отделение" if cb.data=="src_dep" else "Амбулаторно"
    await state.update_data(source=src)
    data = await state.get_data()
    loc = data.get("loc")
    if not loc: return
    
    body = {"surname": data["surname"], "type": data["body_type"], "source": src, "paid": None, "org": ""}
    shifts[loc]["bodies"].append(body)
    n = len(shifts[loc]["bodies"])
    
    await cb.message.edit_text(f"✅ {body['surname']} ({src})\nТел: {n}")
    await cb.answer()
    await cb.message.answer("Следующая фамилия (или 📋 для закрытия):")
    await state.set_state(Morg.surname)

# ============================================================
# МОРГ: ЗАКРЫТИЕ
# ============================================================
@dp.message(F.text == "📋 Смена за сегодня")
async def shift_today(m: types.Message, state: FSMContext):
    loc, shift = find_active_shift(m.from_user.id)
    
    # Если смена активна — закрываем
    if loc and shift["bodies"]:
        await state.update_data(bodies=shift["bodies"].copy(), loc=loc)
        await m.answer(f"📋 {MORG_CONFIG[loc]['name']} — Отметка оплаты:", reply_markup=kb_pay(shift["bodies"]))
        await state.set_state(Morg.closing)
        return

    # Если смены нет — показываем отчет из файла
    await show_daily_report(m)

async def show_daily_report(m: types.Message):
    today = datetime.now(); ym=today.strftime("%Y-%m"); d=today.strftime("%Y-%m-%d")
    txt = ""
    
    for loc, cfg in MORG_CONFIG.items():
        content = read_file(f"morg/{loc}/{ym}/{d}.csv")
        lb=0; ls=0; lt=0; agent_sal=0; agent_bonus=0
        
        if content:
            # Парсим тела
            lines = content.strip().split("\n")
            if len(lines) > 1:
                for line in lines[1:]:
                    p=line.split(",")
                    if len(p) < 5: continue
                    lb += 1
                    if p[3].upper() == "ДА":
                        std = p[1] == "Стандарт"
                        ls += cfg["std_san"] if std else cfg["nstd_san"]
                        lt += cfg["std_trn"] if std else cfg["nstd_trn"]

            # Парсим доп расходы для Мира 11 (если есть отдельный файл расходов или вшито)
            # Упрощение: читаем доп расходы из mira_expenses.csv если Мира
            if loc == "mira":
                exp_content = read_file(f"morg/mira/{ym}/{d}_expenses.csv")
                if exp_content:
                    try:
                        p = exp_content.strip().split(",")
                        agent_sal = int(p[0]) if p[0].isdigit() else 0
                        agent_bonus = int(p[1]) if p[1].isdigit() else 0
                    except: pass

        txt += f"🏥 {cfg['name']}\nТел: {lb}\n"
        txt += f"🧑‍⚕️ Санитары: {ls}₽\n"
        if cfg["std_trn"] > 0 or cfg["nstd_trn"] > 0:
            txt += f"🚚 Перевозка: {lt}₽\n"
        
        if agent_sal or agent_bonus:
            txt += f"👤 Агент з/п: {agent_sal}₽"
            if agent_bonus: txt += f" + {agent_bonus}₽ бонус"
            txt += "\n"
        
        txt += f"{'─'*15}\n\n"

    await m.answer(txt if txt else "Смен за сегодня нет.")

@dp.callback_query(F.data.startswith("pay_"))
async def toggle_pay(cb: types.CallbackQuery, state: FSMContext):
    i = int(cb.data.split("_")[1])
    data = await state.get_data(); bodies = data.get("bodies",[])
    if i >= len(bodies): return
    bodies[i]["paid"] = not bodies[i].get("paid", False)
    await state.update_data(bodies=bodies)
    await cb.message.edit_reply_markup(reply_markup=kb_pay(bodies))
    await cb.answer()

@dp.callback_query(F.data == "calc")
async def calc_shift(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    bodies = data.get("bodies", [])
    loc = data.get("loc")

    # Проверка "кто вывез"
    for i, b in enumerate(bodies):
        if not b.get("paid") and not b.get("org"):
            await state.update_data(bodies=bodies, idx=i)
            await cb.message.edit_text(f"Кто вывез {b['surname']}?")
            await cb.answer()
            await state.set_state(Morg.org)
            return

    # Если Мира 11 — спрашиваем ЗП
    if loc == "mira":
        await state.update_data(bodies=bodies)
        await cb.message.edit_text("З/П агента (число):")
        await cb.answer()
        await state.set_state(Morg.mira_salary)
        return

    # Иначе сразу расчёт
    await cb.answer()
    await finish_calc(cb.message, bodies, loc, 0, 0)

@dp.message(Morg.org, ~F.text.in_(MENU_BTNS))
async def morg_org(m: types.Message, state: FSMContext):
    data = await state.get_data(); bodies=data.get("bodies",[]); i=data.get("idx",0)
    bodies[i]["org"] = m.text.strip().upper()
    await state.update_data(bodies=bodies)
    
    for j, b in enumerate(bodies):
        if not b.get("paid") and not b.get("org"):
            await state.update_data(idx=j)
            await m.answer(f"Кто вывез {b['surname']}?"); return
    
    loc = data.get("loc")
    if loc == "mira":
        await m.answer("З/П агента (число):")
        await state.set_state(Morg.mira_salary)
        return

    await finish_calc(m, bodies, loc, 0, 0)

@dp.message(Morg.mira_salary, ~F.text.in_(MENU_BTNS))
async def mira_salary_input(m: types.Message, state: FSMContext):
    try: val = int(m.text.strip())
    except: await m.answer("Введи число!"); return
    await state.update_data(mira_salary=val)
    await m.answer("Бонус (число или 0):")
    await state.set_state(Morg.mira_bonus)

@dp.message(Morg.mira_bonus, ~F.text.in_(MENU_BTNS))
async def mira_bonus_input(m: types.Message, state: FSMContext):
    try: val = int(m.text.strip())
    except: await m.answer("Введи число!"); return
    
    data = await state.get_data()
    loc = data.get("loc")
    bodies = data.get("bodies", [])
    
    # Сохраняем расходы в файл для отчета
    today = datetime.now(); ym=today.strftime("%Y-%m"); d=today.strftime("%Y-%m-%d")
    gh_upload(f"morg/{loc}/{ym}/{d}_expenses.csv", f"{val},{data.get('mira_bonus',0)}")
    
    await finish_calc(m, bodies, loc, val, data.get('mira_bonus', 0))

async def finish_calc(m, bodies, loc, salary=0, bonus=0):
    cfg = MORG_CONFIG[loc]
    san=0; trn=0; inc=0

    txt = f"📊 {cfg['name']} | {datetime.now().strftime('%d.%m.%Y')}\n"
    txt += f"Всего тел: {len(bodies)}\n\n"

    # Группировка
    stat = [b for b in bodies if b["source"]=="Отделение"]
    amb = [b for b in bodies if b["source"]=="Амбулаторно"]

    for title, lst in [("🏥 СТАЦИОНАР", stat), ("🚗 АМБУЛАТОРНО", amb)]:
        if lst:
            txt += f"{title}:\n"
            for i, b in enumerate(lst, 1):
                if b.get("paid"):
                    std = b["type"] == "Стандарт"
                    s = cfg["std_san"] if std else cfg["nstd_san"]
                    t = cfg["std_trn"] if std else cfg["nstd_trn"]
                    i_val = cfg["std_inc"] if std else cfg["nstd_inc"]
                    san+=s; trn+=t; inc+=i_val
                    txt += f"{i}. {b['surname']} — {s}\n"
                else:
                    txt += f"{i}. {b['surname']} → {b.get('org','')}\n"

    txt += f"\n🧑‍⚕️ Санитары: {san}₽\n"
    if cfg["std_trn"] > 0: txt += f"🚚 Перевозка: {trn}₽\n"

    if loc == "mira":
        # Для Мира 11 выводим только расходы, прибыли нет
        if salary or bonus:
            txt += f"👤 Агент: {salary}₽"
            if bonus: txt += f" + {bonus}₽ бонус"
            txt += "\n"
    else:
        # Для Первомайской считаем прибыль
        if inc > 0:
            txt += f"💰 Доход: {inc}₽\n"
            txt += f"✅ Прибыль: {inc - san - trn}₽\n"

    # Сохранение
    lines = ["Фамилия,Тип,Источник,Оплачено,Организация"]
    for b in bodies:
        lines.append(f"{b['surname']},{b['type']},{b['source']},{'ДА' if b['paid'] else 'НЕТ'},{b.get('org','')}")
    
    today = datetime.now(); ym=today.strftime("%Y-%m"); d=today.strftime("%Y-%m-%d")
    ok = gh_upload(f"morg/{loc}/{ym}/{d}.csv", "\n".join(lines))
    txt += "\n✅ Сохранено" if ok else "\n⚠️ Локально"

    shifts[loc]["date"] = None
    shifts[loc]["bodies"] = []
    await m.answer(txt)

# ============================================================
# РИТУАЛ
# ============================================================
@dp.message(F.text == "🕯️ Ритуал")
async def ritual_menu(m: types.Message):
    await m.answer("Тип заказа:", reply_markup=kb_ritual_type())

@dp.callback_query(F.data.in_(["ord_funeral","ord_cremation"]))
async def start_order(cb: types.CallbackQuery, state: FSMContext):
    t = "funeral" if cb.data=="ord_funeral" else "cremation"
    await state.update_data(type=t, extras=[])
    await cb.message.edit_text("Дата (дд.мм.гггг):")
    await cb.answer()
    await state.set_state(Ritual.event_date)

# Поля ввода (общие)
@dp.message(Ritual.event_date, ~F.text.in_(MENU_BTNS))
async def r_date(m: types.Message, state: FSMContext):
    await state.update_data(event_date=m.text.strip())
    await m.answer("ФИО заказчика:"); await state.set_state(Ritual.customer)

@dp.message(Ritual.customer, ~F.text.in_(MENU_BTNS))
async def r_customer(m: types.Message, state: FSMContext):
    s=m.text.strip().upper()
    if not s: await m.answer("⚠️ Введи ФИО:"); return
    await state.update_data(customer=s)
    await m.answer("Телефон:"); await state.set_state(Ritual.phone)

@dp.message(Ritual.phone, ~F.text.in_(MENU_BTNS))
async def r_phone(m: types.Message, state: FSMContext):
    await state.update_data(phone=m.text.strip())
    await m.answer("ФИО усопшего + адрес:"); await state.set_state(Ritual.deceased)

@dp.message(Ritual.deceased, ~F.text.in_(MENU_BTNS))
async def r_deceased(m: types.Message, state: FSMContext):
    await state.update_data(deceased=m.text.strip().upper())
    data = await state.get_data()
    if data["type"]=="funeral":
        await m.answer("Гроб:"); await state.set_state(Ritual.coffin)
    else:
        await m.answer("Урна:", reply_markup=kb_urn())
        await state.set_state(Ritual.urn_type)

@dp.message(Ritual.coffin, ~F.text.in_(MENU_BTNS))
async def r_coffin(m: types.Message, state: FSMContext):
    await state.update_data(coffin=m.text.strip().upper())
    await m.answer("Храм:"); await state.set_state(Ritual.temple)

@dp.message(Ritual.temple, ~F.text.in_(MENU_BTNS))
async def r_temple(m: types.Message, state: FSMContext):
    await state.update_data(temple=m.text.strip().upper())
    await m.answer("Кладбище:"); await state.set_state(Ritual.cemetery)

@dp.message(Ritual.cemetery, ~F.text.in_(MENU_BTNS))
async def r_cemetery(m: types.Message, state: FSMContext):
    await state.update_data(cemetery=m.text.strip().upper())
    await save_order(m, state)

@dp.message(Ritual.temple_cremation, ~F.text.in_(MENU_BTNS))
async def crem_temple(m: types.Message, state: FSMContext):
    await state.update_data(temple=m.text.strip().upper(), cemetery="Крематорий")
    await save_order(m, state)

# Кремация
@dp.callback_query(F.data.in_(["urn_cardboard","urn_plastic"]))
async def urn_sel(cb: types.CallbackQuery, state: FSMContext):
    urn = "Картон" if cb.data=="urn_cardboard" else "Пластик"
    await state.update_data(urn_type=urn)
    await cb.answer()
    if urn=="Пластик":
        await cb.message.edit_text("Цвет:", reply_markup=kb_color())
        await state.set_state(Ritual.urn_color)
    else:
        await cb.message.edit_text("Доп. услуги:")
        await state.update_data(extras=[])
        await cb.message.answer("Выбери:", reply_markup=kb_extras([]))
        await state.set_state(Ritual.extras)

@dp.callback_query(F.data.startswith("col_"))
async def col_sel(cb: types.CallbackQuery, state: FSMContext):
    cols={"col_white":"Белый","col_black":"Чёрный","col_green":"Зелёный","col_blue":"Синий"}
    await state.update_data(urn_color=cols[cb.data])
    await state.set_state(Ritual.extras)
    await cb.message.edit_text(f"Цвет: {cols[cb.data]}\nДоп. услуги:")
    await cb.message.answer("Выбери:", reply_markup=kb_extras([]))
    await cb.answer()

@dp.callback_query(F.data.startswith("extra_"))
async def extras_hdl(cb: types.CallbackQuery, state: FSMContext):
    key=cb.data.split("_")[1]; data=await state.get_data(); extras=data.get("extras",[])
    if key=="done":
        if "hall" in extras:
            await state.update_data(temple="Зал отпевания", cemetery="Крематорий")
            await cb.answer(); await save_order(cb.message, state)
        else:
            await cb.answer(); await cb.message.answer("Храм:")
            await state.set_state(Ritual.temple_cremation)
        return
    if key in extras: extras.remove(key)
    else: extras.append(key)
    await state.update_data(extras=extras)
    try: await cb.message.edit_reply_markup(reply_markup=kb_extras(extras))
    except: pass
    await cb.answer()

# Сохранение заказа
async def save_order(m, state: FSMContext):
    data = await state.get_data(); now=datetime.now().strftime("%Y-%m-%d %H:%M")
    t=data["type"]; details=""; extras="; ".join(data.get("extras",[]))
    if t=="funeral": details=data.get("coffin","")
    else:
        urn=data.get("urn_type","")
        if urn=="Пластик": urn+=f" ({data.get('urn_color','')})"
        details=urn

    row=f"{now},{data['event_date']},{t},{data['customer']},{data['phone']},{data['deceased']},{details},{extras},{data.get('temple','')},{data['cemetery']}"
    gh_append("ritual/orders.csv",row,"Дата_записи,Дата_события,Тип,Заказчик,Тел,Усопший,Детали,Допы,Храм,Кладбище")

    route = build_route(data)
    crem_card = build_crem_card(data) if t=="cremation" else None

    global last_orders
    last_orders.append({"deceased": data["deceased"], "type": t, "route": route, "crem_card": crem_card, "date": data.get("event_date","")})

    txt = "✅ Заказ сохранён\n\n"
    txt += "━━ 📋 ВОДИТЕЛЮ ━━\n" + route
    if crem_card: txt += "\n\n━━ 🔥 КРЕМАТОРИЙ ━━\n" + crem_card
    txt += "\n\nИспользуй 🚕 Водителю для отправки"
    await m.answer(txt); await state.clear()

def build_route(data):
    t=data["type"]
    txt=f"🚕 ЗАКАЗ ВОДИТЕЛЮ\nДата: {data.get('event_date','')}\nТип: {'Похороны' if t=='funeral' else 'Кремация'}\n"
    txt+=f"Усопший: {data['deceased']}\n"
    if t=="funeral": txt+=f"Гроб: {data.get('coffin','')}\nХрам: {data.get('temple','')}\nКладбище: {data.get('cemetery','')}\n"
    else:
        if "hall" in data.get("extras",[]): txt+="Кладбище: Крематорий\n"
        else: txt+=f"Храм: {data.get('temple','')}\nКладбище: Крематорий\n"
    txt+=f"☎️ {data['phone']}"
    return txt

def build_crem_card(data):
    urn=data.get('urn_type','')
    if urn=='Пластик': urn+=f" ({data.get('urn_color','')})"
    em={"box_pol":"Гроб полированный","large":"Крупное тело","hall":"Зал+отпевание","urgent":"Срочная"}
    er=[em.get(e,e) for e in data.get("extras",[])]
    et="; ".join(er) if er else "Нет"
    return f"🔥 КРЕМАЦИЯ\nДата: {data.get('event_date','')}\nУсопший: {data['deceased']}\nУрна: {urn}\nДопы: {et}\n\nВсе стандартно, оплата наличными, оформлю в день кремации."

# ============================================================
# ВОДИТЕЛЬ И ОТЧЁТЫ
# ============================================================
@dp.message(F.text == "🚕 Водителю")
async def driver_route(m: types.Message, state: FSMContext):
    if not last_orders: await m.answer("⚠️ Нет заказов."); return
    if len(last_orders)==1:
        await m.answer(f"📋 {last_orders[0]['deceased']}", reply_markup=kb_order_actions(0))
        return
    await m.answer("Выбери заказ:", reply_markup=kb_order_select(last_orders))

@dp.callback_query(F.data.startswith("sel_ord_"))
async def select_order(cb: types.CallbackQuery, state: FSMContext):
    i = int(cb.data.split("_")[2])
    o = last_orders[i]
    await cb.message.edit_text(f"📋 {o['deceased']}", reply_markup=kb_order_actions(i))
    await cb.answer()

@dp.callback_query(F.data.startswith("send_driver_"))
async def send_driver(cb: types.CallbackQuery):
    i = int(cb.data.split("_")[2])
    if i < len(last_orders): await cb.message.answer(last_orders[i]["route"])
    await cb.answer("Скопировано")

@dp.callback_query(F.data.startswith("send_crem_"))
async def send_crem(cb: types.CallbackQuery):
    i = int(cb.data.split("_")[2])
    if i < len(last_orders):
        c = last_orders[i].get("crem_card")
        if c: await cb.message.answer(c)
        else: await cb.message.answer("⚠️ Не кремация")
    await cb.answer("Скопировано")

@dp.message(F.text == "📊 Отчёт")
async def report_menu(m: types.Message):
    # Простая статистика за неделю
    today = datetime.now(); week = today - timedelta(days=7)
    txt = "📊 ОТЧЁТ ЗА НЕДЕЛЮ\n\n"
    # Тут можно доработать полный обход файлов, пока заглушка
    txt += "(В разработке)"
    await m.answer(txt)

# ============================================================
# ЗАПУСК
# ============================================================
async def on_startup():
    url = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
    host = os.getenv("RENDER_EXTERNAL_HOSTNAME","")
    if not url and host: url = f"https://{host}"
    if not url: return
    
    secret = WEBHOOK_SECRET if WEBHOOK_SECRET else None
    await bot.set_webhook(f"{url}{WEBHOOK_PATH}", secret_token=secret)
    logger.info("Webhook OK")

def main():
    dp.startup.register(on_startup)
    app = web.Application()
    app.router.add_get("/health", lambda r: web.Response(text="OK"))
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    SimpleRequestHandler(dp, bot, secret_token=WEBHOOK_SECRET if WEBHOOK_SECRET else None).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

if __name__ == "__main__":
    main()