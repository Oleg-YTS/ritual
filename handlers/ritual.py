"""
БЛОК 2: РИТУАЛКА — похороны и кремация
Версия: Рабочая (до рефакторинга меню)
"""

import os
import sys
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.storage import UsersStorage, MorgueStorage
from utils.reports import build_driver_card, build_crematorium_card

logger = logging.getLogger(__name__)

router = Router(name="ritual")

# ============================================================
# ХРАНИЛИЩА
# ============================================================
users_db = UsersStorage()
morgue1_db = MorgueStorage("morgue1")
morgue2_db = MorgueStorage("morgue2")
MORGUE_DBS = {"morgue1": morgue1_db, "morgue2": morgue2_db}
MORGUE_NAMES = {"morgue1": "Первомайская 13", "morgue2": "Мира 11"}

# Временное хранение заказов
active_orders: List[Dict[str, Any]] = []

# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def kb_main_menu(role: str):
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="➕ Добавить тело"))
    if role in ["admin", "manager_morg1", "manager_morg2"]:
        b.row(KeyboardButton(text="🔒 Закрыть смена"))
        b.row(KeyboardButton(text="🗑️ Удалить тело"))
    b.row(KeyboardButton(text="🕯️ Ритуальный заказ"))
    b.row(KeyboardButton(text="📋 Мои заказы"))
    if role in ["admin", "manager_morg1", "manager_morg2"]:
        b.row(KeyboardButton(text="📊 Отчёт за период"))
    if role == "admin":
        b.row(KeyboardButton(text="📈 Статистика"))
        b.row(KeyboardButton(text="👥 Пользователи"))
    return b.as_markup(resize_keyboard=True)

def kb_ritual_type():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚰️ Похороны", callback_data="rtype_funeral")],
        [InlineKeyboardButton(text="🔥 Кремация", callback_data="rtype_cremation")]
    ])

def kb_morgue_location():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Первомайская 13", callback_data="rloc_m1")],
        [InlineKeyboardButton(text="Мира 11", callback_data="rloc_m2")],
        [InlineKeyboardButton(text="Другое место", callback_data="rloc_other")]
    ])

def kb_urn_type():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Вечная память", callback_data="urn_cardboard")],
        [InlineKeyboardButton(text="Пластик", callback_data="urn_plastic")]
    ])

def kb_urn_color():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Белый", callback_data="ucol_white")],
        [InlineKeyboardButton(text="Чёрный", callback_data="ucol_black")],
        [InlineKeyboardButton(text="Синий", callback_data="ucol_blue")]
    ])

def kb_extras(selected: list = None):
    if selected is None: selected = []
    extras = {
        "large_body": "Крупное тело",
        "short_farewell": "Короткое прощание",
        "polished_coffin": "Полированный гроб",
        "hall": "Зал",
        "hall_blessing": "Зал + отпевание",
        "urgent": "Срочная кремация"
    }
    b = InlineKeyboardBuilder()
    for key, label in extras.items():
        mark = "✅" if key in selected else ""
        b.row(InlineKeyboardButton(text=f"{mark} {label}" if mark else label, callback_data=f"rextra_{key}"))
    b.row(InlineKeyboardButton(text="ДАЛЕЕ", callback_data="rextra_done"))
    return b.as_markup()

def kb_order_select(orders: list):
    b = InlineKeyboardBuilder()
    for i, order in enumerate(orders):
        icon = "🔥" if order.get("type") == "cremation" else ""
        b.row(InlineKeyboardButton(text=f"{icon} {order.get('deceased', '?')}", callback_data=f"rorder_{i}"))
    return b.as_markup()

def kb_order_actions():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Водителю", callback_data="rsend_driver")],
        [InlineKeyboardButton(text="Крематорий", callback_data="rsend_crem")]
    ])

def kb_report_period():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Неделя", callback_data="speriod_week")],
        [InlineKeyboardButton(text="Месяц", callback_data="speriod_month")],
        [InlineKeyboardButton(text="Квартал", callback_data="speriod_quarter")]
    ])

# ============================================================
# FSM
# ============================================================

class RitualFSM(StatesGroup):
    order_type = State()
    event_date = State()
    customer_name = State()
    customer_phone = State()
    deceased_name = State()
    location_type = State()
    other_location = State()
    temple = State()
    cemetery = State()
    urn_type = State()
    urn_color = State()
    extras = State()
    extras_temple = State()

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ
# ============================================================

def get_user(tid):
    return users_db.get_user(tid)

def get_user_morgue(tid):
    role = get_user(tid)
    if not role: return None
    r = role.get("role", "")
    if r == "admin": return None
    if "morg1" in r: return "morgue1"
    if "morg2" in r: return "morgue2"
    return None

def check_perm(tid, action):
    user = get_user(tid)
    if not user: return False
    role = user.get("role", "")
    perms = {
        "admin": ["add", "remove", "close", "stats", "report", "removed", "order", "cards", "users"],
        "manager_morg1": ["add", "remove", "close", "report", "removed", "order", "cards"],
        "manager_morg2": ["add", "remove", "close", "report", "removed", "order", "cards"],
        "agent_morg1": ["add", "remove", "order", "cards"],
        "agent_morg2": ["add", "remove", "order", "cards"],
    }
    return action in perms.get(role, [])

def save_order_to_shift(order: dict, morgue_id: str) -> bool:
    db = MORGUE_DBS.get(morgue_id)
    if not db: return False
    shift = db.get_active_shift()
    if shift: return db.add_order(shift["shift_id"], order)
    return False

# ============================================================
# НАЧАЛО ЗАКАЗА
# ============================================================

@router.message(F.text == "🕯️ Ритуальный заказ")
async def start_ritual_order(message: types.Message, state: FSMContext):
    logger.info(f"Нажата 🕯️ Ритуальный заказ от {message.from_user.id}")
    if not check_perm(message.from_user.id, "order"):
        await message.answer("⚠️ Нет прав."); return
    await state.clear()
    await message.answer("Тип заказа:", reply_markup=kb_ritual_type())
    await state.set_state(RitualFSM.order_type)

@router.callback_query(F.data.in_(["rtype_funeral", "rtype_cremation"]), RitualFSM.order_type)
async def select_order_type(cb: types.CallbackQuery, state: FSMContext):
    otype = "funeral" if cb.data == "rtype_funeral" else "cremation"
    await state.update_data(type=otype)
    await cb.message.edit_text("📅 Дата мероприятия (ДД.ММ.ГГГГ):")
    await cb.answer()
    await state.set_state(RitualFSM.event_date)

@router.message(RitualFSM.event_date)
async def input_event_date(message: types.Message, state: FSMContext):
    date_str = message.text.strip()
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        await message.answer("⚠️ Введи дату в формате ДД.ММ.ГГГГ:"); return
    await state.update_data(event_date=date_str)
    await message.answer("👤 ФИО заказчика:")
    await state.set_state(RitualFSM.customer_name)

@router.message(RitualFSM.customer_name)
async def input_customer_name(message: types.Message, state: FSMContext):
    name = message.text.strip().upper()
    if not name: await message.answer("⚠️ Введи ФИО:"); return
    await state.update_data(customer_name=name)
    await message.answer("☎️ Телефон:")
    await state.set_state(RitualFSM.customer_phone)

@router.message(RitualFSM.customer_phone)
async def input_customer_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if not phone: await message.answer("⚠️ Введи телефон:"); return
    await state.update_data(customer_phone=phone)
    await message.answer("💀 ФИО усопшего:")
    await state.set_state(RitualFSM.deceased_name)

@router.message(RitualFSM.deceased_name)
async def input_deceased_name(message: types.Message, state: FSMContext):
    name = message.text.strip().upper()
    if not name: await message.answer("⚠️ Введи ФИО:"); return
    await state.update_data(deceased_name=name)
    await message.answer("📍 Где тело?", reply_markup=kb_morgue_location())
    await state.set_state(RitualFSM.location_type)

@router.callback_query(F.data.in_(["rloc_m1", "rloc_m2", "rloc_other"]), RitualFSM.location_type)
async def select_location(cb: types.CallbackQuery, state: FSMContext):
    loc_map = {"rloc_m1": "morgue1", "rloc_m2": "morgue2", "rloc_other": "other"}
    loc = loc_map[cb.data]
    await state.update_data(location=loc)
    if loc == "other":
        await cb.message.edit_text("📍 Введи адрес:")
        await cb.answer()
        await state.set_state(RitualFSM.other_location)
    else:
        await cb.answer()
        data = await state.get_data()
        if data["type"] == "funeral":
            await cb.message.answer("⛪ Где отпевают (храм):")
            await state.set_state(RitualFSM.temple)
        else:
            await cb.message.answer("📦 Тип урны:", reply_markup=kb_urn_type())
            await state.set_state(RitualFSM.urn_type)

@router.message(RitualFSM.other_location)
async def input_other_location(message: types.Message, state: FSMContext):
    loc = message.text.strip().upper()
    if not loc: await message.answer("⚠️ Введи адрес:"); return
    await state.update_data(location=loc)
    data = await state.get_data()
    if data["type"] == "funeral":
        await message.answer("⛪ Где отпевают (храм):")
        await state.set_state(RitualFSM.temple)
    else:
        await message.answer("📦 Тип урны:", reply_markup=kb_urn_type())
        await state.set_state(RitualFSM.urn_type)

# Похороны
@router.message(RitualFSM.temple)
async def input_temple(message: types.Message, state: FSMContext):
    temple = message.text.strip().upper()
    if not temple: await message.answer("⚠️ Введи храм:"); return
    await state.update_data(temple=temple)
    await message.answer("🪦 Кладбище:")
    await state.set_state(RitualFSM.cemetery)

@router.message(RitualFSM.cemetery)
async def input_cemetery(message: types.Message, state: FSMContext):
    cemetery = message.text.strip().upper()
    if not cemetery: await message.answer("⚠️ Введи кладбище:"); return
    await state.update_data(cemetery=cemetery)
    await _save_and_send(message, state)

# Кремация
@router.callback_query(F.data.in_(["urn_cardboard", "urn_plastic"]), RitualFSM.urn_type)
async def select_urn_type(cb: types.CallbackQuery, state: FSMContext):
    urn = "cardboard" if cb.data == "urn_cardboard" else "plastic"
    await state.update_data(urn_type=urn)
    if urn == "plastic":
        await cb.message.edit_text("Цвет урны:", reply_markup=kb_urn_color())
        await cb.answer()
        await state.set_state(RitualFSM.urn_color)
    else:
        await state.update_data(urn_color=None)
        await cb.message.edit_text("Доп. услуги:")
        await state.update_data(extras=[])
        await cb.message.answer("Отметь нужное:", reply_markup=kb_extras([]))
        await cb.answer()
        await state.set_state(RitualFSM.extras)

@router.callback_query(F.data.in_(["ucol_white", "ucol_black", "ucol_blue"]), RitualFSM.urn_color)
async def select_urn_color(cb: types.CallbackQuery, state: FSMContext):
    col_map = {"ucol_white": "Белый", "ucol_black": "Чёрный", "ucol_blue": "Синий"}
    await state.update_data(urn_color=col_map[cb.data])
    await cb.message.edit_text("Доп. услуги:")
    await state.update_data(extras=[])
    await cb.message.answer("Отметь нужное:", reply_markup=kb_extras([]))
    await cb.answer()
    await state.set_state(RitualFSM.extras)

@router.callback_query(F.data.startswith("rextra_"), RitualFSM.extras)
async def handle_extras(cb: types.CallbackQuery, state: FSMContext):
    if cb.data == "rextra_done":
        await cb.answer()
        data = await state.get_data()
        extras = data.get("extras", [])
        has_hall = "hall" in extras or "hall_blessing" in extras
        if has_hall:
            await state.update_data(temple="Зал отпевания")
            await _save_and_send(cb.message, state)
        else:
            await cb.message.answer("⛪ Где отпевают (храм):")
            await state.set_state(RitualFSM.extras_temple)
        return
    key = cb.data.replace("rextra_", "")
    data = await state.get_data()
    extras = data.get("extras", [])
    if key in extras: extras.remove(key)
    else: extras.append(key)
    await state.update_data(extras=extras)
    try:
        await cb.message.edit_reply_markup(reply_markup=kb_extras(extras))
    except: pass
    await cb.answer()

@router.message(RitualFSM.extras_temple)
async def input_extras_temple(message: types.Message, state: FSMContext):
    temple = message.text.strip().upper()
    if not temple: await message.answer("⚠️ Введи храм:"); return
    await state.update_data(temple=temple)
    await _save_and_send(message, state)

# Сохранение
async def _save_and_send(message, state: FSMContext):
    data = await state.get_data()
    otype = data["type"]
    loc = data.get("location", "")
    loc_map = {"morgue1": "Первомайская 13", "morgue2": "Мира 11"}
    morgue_name = loc_map.get(loc, loc)
    
    order = {
        "order_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "event_date": data["event_date"], "type": otype,
        "customer_name": data["customer_name"], "customer_phone": data["customer_phone"],
        "deceased": data["deceased_name"], "morgue_location": morgue_name,
        "phone": data["customer_phone"], "temple": data.get("temple", ""),
        "cemetery": data.get("cemetery", "")
    }
    if otype == "cremation":
        urn = data.get("urn_type", "")
        order["urn"] = "Вечная память" if urn == "cardboard" else f"Пластик ({data.get('urn_color', '')})"
        order["extras"] = data.get("extras", [])

    active_orders.append(order)
    actual_morgue = loc if loc in ["morgue1", "morgue2"] else get_user_morgue(message.from_user.id)
    if actual_morgue: save_order_to_shift(order, actual_morgue)

    driver_card = build_driver_card(order)
    crem_card = build_crematorium_card(order) if otype == "cremation" else None
    response = "✅ Заказ сохранён\n\n"
    response += "━━ 📋 ВОДИТЕЛЮ ━━\n" + driver_card
    if crem_card: response += "\n\n━━ 🔥 КРЕМАТОРИЙ ━━\n" + crem_card
    response += "\n\nИспользуй 📋 Мои заказы для отправки"
    
    await message.answer(response)
    await state.clear()
    user = get_user(message.from_user.id)
    role = user["role"] if user else "admin"
    await message.answer("Далее:", reply_markup=kb_main_menu(role))

# Мои заказы
@router.message(F.text == "📋 Мои заказы")
async def show_my_orders(message: types.Message, state: FSMContext):
    if not check_perm(message.from_user.id, "cards"):
        await message.answer("⚠️ Нет прав."); return
    if not active_orders:
        await message.answer("⚠️ Нет заказов."); return
    if len(active_orders) == 1:
        order = active_orders[0]
        icon = "🔥" if order.get("type") == "cremation" else ""
        await message.answer(f"{icon} {order.get('deceased', 'Без имени')}", reply_markup=kb_order_actions())
    else:
        await message.answer("Выбери заказ:", reply_markup=kb_order_select(active_orders))

@router.callback_query(F.data.startswith("rorder_"))
async def select_order(cb: types.CallbackQuery, state: FSMContext):
    idx = int(cb.data.split("_")[-1])
    if 0 <= idx < len(active_orders):
        await state.update_data(selected_order_idx=idx)
        order = active_orders[idx]
        icon = "🔥" if order.get("type") == "cremation" else ""
        await cb.message.edit_text(f"{icon} {order.get('deceased', 'Без имени')}", reply_markup=kb_order_actions())
    await cb.answer()

@router.callback_query(F.data == "rsend_driver")
async def send_driver(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    idx = data.get("selected_order_idx", 0)
    if 0 <= idx < len(active_orders):
        await cb.message.answer(build_driver_card(active_orders[idx]))
    await cb.answer()

@router.callback_query(F.data == "rsend_crem")
async def send_crem(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    idx = data.get("selected_order_idx", 0)
    if 0 <= idx < len(active_orders):
        order = active_orders[idx]
        if order.get("type") == "cremation":
            await cb.message.answer(build_crematorium_card(order))
        else:
            await cb.message.answer("⚠️ Это похороны.")
    await cb.answer()
