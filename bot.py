"""
Telegram-бот для учёта морга и ритуальных услуг
Версия: 6.1 — Роли, локации, раздельные карточки, отчёты
"""

import os, logging, asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                            KeyboardButton, ReplyKeyboardMarkup)
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
# ГЛОБАЛЬНЫЕ ДАННЫЕ
# ============================================================
# Тестовые роли (для отладки)
test_roles = {}

# Список кнопок меню, чтобы не попадали в ввод данных
MENU_BTNS = [
    "➕ Добавить тело", "🔄 Новая смена", "🔒 Подвести смену", "📋 Смена за сегодня",
    "🕯️ Ритуал", "🚕 Водителю", "📊 Отчёт", "👥 Пользователи"
]

# Раздельные смены и конфиги
shifts = {
    "perv": {"date": None, "bodies": []},
    "mira": {"date": None, "bodies": []},
}

# Конфигурация моргов
MORG_CONFIG = {
    "perv": {
        "name": "Первомайская 13",
        "standard_income": 8000,
        "standard_sanitar": 5500,
        "standard_transport": 0,
        "nonstd_income": 10000,
        "nonstd_sanitar": 8000,
        "nonstd_transport": 0,
        "has_income": True,
        "has_transport": False,
    },
    "mira": {
        "name": "Мира 11",
        "standard_income": 0,
        "standard_sanitar": 6500,
        "standard_transport": 1500,
        "nonstd_income": 0,
        "nonstd_sanitar": 8000,
        "nonstd_transport": 2000,
        "has_income": False,
        "has_transport": True,
    },
}

last_orders = []

users_cache = {
    747600306: {"role": "super_admin", "name": "Евсеев", "location": "Мира 11"},
    7819002363: {"role": "manager", "name": "Семенов", "location": "Первомайская 13"},
    387529965: {"role": "agent", "name": "Жуков", "location": ""},
}

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
    if not repo: _local_save(path, content); return False
    try:
        try:
            f = repo.get_contents(path)
            repo.update_file(f.path, msg, content, f.sha, branch="main")
        except: repo.create_file(path, msg, content, branch="main")
        return True
    except Exception as e:
        logger.error(f"GH: {e}"); _local_save(path, content); return False

def _local_save(path, content):
    try:
        d = os.path.dirname(path)
        if d: os.makedirs(d, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f: f.write(content + "\n")
    except Exception as e: logger.error(f"Local: {e}")

def gh_read(path):
    if not repo: return None
    try: return repo.get_contents(path).decoded_content.decode('utf-8')
    except: return None

def read_file(path):
    c = gh_read(path)
    if not c and os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f: return f.read()
        except: pass
    return c

def gh_append(path, row, headers):
    if not repo: _local_save(path, row); return False
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
    except: _local_save(path, row); return False

# ============================================================
# СТАТИСТИКА
# ============================================================
def get_weekly():
    today = datetime.now(); week = today - timedelta(days=7)
    m = {"total":0,"paid":0,"unpaid":0,"income":0,"sanitars":0,"transport":0,"profit":0}
    for i in range(8):
        d = week + timedelta(days=i)
        for loc,cfg in MORG_CONFIG.items():
            content = read_file(f"morg/{loc}/{d.strftime('%Y-%m')}/{d.strftime('%Y-%m-%d')}.csv")
            if content:
                for line in content.strip().split("\n")[1:]:
                    p = line.split(",")
                    if len(p) < 5: continue
                    m["total"] += 1
                    std = p[1].strip()=="Стандарт"
                    if p[3].strip().upper() in ("ДА","YES","TRUE","1"):
                        m["paid"] += 1
                        s = cfg["standard_sanitar"] if std else cfg["nonstd_sanitar"]
                        t = cfg["standard_transport"] if std else cfg["nonstd_transport"]
                        inc = cfg["standard_income"] if std else cfg["nonstd_income"]
                        m["sanitars"]+=s; m["transport"]+=t; m["income"]+=inc
                    else: m["unpaid"] += 1
    m["profit"] = m["income"] - m["sanitars"] - m["transport"]
    return m

# ============================================================
# КЛАВИАТУРЫ
# ============================================================
def kb_super_admin():
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="🔄 Новая смена"), KeyboardButton(text="➕ Добавить тело"))
    b.row(KeyboardButton(text="🔒 Подвести смену"), KeyboardButton(text="📋 Смена за сегодня"))
    b.row(KeyboardButton(text="🕯️ Ритуал"))
    b.row(KeyboardButton(text="🚕 Водителю"), KeyboardButton(text="📊 Отчёт"))
    b.row(KeyboardButton(text="👥 Пользователи"))
    return b.as_markup(resize_keyboard=True)

def kb_manager():
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="🔄 Новая смена"), KeyboardButton(text="➕ Добавить тело"))
    b.row(KeyboardButton(text="🔒 Подвести смену"), KeyboardButton(text="📋 Смена за сегодня"))
    b.row(KeyboardButton(text="🕯️ Ритуал"))
    b.row(KeyboardButton(text="🚕 Водителю"), KeyboardButton(text="📊 Отчёт"))
    return b.as_markup(resize_keyboard=True)

def kb_agent():
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="🕯️ Ритуал"))
    b.row(KeyboardButton(text="🚕 Водителю"))
    return b.as_markup(resize_keyboard=True)

def get_menu(role):
    if role == "super_admin": return kb_super_admin()
    if role == "manager": return kb_manager()
    return kb_agent()

def kb_locations():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏥 Первомайская 13", callback_data="loc_perv")],
        [InlineKeyboardButton(text="🏥 Мира 11", callback_data="loc_mira")]
    ])

def kb_type():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Стандарт (8000₽)", callback_data="type_std")],
        [InlineKeyboardButton(text="Не стандарт (10000₽)", callback_data="type_non")]
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
        btns.append([InlineKeyboardButton(text=f"{s} {b['surname']} ({b['type']})", callback_data=f"pay_{i}")])
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
    """Кнопки выбора: водитель или крематорий"""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🚕 Водителю", callback_data=f"send_driver_{order_idx}"))
    b.row(InlineKeyboardButton(text="🔥 Крематорий", callback_data=f"send_crem_{order_idx}"))
    return b.as_markup()

# ============================================================
# ОБРАБОТЧИКИ — ГЛАВНОЕ МЕНЮ
# ============================================================
@dp.message(F.text == "/start")
async def cmd_start(m: types.Message):
    uid = m.from_user.id; logger.info(f"/start от {uid}")
    if uid not in users_cache:
        await m.answer(f"⚠️ Вас нет в списке. ID: {uid}"); return

    # Проверяем тестовую роль
    role = test_roles.get(uid, users_cache[uid]["role"])
    name = users_cache[uid]["name"]; loc = users_cache[uid].get("location","")

    # Сбрасываем все смены при старте
    for s in shifts.values(): s["date"]=None; s["bodies"]=[]
    loc_t = f" | {loc}" if loc else ""
    test_note = f"\n🧪 Тест-роль: {role}" if role != users_cache[uid]["role"] else ""
    await m.answer(f"👋 {name} ({role}{loc_t}){test_note}\n\n📋 Меню:", reply_markup=get_menu(role))

# Команда смены роли для тестирования (только для super_admin)
@dp.message(F.text.startswith("/role"))
async def change_role(m: types.Message):
    uid = m.from_user.id
    if users_cache.get(uid, {}).get("role") != "super_admin":
        await m.answer("⚠️ Только для super_admin"); return

    parts = m.text.split()
    if len(parts) < 2 or parts[1] not in ("super_admin", "manager", "agent"):
        await m.answer("Использование: /role <role>\nДоступно: super_admin, manager, agent"); return

    role = parts[1]
    test_roles[uid] = role
    name = users_cache[uid]["name"]
    await m.answer(f"🧪 {name}, роль изменена на: {role}\n\nНажми /start для обновления меню")

@dp.message(F.text == "👥 Пользователи")
async def users_menu(m: types.Message):
    role = test_roles.get(m.from_user.id, users_cache.get(m.from_user.id, {}).get("role"))
    if role != "super_admin": return
    await m.answer("Функция в разработке. Правьте users.csv вручную.")

# ============================================================
# МОРГ
# ============================================================
@dp.message(F.text == "➕ Добавить тело")
async def add_body(m: types.Message, state: FSMContext):
    loc = find_active_location(m.from_user.id)
    if not loc:
        await m.answer("Сначала выбери морг через 🔄 Новая смена"); return
    shift = shifts[loc]
    await state.update_data(loc=loc)  # Сохраняем loc
    if not shift["date"]:
        shift["date"]=datetime.now(); shift["bodies"]=[]
        await m.answer(f"🏥 {MORG_CONFIG[loc]['name']}\n\n📝 Смена начата\n\nФамилия:")
    else: await m.answer("Фамилия:")
    await state.set_state(Morg.surname)

def find_active_location(user_id):
    """Находит активный морг пользователя"""
    # Для тест-роли используем локацию из кэша
    role = test_roles.get(user_id)
    if role:
        for l, s in shifts.items():
            if s["date"]: return l
        return "mira"  # По умолчанию для теста
    u = users_cache.get(user_id, {})
    loc_map = {"Первомайская 13": "perv", "Мира 11": "mira"}
    if u.get("location") in loc_map:
        loc = loc_map[u["location"]]
        if shifts[loc]["date"]: return loc
    for l, s in shifts.items():
        if s["date"]: return l
    return None

@dp.message(F.text == "🔄 Новая смена")
async def new_shift(m: types.Message, state: FSMContext):
    await m.answer("Выбери морг:", reply_markup=kb_locations())
    await state.set_state(Morg.location)

@dp.callback_query(F.data.in_(["loc_perv","loc_mira"]))
async def morg_location(cb: types.CallbackQuery, state: FSMContext):
    loc = "perv" if cb.data=="loc_perv" else "mira"
    name = MORG_CONFIG[loc]["name"]
    shifts[loc]["date"]=datetime.now(); shifts[loc]["bodies"]=[]
    await state.update_data(loc=loc)  # Сохраняем loc в state
    await cb.message.edit_text(f"🏥 {name}\n\n📝 Смена начата\n\nФамилия:")
    await cb.answer(); await state.set_state(Morg.surname)

@dp.message(Morg.surname, ~F.text.in_(MENU_BTNS))
async def morg_surname(m: types.Message, state: FSMContext):
    s = m.text.strip().upper()
    if not s: await m.answer("⚠️ Введи фамилию:"); return
    # Берём loc из state (сохранён при выборе морга) или ищем активный
    data = await state.get_data()
    loc = data.get("loc") or find_active_location(m.from_user.id)
    await state.update_data(surname=s, loc=loc)
    await m.answer("Тип:", reply_markup=kb_type())
    await state.set_state(Morg.type)

@dp.callback_query(F.data.in_(["type_std","type_non"]))
async def morg_type(cb: types.CallbackQuery, state: FSMContext):
    t = "Стандарт" if cb.data=="type_std" else "Не стандарт"
    await state.update_data(body_type=t)
    await cb.message.edit_text(f"Тип: {t}\n\nИсточник:", reply_markup=kb_source())
    await cb.answer(); await state.set_state(Morg.source)

@dp.callback_query(F.data.in_(["src_dep","src_amb"]))
async def morg_source(cb: types.CallbackQuery, state: FSMContext):
    src = "Отделение" if cb.data=="src_dep" else "Амбулаторно"
    await state.update_data(source=src)
    data = await state.get_data()
    loc = data.get("loc")
    if not loc:
        await cb.answer("Ошибка: морг не выбран. Начни через 🔄 Новая смена"); return
    body = {"surname":data["surname"],"type":data["body_type"],"source":src,"paid":None,"org":""}
    shifts[loc]["bodies"].append(body)
    n = len(shifts[loc]["bodies"])
    cfg = MORG_CONFIG[loc]
    await cb.message.edit_text(f"✅ {body['surname']} ({body['type']}, {src})\nТел: {n} | {cfg['name']}")
    await cb.answer()
    await cb.message.answer("Следующая фамилия (или 🔒):")
    await state.set_state(Morg.surname)

@dp.message(F.text == "🔒 Подвести смену")
async def close_shift(m: types.Message, state: FSMContext):
    # Проверяем ВСЕ смены
    active = []
    for loc in shifts:
        s = shifts[loc]
        if s["date"] and s["bodies"]:
            active.append(loc)
            logger.info(f"Активная смена {loc}: {len(s['bodies'])} тел")
    
    logger.info(f"close_shift: active={active}")
    
    if not active:
        await m.answer("⚠️ Смена пуста или не начата. Сначала выбери 🔄 Новая смена."); return
    
    if len(active) == 1:
        loc = active[0]
        await state.update_data(bodies=shifts[loc]["bodies"].copy(), loc=loc)
        cfg = MORG_CONFIG[loc]
        await m.answer(f"📋 {cfg['name']} — Нажми на фамилию:", reply_markup=kb_pay(shifts[loc]["bodies"]))
        await state.set_state(Morg.closing)
    else:
        b = InlineKeyboardBuilder()
        for loc in active:
            b.row(InlineKeyboardButton(text=f"🏥 {MORG_CONFIG[loc]['name']}", callback_data=f"close_loc_{loc}"))
        await m.answer("Выбери морг:", reply_markup=b.as_markup())

@dp.callback_query(F.data.startswith("close_loc_"))
async def close_shift_loc(cb: types.CallbackQuery, state: FSMContext):
    loc = cb.data.split("_")[-1]
    await state.update_data(bodies=shifts[loc]["bodies"].copy(), loc=loc)
    cfg = MORG_CONFIG[loc]
    await cb.message.edit_text(f"📋 {cfg['name']} — Нажми на фамилию:", reply_markup=kb_pay(shifts[loc]["bodies"]))
    await cb.answer()
    await state.set_state(Morg.closing)

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
    data = await state.get_data(); bodies = data.get("bodies",[])
    # Проверяем неоплаченные без org
    for i, b in enumerate(bodies):
        if not b.get("paid") and not b.get("org"):
            await state.update_data(bodies=bodies, idx=i)
            await cb.message.edit_text(f"Кто вывез тело {b['surname']}? (организация):")
            await cb.answer(); await state.set_state(Morg.org)
            return
    await cb.answer(); await show_calc(cb.message, bodies, state)

@dp.message(Morg.org, ~F.text.in_(MENU_BTNS))
async def morg_org(m: types.Message, state: FSMContext):
    data = await state.get_data(); bodies = data.get("bodies",[]); i = data.get("idx",0)
    bodies[i]["org"] = m.text.strip().upper()
    await state.update_data(bodies=bodies)
    for j, b in enumerate(bodies):
        if not b.get("paid") and not b.get("org"):
            await state.update_data(idx=j)
            await m.answer(f"Кто вывез {b['surname']}? (организация):"); return
    await show_calc(m, bodies, state)

async def show_calc(m, bodies, state):
    data = await state.get_data()
    loc = data.get("loc")
    if not loc or loc not in shifts:
        # Fallback: ищем активную смену
        for l in shifts:
            if shifts[l]["date"] and shifts[l]["bodies"]:
                loc = l; break
    if not loc: return
    cfg = MORG_CONFIG[loc]; name = cfg["name"]

    stat = [b for b in bodies if b["source"]=="Отделение"]
    amb = [b for b in bodies if b["source"]=="Амбулаторно"]
    san=0; trn=0; income=0

    txt = f"📊 {name} | {shifts[loc]['date'].strftime('%d.%m.%Y')}\nВсего: {len(bodies)}\n\n"

    for title, lst in [("🏥 СТАЦИОНАР", stat), ("🚗 АМБУЛАТОРНО", amb)]:
        if lst:
            txt += f"{title}:\n"
            for i,b in enumerate(lst,1):
                if b.get("paid"):
                    if b["type"]=="Стандарт":
                        s=cfg["standard_sanitar"]; t=cfg["standard_transport"]; inc=cfg["standard_income"]
                    else:
                        s=cfg["nonstd_sanitar"]; t=cfg["nonstd_transport"]; inc=cfg["nonstd_income"]
                    san+=s; trn+=t; income+=inc
                    txt += f"{i}. {b['surname']} — {s}\n"
                else:
                    txt += f"{i}. {b['surname']} → {b.get('org','НЕ УКАЗАНО')}\n"

    txt += f"\n{'─'*20}\n🧑‍⚕️ Санитары: {san}₽"
    if cfg["has_transport"]: txt += f"\n🚚 Перевозка: {trn}₽"
    if cfg["has_income"]: txt += f"\n💰 Доход: {income}₽"

    # З/П агента из заказов за сегодня
    today_str = datetime.now().strftime("%Y-%m-%d")
    agent_sal = 0
    content = read_file("ritual/orders.csv")
    if content:
        for line in content.strip().split("\n")[1:]:
            p = line.split(",")
            if len(p)>=8 and p[0].startswith(today_str):
                try: agent_sal += int(p[7].strip())
                except: pass
    
    if agent_sal: txt += f"\n👤 Агент з/п: {agent_sal}₽"

    # Прибыль / Расход
    total_expense = san + trn + agent_sal
    if cfg["has_income"]:
        profit = income - total_expense
        txt += f"\n✅ Прибыль: {profit}₽"
    else:
        txt += f"\n💸 Расход: {total_expense}₽"

    lines = ["Фамилия,Тип,Источник,Оплачено,Организация"]
    for b in bodies:
        lines.append(f"{b['surname']},{b['type']},{b['source']},{'ДА' if b['paid'] else 'НЕТ'},{b.get('org','')}")
    ym = shifts[loc]["date"].strftime("%Y-%m")
    d = shifts[loc]["date"].strftime("%Y-%m-%d")
    ok = gh_upload(f"morg/{loc}/{ym}/{d}.csv", "\n".join(lines))
    txt += "\n✅ GitHub" if ok else "\n⚠️ Локально"

    await m.answer(txt)
    shifts[loc]["date"]=None; shifts[loc]["bodies"]=[]
    await state.clear()

@dp.message(F.text == "📋 Смена за сегодня")
async def today_report(m: types.Message):
    today = datetime.now(); ym=today.strftime("%Y-%m"); d=today.strftime("%Y-%m-%d")
    txt=""
    
    # Зарплата агента за сегодня
    agent_sal = 0
    content = read_file("ritual/orders.csv")
    if content:
        for line in content.strip().split("\n")[1:]:
            p = line.split(",")
            if len(p)>=8 and p[0].startswith(d):
                try: agent_sal += int(p[7].strip())
                except: pass

    for loc,cfg in MORG_CONFIG.items():
        content = read_file(f"morg/{loc}/{ym}/{d}.csv")
        lb=0; ls=0; lt=0
        if content:
            for line in content.strip().split("\n")[1:]:
                p=line.split(",")
                if len(p)<5: continue
                lb+=1
                if p[3].strip().upper() in ("ДА","YES","TRUE","1"):
                    std = p[1].strip()=="Стандарт"
                    s = cfg["standard_sanitar"] if std else cfg["nonstd_sanitar"]
                    t = cfg["standard_transport"] if std else cfg["nonstd_transport"]
                    ls+=s; lt+=t
        
        txt += f"🏥 {cfg['name']}\n"
        txt += f"Тел: {lb}\n"
        txt += f"🧑‍⚕️ Санитары: {ls}₽\n"
        if cfg["has_transport"]: txt += f"🚚 Перевозка: {lt}₽\n"
        txt += f"👤 Агент з/п: {agent_sal}₽\n"
        if cfg["has_income"]:
            inc = 0
            if content:
                for line in content.strip().split("\n")[1:]:
                    p=line.split(",")
                    if len(p)<5: continue
                    if p[3].strip().upper() in ("ДА","YES","TRUE","1"):
                        inc += cfg["standard_income"] if p[1].strip()=="Стандарт" else cfg["nonstd_income"]
            txt += f"💰 Доход: {inc}₽\n"
        txt += f"{'─'*15}\n\n"
    
    if not txt: txt = "Смен за сегодня нет."
    await m.answer(txt)

# ============================================================
# РИТУАЛ
# ============================================================
@dp.message(F.text == "🕯️ Ритуал")
async def ritual_menu(m: types.Message):
    await m.answer("Выбери тип заказа:", reply_markup=kb_ritual_type())

@dp.callback_query(F.data.in_(["ord_funeral","ord_cremation"]))
async def start_order(cb: types.CallbackQuery, state: FSMContext):
    t = "funeral" if cb.data=="ord_funeral" else "cremation"
    await state.update_data(type=t, extras=[])
    txt = "⚰️ ПОХОРОНЫ\n\nДата (дд.мм.гггг):" if t=="funeral" else "🔥 КРЕМАЦИЯ\n\nДата (дд.мм.гггг):"
    await cb.message.edit_text(txt); await cb.answer()
    await state.set_state(Ritual.event_date)

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

# Похороны
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
        await cb.message.answer("Выбери (можно несколько):", reply_markup=kb_extras([]))
        await state.set_state(Ritual.extras)

@dp.callback_query(F.data.startswith("col_"))
async def col_sel(cb: types.CallbackQuery, state: FSMContext):
    cols={"col_white":"Белый","col_black":"Чёрный","col_green":"Зелёный","col_blue":"Синий"}
    await state.update_data(urn_color=cols[cb.data])
    await state.set_state(Ritual.extras)
    await cb.message.edit_text(f"Цвет: {cols[cb.data]}\n\nДоп. услуги:")
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
    try:
        await cb.message.edit_reply_markup(reply_markup=kb_extras(extras))
    except Exception:
        pass  # Игнорируем "message not modified"
    await cb.answer()

# ============================================================
# СОХРАНЕНИЕ ЗАКАЗА + РАЗДЕЛЬНЫЕ КАРТОЧКИ
# ============================================================
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

    # ДВЕ РАЗДЕЛЬНЫЕ карточки
    route = build_route(data)
    crem_card = build_crem_card(data) if t=="cremation" else None

    global last_orders
    last_orders.append({
        "deceased": data["deceased"],
        "type": t,
        "route": route,           # Карточка водителя
        "crem_card": crem_card,   # Карточка крематория
        "date": data.get("event_date","")
    })

    # Показываем ОБЕ карточки отдельно
    txt = "✅ Заказ сохранён\n\n"
    txt += "━━━━━━━━━━━━━━━\n📋 ВОДИТЕЛЮ:\n━━━━━━━━━━━━━━━\n" + route

    if crem_card:
        txt += "\n\n━━━━━━━━━━━━━━━\n🔥 КРЕМАТОРИЙ:\n━━━━━━━━━━━━━━━\n" + crem_card

    txt += "\n\n📌 Используй 🚕 Водителю для отправки карточки водителю"

    await m.answer(txt); await state.clear()

def build_route(data):
    t=data["type"]
    txt=f"🚕 ЗАКАЗ ВОДИТЕЛЮ\nДата: {data.get('event_date','')}\nТип: {'Похороны' if t=='funeral' else 'Кремация'}\n"
    txt+=f"Усопший: {data['deceased']}\n"
    if t=="funeral":
        txt+=f"Гроб: {data.get('coffin','')}\nХрам: {data.get('temple','')}\nКладбище: {data.get('cemetery','')}\n"
    else:
        extras=data.get("extras",[])
        if "hall" in extras: txt+="Кладбище: Крематорий\n"
        else: txt+=f"Храм: {data.get('temple','')}\nКладбище: Крематорий\n"
    txt+=f"☎️ {data['phone']}"
    return txt

def build_crem_card(data):
    """Карточка для оформления в крематории"""
    urn=data.get('urn_type','')
    if urn=='Пластик': urn+=f" ({data.get('urn_color','')})"
    em={"box_pol":"Гроб полированный","large":"Крупное тело","hall":"Зал+отпевание","urgent":"Срочная"}
    er=[em.get(e,e) for e in data.get("extras",[])]
    et="; ".join(er) if er else "Нет"
    
    return (
        f"🔥 КРЕМАЦИЯ — ОФОРМЛЕНИЕ\n"
        f"Дата: {data.get('event_date','')}\n"
        f"Усопший: {data['deceased']}\n"
        f"Урна: {urn}\n"
        f"Доп. услуги: {et}\n"
        f"Храм: {data.get('temple','Крематорий')}\n\n"
        f"Все стандартно, оплата наличными,\n"
        f"оформлю в день кремации."
    )

# ============================================================
# ВОДИТЕЛЮ И КРЕМАТОРИЙ — ВЫБОР
# ============================================================
@dp.message(F.text == "🚕 Водителю")
async def driver_route(m: types.Message, state: FSMContext):
    if not last_orders:
        await m.answer("⚠️ Нет заказов."); return
    if len(last_orders)==1:
        o = last_orders[0]
        # Показываем кнопки выбора
        await m.answer(f"📋 Заказ: {o['deceased']}\n\nЧто отправить?", reply_markup=kb_order_actions(0))
        return
    # Несколько заказов — выбор
    await m.answer("Выбери заказ:", reply_markup=kb_order_select(last_orders))
    await state.set_state(Morg.closing)

@dp.callback_query(F.data.startswith("sel_ord_"))
async def select_order(cb: types.CallbackQuery, state: FSMContext):
    i = int(cb.data.split("_")[2])
    o = last_orders[i]
    await cb.message.edit_text(f"📋 {o['deceased']} ({o['date']})\n\nЧто отправить?", reply_markup=kb_order_actions(i))
    await cb.answer()

@dp.callback_query(F.data.startswith("send_driver_"))
async def send_driver(cb: types.CallbackQuery):
    i = int(cb.data.split("_")[2])
    if i < len(last_orders):
        await cb.message.answer(last_orders[i]["route"])
    await cb.answer("Карточка водителя скопирована")

@dp.callback_query(F.data.startswith("send_crem_"))
async def send_crem(cb: types.CallbackQuery):
    i = int(cb.data.split("_")[2])
    if i < len(last_orders):
        card = last_orders[i].get("crem_card")
        if card:
            await cb.message.answer(card)
        else:
            await cb.message.answer("⚠️ Это не кремация")
    await cb.answer("Карточка крематория скопирована")

@dp.message(F.text == "📊 Отчёт")
async def report_menu(m: types.Message):
    await m.answer("⏳ Загружаю...")
    mg = get_weekly()
    today=datetime.now().strftime("%d.%m.%Y")
    wk=(datetime.now()-timedelta(days=7)).strftime("%d.%m.%Y")
    txt=(f"📊 ОТЧЁТ ({wk} — {today})\n\n⚰️ МОРГ:\nВсего: {mg['total']}\n"
         f"✅ {mg['paid']} | ❌ {mg['unpaid']}\nДоход: {mg['income']}₽\n"
         f"🧑‍⚕️ Санитары: {mg['sanitars']}₽\n🚚 Перевозка: {mg['transport']}₽\n"
         f"💰 Прибыль: {mg['profit']}₽")
    await m.answer(txt)

# ============================================================
# ЗАПУСК
# ============================================================
@dp.errors()
async def errors_handler(e):
    logger.error(f"Error: {e}"); return True

async def on_startup():
    url=os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
    host=os.getenv("RENDER_EXTERNAL_HOSTNAME","")
    if not url and host: url=f"https://{host}"
    if not url: logger.error("RENDER_EXTERNAL_URL не задан!"); return
    secret=WEBHOOK_SECRET if WEBHOOK_SECRET else None
    try:
        await bot.set_webhook(f"{url}{WEBHOOK_PATH}", secret_token=secret)
        logger.info(f"Webhook: {url}{WEBHOOK_PATH}")
    except Exception as e: logger.error(f"Webhook: {e}")
    logger.info(f"Пользователи: {len(users_cache)}")
    for uid,info in users_cache.items(): logger.info(f"  {uid} -> {info['role']} ({info['name']})")

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
