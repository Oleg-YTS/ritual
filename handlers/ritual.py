"""
БЛОК 2: РИТУАЛКА — похороны и кремация
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.storage import UsersStorage, MorgueStorage
from database.order_storage import save_order as save_order_to_file, get_orders_by_date, get_all_orders_for_morgue
from database.crm import add_order as crm_add_order
from utils.reports import build_driver_card, build_crematorium_card
from keyboards.menus import (
    kb_main_menu, kb_morgue_location,
    kb_urn_type, kb_urn_color, kb_extras, kb_order_select, kb_order_actions,
    ALL_MENU_BUTTONS
)

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

# Удалено: active_orders = [] (теперь читаем из БД)

# ============================================================
# FSM
# ============================================================

class RitualFSM(StatesGroup):
    select_morgue = State()
    other_location = State()
    event_date = State()
    customer_name = State()
    customer_phone = State()
    deceased_name = State()
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

# Функция save_order_to_shift удалена — она не нужна

# ============================================================
# ХЕНДЛЕРЫ (Прямой запуск кнопок)
# ============================================================

@router.message(F.text == "⚰️ Похороны")
async def start_funeral(message: types.Message, state: FSMContext):
    await state.clear()
    await _start_ritual_flow(message, state, "funeral")

@router.message(F.text == "🔥 Кремация")
async def start_cremation(message: types.Message, state: FSMContext):
    await state.clear()
    await _start_ritual_flow(message, state, "cremation")

async def _start_ritual_flow(message, state, otype: str):
    if not check_perm(message.from_user.id, "order"):
        await message.answer("⚠️ Нет прав."); return
    
    await state.update_data(type=otype)
    await state.clear()
    await state.update_data(type=otype)

    # Всегда спрашиваем выбор морга у всех (Агент, Менеджер, Админ)
    await message.answer("📍 Где находится тело?", reply_markup=kb_morgue_location())
    await state.set_state(RitualFSM.select_morgue)

@router.callback_query(F.data.in_(["rloc_m1", "rloc_m2", "rloc_other"]), RitualFSM.select_morgue)
async def select_location(cb: types.CallbackQuery, state: FSMContext):
    loc_map = {"rloc_m1": "morgue1", "rloc_m2": "morgue2", "rloc_other": "other"}
    loc = loc_map[cb.data]
    await state.update_data(location=loc, morgue_id=loc if loc != "other" else None)
    
    if loc == "other":
        await cb.message.edit_text("📍 Введи адрес:")
        await cb.answer()
        await state.set_state(RitualFSM.other_location)
    else:
        data = await state.get_data()
        otype_name = "Похороны" if data.get("type") == "funeral" else "Кремация"
        await cb.message.edit_text(f"🏥 {MORGUE_NAMES.get(loc, loc)} — {otype_name}\n\n📅 Дата мероприятия (ДД.ММ.ГГГГ):")
        await cb.answer()
        await state.set_state(RitualFSM.event_date)

@router.message(RitualFSM.other_location)
async def input_other_location(message: types.Message, state: FSMContext):
    loc = message.text.strip().upper()
    if not loc: await message.answer("⚠️ Введи адрес:"); return
    await state.update_data(location=loc)
    await message.answer("📅 Дата мероприятия (ДД.ММ.ГГГГ):")
    await state.set_state(RitualFSM.event_date)

@router.message(RitualFSM.event_date, ~F.text.in_(ALL_MENU_BUTTONS))
async def input_event_date(message: types.Message, state: FSMContext):
    date_str = message.text.strip()
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        await message.answer("⚠️ Введи дату в формате ДД.ММ.ГГГГ:"); return
    await state.update_data(event_date=date_str)
    await message.answer("👤 ФИО заказчика:")
    await state.set_state(RitualFSM.customer_name)

@router.message(RitualFSM.customer_name, ~F.text.in_(ALL_MENU_BUTTONS))
async def input_customer_name(message: types.Message, state: FSMContext):
    name = message.text.strip().upper()
    if not name: await message.answer("⚠️ Введи ФИО:"); return
    await state.update_data(customer_name=name)
    await message.answer("☎️ Телефон:")
    await state.set_state(RitualFSM.customer_phone)

@router.message(RitualFSM.customer_phone, ~F.text.in_(ALL_MENU_BUTTONS))
async def input_customer_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if not phone: await message.answer("⚠️ Введи телефон:"); return
    await state.update_data(customer_phone=phone)
    await message.answer("💀 ФИО усопшего:")
    await state.set_state(RitualFSM.deceased_name)

@router.message(RitualFSM.deceased_name, ~F.text.in_(ALL_MENU_BUTTONS))
async def input_deceased_name(message: types.Message, state: FSMContext):
    name = message.text.strip().upper()
    if not name: await message.answer("⚠️ Введи ФИО:"); return
    await state.update_data(deceased_name=name)
    
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
    
    # Всегда запрашиваем размер гроба для похорон
    data = await state.get_data()
    if data["type"] == "funeral":
        await message.answer("🪦 Гроб (размер):")
        await state.set_state(RitualFSM.select_casket)
    else:
        await _save_and_send(message, state)

@router.message(RitualFSM.select_casket)
async def input_casket(message: types.Message, state: FSMContext):
    casket = message.text.strip()
    if not casket:
        await message.answer("🪦 Гроб (размер):")
        return
    await state.update_data(casket=casket)
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
    
    now = datetime.now()
    
    order = {
        "order_date": now.strftime("%Y-%m-%d %H:%M"), # Лог
        "creation_date": now.strftime("%d.%m.%Y"),    # Для поиска "Мои заказы за сегодня"
        "event_date": data["event_date"], 
        "type": otype,
        "customer_name": data["customer_name"], "customer_phone": data["customer_phone"],
        "deceased": data["deceased_name"], "morgue_location": morgue_name,
        "phone": data["customer_phone"], "temple": data.get("temple", ""),
        "cemetery": data.get("cemetery", "")
    }
    if otype == "cremation":
        urn = data.get("urn_type", "")
        order["urn"] = "Вечная память" if urn == "cardboard" else f"Пластик ({data.get('urn_color', '')})"
        order["extras"] = data.get("extras", [])
    
    # Определяем морг для сохранения заказа
    if loc in ["morgue1", "morgue2"]:
        actual_morgue = loc
    else:
        # Если выбрано "Другое место" или нет выбора
        user_morgue = get_user_morgue(message.from_user.id)
        if user_morgue:
            actual_morgue = user_morgue  # Менеджер/Агент — свой морг
        else:
            # Админ без привязки — сохраняем в ОБА морга
            actual_morgue = None

    # Читаем актуальные карточки из сохраненного order
    driver_card = build_driver_card(order)
    crem_card = build_crematorium_card(order) if otype == "cremation" else None
    response = "✅ Заказ сохранён\n\n"
    response += driver_card
    if crem_card: response += "\n\n" + crem_card
    response += "\n\nИспользуй 📋 Мои заказы для отправки"

    try:
        # Сохраняем заказ ТОЛЬКО в файл по дате мероприятия (архив)
        if actual_morgue:
            save_order_to_file(actual_morgue, order)
            logger.info(f"Заказ сохранён в архив: {actual_morgue}")

            # Добавляем заказ в смену как ОРДЕР (не тело!) - только для отображения
            db = MORGUE_DBS[actual_morgue]
            shift = db.get_active_shift()
            if shift:
                if "orders" not in shift:
                    shift["orders"] = []
                shift["orders"].append(order)
                db.update_shift(shift["shift_id"], shift)
        else:
            # Админ без привязки — сохраняем в ОБА файла
            save_order_to_file("morgue1", order)
            save_order_to_file("morgue2", order)
            logger.info(f"Заказ сохранен в оба архива (Admin/Other location)")

            # Добавляем заказ в обе смены как ОРДЕР
            for mid in ["morgue1", "morgue2"]:
                db = MORGUE_DBS[mid]
                shift = db.get_active_shift()
                if shift:
                    if "orders" not in shift:
                        shift["orders"] = []
                    shift["orders"].append(order)
                    db.update_shift(shift["shift_id"], shift)

        # Сохраняем в CRM базу для обзвона и памятников
        crm_add_order(order)
        logger.info(f"Заказ добавлен в CRM: {order.get('deceased')}")

    except Exception as e:
        logger.error(f"ОШИБКА СОХРАНЕНИЯ ЗАКАЗА: {e}")
        await message.answer(f"⚠️ Ошибка сохранения заказа: {e}")
        return
    
    await message.answer(response)
    await state.clear()
    user = get_user(message.from_user.id)
    role = user["role"] if user else "admin"
    await message.answer("Далее:", reply_markup=kb_main_menu(role))

# Добавляем новое состояние для выбора даты
class RitualFSM(StatesGroup):
    select_morgue = State()
    other_location = State()
    event_date = State()
    customer_name = State()
    customer_phone = State()
    deceased_name = State()
    temple = State()
    cemetery = State()
    urn_type = State()
    urn_color = State()
    extras = State()
    extras_temple = State()
    select_orders_date = State()  # Новое состояние для ввода даты
    select_casket = State()      # Новое состояние для ввода размера гроба

# Клавиатура для выбора даты
def kb_orders_date():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Сегодня", callback_data="order_today"),
        InlineKeyboardButton(text="Вчера", callback_data="order_yesterday")
    )
    builder.row(
        InlineKeyboardButton(text="Выбрать дату", callback_data="order_choose_date")
    )
    return builder.as_markup()

# Мои заказы — начальный экран с выбором даты
@router.message(F.text == "📋 Мои заказы")
async def show_my_orders_menu(message: types.Message, state: FSMContext):
    if not check_perm(message.from_user.id, "cards"):
        await message.answer("⚠️ Нет прав."); return
    await message.answer("📋 Выберите дату:", reply_markup=kb_orders_date())

# Обработчик выбора даты
@router.callback_query(F.data.in_(["order_today", "order_yesterday"]))
async def handle_order_date(cb: types.CallbackQuery, state: FSMContext):
    user_morgue = get_user_morgue(cb.from_user.id)
    
    # Определяем дату
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    if cb.data == "order_today":
        target_date = now.strftime("%d.%m.%Y")
        date_label = "сегодня"
    else:
        yesterday = now - timedelta(days=1)
        target_date = yesterday.strftime("%d.%m.%Y")
        date_label = "вчера"
    
    # Собираем заказы
    orders_to_show = []
    mids = ["morgue1", "morgue2"] if not user_morgue else [user_morgue]
    
    for mid in mids:
        all_orders = get_all_orders_for_morgue(mid)
        for order in all_orders:
            if order.get("creation_date") == target_date:
                order["_morgue_name"] = MORGUE_NAMES[mid]
                orders_to_show.append(order)

    if not orders_to_show:
        await cb.message.edit_text(f"📅 Заказов за {date_label} ({target_date}) не найдено.")
        await cb.answer()
        return

    # Формируем список
    text = f"📋 Мои заказы ({target_date})\n"
    text += "_" * 35 + "\n"
    
    for i, order in enumerate(orders_to_show, 1):
        icon = "🔥" if order.get("type") == "cremation" else "⚰️"
        deceased = order.get("deceased", "Без имени")
        text += f"\n{i}. {icon} {deceased}"

    await state.update_data(orders_list=orders_to_show)
    await cb.message.edit_text(text, reply_markup=kb_order_select(orders_to_show))
    await cb.answer()

# Выбор даты вручную
@router.callback_query(F.data == "order_choose_date")
async def choose_date_manually(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.edit_text("📅 Введите дату в формате ДД.ММ.ГГГГ\nПример: 20.04.2026")
    await state.set_state(RitualFSM.select_orders_date)
    await cb.answer()

# Обработка ввода даты
@router.message(RitualFSM.select_orders_date)
async def show_orders_by_date(message: types.Message, state: FSMContext):
    user_morgue = get_user_morgue(message.from_user.id)
    
    # Парсим дату
    try:
        day, month, year = map(int, message.text.strip().split('.'))
        if len(str(year)) != 4 or year < 2000:
            raise ValueError
        input_date = f"{message.text.strip():0>8}"
    except:
        await message.answer("⚠️ Введите дату в формате ДД.ММ.ГГГГ")
        return

    # Собираем заказы
    orders_to_show = []
    mids = ["morgue1", "morgue2"] if not user_morgue else [user_morgue]
    
    for mid in mids:
        all_orders = get_all_orders_for_morgue(mid)
        for order in all_orders:
            if order.get("creation_date") == input_date:
                order["_morgue_name"] = MORGUE_NAMES[mid]
                orders_to_show.append(order)

    if not orders_to_show:
        await message.answer(f"📅 Заказов за {input_date} не найдено.")
        await state.clear()
        return

    # Формируем список
    text = f"📋 Мои заказы ({input_date})\n"
    text += "_" * 35 + "\n"
    
    for i, order in enumerate(orders_to_show, 1):
        icon = "🔥" if order.get("type") == "cremation" else "⚰️"
        deceased = order.get("deceased", "Без имени")
        text += f"\n{i}. {icon} {deceased}"

    await state.update_data(orders_list=orders_to_show)
    await message.answer(text, reply_markup=kb_order_select(orders_to_show))
    await state.clear()

# Хендлер выбора заказа из списка
@router.callback_query(F.data.startswith("rorder_"))
async def select_order_from_list(cb: types.CallbackQuery, state: FSMContext):
    idx = int(cb.data.split("_")[-1])
    data = await state.get_data()
    orders_list = data.get("orders_list", [])
    
    if 0 <= idx < len(orders_list):
        order = orders_list[idx]
        await state.update_data(current_order=order)
        
        # Для похорон сразу отправляем карточку водителю
        if order.get("type") == "funeral":
            driver_card = build_driver_card(order)
            response = driver_card
            await cb.message.edit_text(response)
        else:
            # Для кремации отправляем обе карточки и кнопки выбора
            driver_card = build_driver_card(order)
            crem_card = build_crematorium_card(order)
            response = driver_card + "\n\n" + crem_card
            await cb.message.edit_text(response, reply_markup=kb_order_actions())
    await cb.answer()

# Хендлер кнопки "Водителю"
@router.callback_query(F.data == "rsend_driver")
async def send_driver(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    order = data.get("current_order")
    if order:
        driver_card = build_driver_card(order)
        response = driver_card + "\n\n✅ Карточка отправлена водителю"
        await cb.message.edit_text(response)
    else:
        # Если в стейте нет (например, был 1 заказ)
        await cb.message.edit_text("⚠️ Ошибка контекста заказа. Попробуй из списка.")
    await cb.answer()

# Хендлер кнопки "Крематорий"
@router.callback_query(F.data == "rsend_crem")
async def send_crem(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    order = data.get("current_order")
    if order:
        if order.get("type") == "cremation":
            crem_card = build_crematorium_card(order)
            response = crem_card + "\n\n✅ Карточка отправлена крематорию"
            await cb.message.edit_text(response)
        else:
            await cb.message.edit_text("⚠️ Это похороны.")
    else:
        await cb.message.edit_text("⚠️ Ошибка контекста.")
    await cb.answer()