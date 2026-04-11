"""
БЛОК 1: МОРГ — добавление тел, удаление, закрытие смены
"""

import os
import sys
import logging
from datetime import datetime

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.storage import UsersStorage, MorgueStorage
from utils.reports import MORGUE_CONFIG, calculate_shift_finances, format_shift_report
from database.github_backup import gh_backup
from keyboards.menus import (
    kb_main_menu, kb_select_morgue_add, kb_select_morgue_close,
    kb_select_morgue_remove, kb_body_type, kb_body_source,
    kb_payment_status, kb_bodies_for_removal, kb_removal_reason,
    ALL_MENU_BUTTONS, kb_role_switcher
)

logger = logging.getLogger(__name__)

router = Router(name="morgue")

# ============================================================
# ХРАНИЛИЩА
# ============================================================
users_db = UsersStorage()
morgue1_db = MorgueStorage("morgue1")
morgue2_db = MorgueStorage("morgue2")
MORGUE_DBS = {"morgue1": morgue1_db, "morgue2": morgue2_db}

# ============================================================
# КОНСТАНТЫ
# ============================================================
MORGUE_NAMES = {
    "morgue1": "Первомайская 13",
    "morgue2": "Мира 11"
}

MENU_BUTTONS = ALL_MENU_BUTTONS

# ============================================================
# FSM
# ============================================================

class AddBodyFSM(StatesGroup):
    surname = State()
    body_type = State()
    source = State()

class CloseShiftFSM(StatesGroup):
    payment = State()
    org_input = State()

class RemoveBodyFSM(StatesGroup):
    reason = State()
    custom_reason = State()

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

def get_or_create_shift(tid, morgue_id):
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()
    if not shift:
        user = get_user(tid)
        name = user.get("name", "Unknown") if user else "Unknown"
        shift = db.create_shift(tid, name)
    return shift

def find_real_index(bodies, active_index):
    ctr = 0
    for i, b in enumerate(bodies):
        if not b.get("removed"):
            if ctr == active_index:
                return i
            ctr += 1
    return None

# ============================================================
# /start
# ============================================================

@router.message(F.text == "/start")
async def cmd_start(message: types.Message, state: FSMContext):
    user = get_user(message.from_user.id)
    if not user:
        await message.answer(f"⚠️ Вас нет в списке.\nID: {message.from_user.id}")
        return

    role = user["role"]
    await state.clear()
    await message.answer(f"👋 {user['name']}\nМеню:", reply_markup=kb_main_menu(role))

    user_morgue = get_user_morgue(message.from_user.id)
    if role == "admin":
        await message.answer("🏥 Выбери активный морг:", reply_markup=kb_select_morgue_add())
    else:
        if user_morgue:
            await state.update_data(morgue_id=user_morgue)
            await message.answer(f"🏥 Твой морг: {MORGUE_NAMES[user_morgue]}")

@router.callback_query(F.data.in_(["add_m1", "add_m2"]))
async def set_admin_morgue(cb: types.CallbackQuery, state: FSMContext):
    mid = "morgue1" if cb.data == "add_m1" else "morgue2"
    await state.update_data(morgue_id=mid)
    await cb.message.edit_text(f"✅ Выбран: {MORGUE_NAMES[mid]}\nТеперь работай через меню.")
    await cb.answer()

# ============================================================
# ДОБАВЛЕНИЕ ТЕЛА
# ============================================================

@router.message(F.text == "➕ Добавить тело")
async def start_add_body(message: types.Message, state: FSMContext):
    if not check_perm(message.from_user.id, "add"):
        await message.answer("⚠️ Нет прав."); return
    
    data = await state.get_data()
    if data.get("morgue_id"):
        await message.answer("Фамилия:")
        await state.set_state(AddBodyFSM.surname)
        return

    user_morgue = get_user_morgue(message.from_user.id)
    if user_morgue:
        await state.update_data(morgue_id=user_morgue)
        await message.answer("Фамилия:")
        await state.set_state(AddBodyFSM.surname)
    else:
        await message.answer("Выбери морг:", reply_markup=kb_select_morgue_add())

@router.callback_query(F.data.in_(["add_m1", "add_m2"]))
async def add_select_morgue(cb: types.CallbackQuery, state: FSMContext):
    mid = "morgue1" if cb.data == "add_m1" else "morgue2"
    await state.update_data(morgue_id=mid)
    await cb.message.edit_text(f"🏥 {MORGUE_NAMES[mid]['name']}\n\nФамилия:")
    await cb.answer()
    await state.set_state(AddBodyFSM.surname)

@router.message(AddBodyFSM.surname, ~F.text.in_(MENU_BUTTONS))
async def add_surname(message: types.Message, state: FSMContext):
    surname = message.text.strip().upper()
    if not surname:
        await message.answer("⚠️ Введи фамилию:"); return
    await state.update_data(surname=surname)
    await message.answer("Тип:", reply_markup=kb_body_type())
    await state.set_state(AddBodyFSM.body_type)

@router.callback_query(F.data.in_(["btype_std", "btype_nstd"]))
async def add_body_type(cb: types.CallbackQuery, state: FSMContext):
    bt = "std" if cb.data == "btype_std" else "nstd"
    await state.update_data(body_type=bt)
    await cb.message.edit_text(f"Тип: {'Стандарт' if bt == 'std' else 'Не стандарт'}\n\nИсточник:", reply_markup=kb_body_source())
    await cb.answer()
    await state.set_state(AddBodyFSM.source)

@router.callback_query(F.data.in_(["bsrc_stat", "bsrc_amb"]))
async def add_source(cb: types.CallbackQuery, state: FSMContext):
    src = "stat" if cb.data == "bsrc_stat" else "amb"
    data = await state.get_data()
    mid = data["morgue_id"]
    tid = cb.from_user.id
    
    db = MORGUE_DBS[mid]
    shift = db.get_active_shift()
    if not shift:
        user = get_user(tid)
        name = user.get("name", "Unknown") if user else "Unknown"
        shift = db.create_shift(tid, name)
    
    body = {
        "surname": data["surname"],
        "type": data["body_type"],
        "source": src,
        "paid": False,
        "removed": False,
        "organization": ""
    }
    
    db.add_body(shift["shift_id"], body)
    src_name = "Стационар" if src == "stat" else "Амбулаторно"
    total = len([b for b in shift["bodies"] if not b.get("removed")])
    
    await cb.message.edit_text(f"✅ {body['surname']} ({src_name})\nВсего тел: {total}")
    await cb.answer()
    
    # ЦИКЛ: Сразу просим следующую фамилию
    await cb.message.answer("Следующая фамилия (или выбери действие в меню):")
    await state.set_state(AddBodyFSM.surname)

# ============================================================
# УДАЛЕНИЕ ТЕЛА
# ============================================================

@router.message(F.text == "🗑️ Удалить тело")
async def start_remove_body(message: types.Message, state: FSMContext):
    if not check_perm(message.from_user.id, "remove"):
        await message.answer("⚠️ Нет прав."); return
    
    data = await state.get_data()
    if data.get("morgue_id"):
        await _show_bodies_for_removal(message, data["morgue_id"], state)
        return

    user_morgue = get_user_morgue(message.from_user.id)
    if user_morgue:
        await _show_bodies_for_removal(message, user_morgue, state)
    else:
        await message.answer("Морг:", reply_markup=kb_select_morgue_remove())

@router.callback_query(F.data.in_(["rm_m1", "rm_m2"]))
async def rm_select_morgue(cb: types.CallbackQuery, state: FSMContext):
    mid = "morgue1" if cb.data == "rm_m1" else "morgue2"
    await cb.answer()
    await state.clear()
    await _show_bodies_for_removal(cb.message, mid, state)

async def _show_bodies_for_removal(message, morgue_id: str, state: FSMContext):
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()
    if not shift or not shift.get("bodies"):
        await message.answer("⚠️ Нет тел в смене."); return
    active = [b for b in shift["bodies"] if not b.get("removed")]
    if not active:
        await message.answer("⚠️ Все тела удалены."); return
    await state.update_data(morgue_id=morgue_id, shift_id=shift["shift_id"])
    await message.answer("Выберите тело:", reply_markup=kb_bodies_for_removal(active))
    await state.set_state(RemoveBodyFSM.reason)

@router.callback_query(F.data.startswith("rm_body_"))
async def rm_select_body(cb: types.CallbackQuery, state: FSMContext):
    idx = int(cb.data.split("_")[-1])
    await state.update_data(body_index=idx)
    await cb.message.edit_text("Причина:", reply_markup=kb_removal_reason())
    await cb.answer()
    await state.set_state(RemoveBodyFSM.reason)

@router.callback_query(F.data == "rmreason_bsme")
async def rm_reason_bsme(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    db = MORGUE_DBS[data["morgue_id"]]
    shift = db.get_active_shift()
    if shift:
        bodies = shift["bodies"]
        real_i = find_real_index(bodies, data["body_index"])
        if real_i is not None:
            bodies[real_i]["removed"] = True
            bodies[real_i]["removed_reason"] = "БСМЭ"
            db.update_shift(shift["shift_id"], shift)
    await cb.message.edit_text("✅ Удалено. Причина: БСМЭ")
    await cb.answer()
    await state.clear()
    user = get_user(cb.from_user.id)
    role = user["role"] if user else "admin"
    await cb.message.answer("Далее:", reply_markup=kb_main_menu(role))

@router.callback_query(F.data == "rmreason_other")
async def rm_reason_other(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.edit_text("Введи причину:")
    await cb.answer()
    await state.set_state(RemoveBodyFSM.custom_reason)

@router.message(RemoveBodyFSM.custom_reason, ~F.text.in_(MENU_BUTTONS))
async def rm_custom_reason(message: types.Message, state: FSMContext):
    reason = message.text.strip()
    if not reason:
        await message.answer("⚠️ Введи причину:"); return
    data = await state.get_data()
    db = MORGUE_DBS[data["morgue_id"]]
    shift = db.get_active_shift()
    if shift:
        bodies = shift["bodies"]
        real_i = find_real_index(bodies, data["body_index"])
        if real_i is not None:
            bodies[real_i]["removed"] = True
            bodies[real_i]["removed_reason"] = reason
            db.update_shift(shift["shift_id"], shift)
    await message.answer(f"✅ Удалено. Причина: {reason}")
    await state.clear()
    user = get_user(message.from_user.id)
    role = user["role"] if user else "admin"
    await message.answer("Далее:", reply_markup=kb_main_menu(role))

# ============================================================
# ЗАКРЫТИЕ СМЕНЫ
# ============================================================

@router.message(F.text == "🔒 Закрыть смена")
async def start_close_shift(message: types.Message, state: FSMContext):
    if not check_perm(message.from_user.id, "close"):
        await message.answer("⚠️ Нет прав."); return
    
    data = await state.get_data()
    if data.get("morgue_id"):
        await _do_close_shift(message, data["morgue_id"], state)
        return

    user_morgue = get_user_morgue(message.from_user.id)
    if user_morgue:
        await _do_close_shift(message, user_morgue, state)
    else:
        await message.answer("Морг:", reply_markup=kb_select_morgue_close())

@router.callback_query(F.data == "close_m1")
async def do_close_m1(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await _do_close_shift(cb.message, "morgue1", state)

@router.callback_query(F.data == "close_m2")
async def do_close_m2(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await _do_close_shift(cb.message, "morgue2", state)

async def _do_close_shift(message, morgue_id: str, state: FSMContext):
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()
    if not shift:
        await message.answer("⚠️ Нет активной смены."); return
    if not shift.get("bodies"):
        await message.answer("⚠️ Смена пуста."); return
    active = [b for b in shift["bodies"] if not b.get("removed")]
    if not active:
        await message.answer("⚠️ Все тела удалены."); return
    await state.update_data(morgue_id=morgue_id, shift_id=shift["shift_id"])
    await message.answer(
        f"{MORGUE_CONFIG[morgue_id]['name']}\nНажми на фамилию:",
        reply_markup=kb_payment_status(active)
    )
    await state.set_state(CloseShiftFSM.payment)

@router.callback_query(F.data.startswith("pay_"))
async def toggle_pay(cb: types.CallbackQuery, state: FSMContext):
    idx = int(cb.data.split("_")[-1])
    data = await state.get_data()
    db = MORGUE_DBS[data["morgue_id"]]
    shift = db.get_active_shift()
    if shift:
        bodies = shift["bodies"]
        real_i = find_real_index(bodies, idx)
        if real_i is not None:
            bodies[real_i]["paid"] = not bodies[real_i].get("paid", False)
            db.update_shift(shift["shift_id"], shift)
            active = [b for b in bodies if not b.get("removed")]
    await cb.message.edit_reply_markup(reply_markup=kb_payment_status(active))
    await cb.answer()

@router.callback_query(F.data == "calc_done")
async def calc_done(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    db = MORGUE_DBS[data["morgue_id"]]
    shift = db.get_active_shift()
    if not shift:
        await cb.message.answer("⚠️ Смена не найдена."); await cb.answer(); return
    active = [b for b in shift["bodies"] if not b.get("removed")]
    unpaid = [b for b in active if not b.get("paid")]
    if unpaid:
        first = unpaid[0]
        first_idx = active.index(first)
        await state.update_data(unpaid_index=first_idx)
        await cb.message.edit_text(f"Кто вывез {first['surname']}?\nОрганизация:")
        await cb.answer()
        await state.set_state(CloseShiftFSM.org_input)
    else:
        await cb.answer()
        await _finish_close(cb.message, data["morgue_id"], data["shift_id"], state, 0)

@router.message(CloseShiftFSM.org_input, ~F.text.in_(MENU_BUTTONS))
async def org_input(message: types.Message, state: FSMContext):
    org = message.text.strip().upper()
    if not org:
        await message.answer("⚠️ Введи организацию:"); return
    data = await state.get_data()
    db = MORGUE_DBS[data["morgue_id"]]
    shift = db.get_active_shift()
    if shift:
        bodies = shift["bodies"]
        active = [b for b in bodies if not b.get("removed")]
        idx = data.get("unpaid_index", 0)
        real_i = find_real_index(bodies, idx)
        if real_i is not None:
            bodies[real_i]["organization"] = org
            db.update_shift(shift["shift_id"], shift)
    unpaid = [b for b in active if not b.get("paid") and not b.get("organization")]
    if unpaid:
        next_unpaid = unpaid[0]
        next_idx = active.index(next_unpaid)
        await state.update_data(unpaid_index=next_idx)
        await message.answer(f"Кто вывез {next_unpaid['surname']}?\nОрганизация:")
    else:
        await message.answer("✅ Организации указаны.")
        await _finish_close(message, data["morgue_id"], data["shift_id"], state, 0)

async def _finish_close(message, morgue_id: str, shift_id: str, state: FSMContext, agent_salary: int):
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()
    if not shift:
        await message.answer("⚠️ Смена не найдена."); return
    shift["agent_salary"] = agent_salary
    user = get_user(message.from_user.id)
    name = user.get("name", "Unknown") if user else "Unknown"
    db.close_shift(shift_id, message.from_user.id, name)
    gh_backup.backup_shift(shift, morgue_id)
    report = format_shift_report(shift, morgue_id)
    await message.answer(report)
    orders = shift.get("orders", [])
    if orders:
        orders_text = "\n━━━━ ЗАКАЗЫ В СМЕНЕ ━━━━\n"
        for order in orders:
            icon = "⚰️" if order.get("type") == "funeral" else "🔥"
            label = "Похороны" if order.get("type") == "funeral" else "Кремация"
            orders_text += f"{icon} {order.get('deceased', '?')} — {label} — {order.get('event_date', '?')}\n"
        await message.answer(orders_text)
    await state.clear()
    role = user["role"] if user else "admin"
    await message.answer("Далее:", reply_markup=kb_main_menu(role))
