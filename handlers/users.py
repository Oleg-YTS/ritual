"""
БЛОК 4: ПОЛЬЗОВАТЕЛИ — добавить/удалить/список (только админ)
"""

import os
import sys
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.storage import UsersStorage
from keyboards.menus import kb_main_menu, kb_role_switcher, ALL_MENU_BUTTONS

logger = logging.getLogger(__name__)

router = Router(name="users")

users_db = UsersStorage()

ROLE_NAMES = {
    "admin": "Админ",
    "manager_morg1": "Менеджер М13",
    "manager_morg2": "Менеджер М11",
    "agent_morg1": "Агент М13",
    "agent_morg2": "Агент М11"
}

# ============================================================
# FSM
# ============================================================

class UserFSM(StatesGroup):
    add_id = State()
    add_name = State()
    add_role = State()
    remove_id = State()

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ
# ============================================================

def get_user(tid):
    return users_db.get_user(tid)

# ============================================================
# УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ
# ============================================================

@router.message(F.text == "👥 Пользователи", ~F.text.in_(ALL_MENU_BUTTONS))
async def start_user_mgmt(message: types.Message, state: FSMContext):
    user = get_user(message.from_user.id)
    if not user or user.get("role") != "admin":
        await message.answer("⚠️ Только админ."); return
    await state.clear()
    await message.answer("Управление:", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="➕ Добавить", callback_data="uaction_add")],
        [types.InlineKeyboardButton(text="🗑️ Удалить", callback_data="uaction_remove")],
        [types.InlineKeyboardButton(text="📋 Список", callback_data="uaction_list")]
    ]))

# --- СПИСОК ---
@router.callback_query(F.data == "uaction_list")
async def show_users(cb: types.CallbackQuery, state: FSMContext):
    users = users_db.get_all_users()
    text = "👥 ПОЛЬЗОВАТЕЛИ:\n"
    text += "━" * 30 + "\n"
    for uid, udata in users.items():
        role_name = ROLE_NAMES.get(udata.get("role", ""), udata.get("role", "?"))
        text += f"• {udata['name']} — {role_name} (ID: {uid})\n"
    await cb.message.edit_text(text)
    await cb.answer()
    user = get_user(cb.from_user.id)
    role = user["role"] if user else "admin"
    await cb.message.answer("Далее:", reply_markup=kb_main_menu(role))
    await state.clear()

# --- ДОБАВИТЬ ---
@router.callback_query(F.data == "uaction_add")
async def start_add_user(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.edit_text("Введи Telegram ID нового пользователя:")
    await cb.answer()
    await state.set_state(UserFSM.add_id)

@router.message(UserFSM.add_id, ~F.text.in_(ALL_MENU_BUTTONS))
async def input_add_id(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введи число:"); return
    await state.update_data(new_uid=uid)
    await message.answer("Введи имя:")
    await state.set_state(UserFSM.add_name)

@router.message(UserFSM.add_name, ~F.text.in_(ALL_MENU_BUTTONS))
async def input_add_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("⚠️ Введи имя:"); return
    await state.update_data(new_name=name)
    await message.answer("Выбери роль:", reply_markup=kb_role_switcher())
    await state.set_state(UserFSM.add_role)

@router.callback_query(F.data.startswith("urole_"), UserFSM.add_role)
async def select_role(cb: types.CallbackQuery, state: FSMContext):
    role = cb.data.replace("urole_", "")
    data = await state.get_data()
    uid = data.get("new_uid")
    name = data.get("new_name")

    users_db.add_user(uid, role, name)
    role_name = ROLE_NAMES.get(role, role)

    await cb.message.edit_text(f"✅ {name} (ID: {uid}) добавлен как {role_name}")
    await cb.answer()
    await state.clear()
    await cb.message.answer("Далее:", reply_markup=kb_main_menu("admin"))

# --- УДАЛИТЬ ---
@router.callback_query(F.data == "uaction_remove")
async def start_remove_user(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.edit_text("Введи Telegram ID для удаления:")
    await cb.answer()
    await state.set_state(UserFSM.remove_id)

@router.message(UserFSM.remove_id, ~F.text.in_(ALL_MENU_BUTTONS))
async def input_remove_id(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введи число:"); return

    user = users_db.get_user(uid)
    if not user:
        await message.answer("⚠️ Пользователь не найден."); return

    users_db.remove_user(uid)
    await message.answer(f"✅ {user['name']} (ID: {uid}) удалён.")
    await state.clear()
    await message.answer("Далее:", reply_markup=kb_main_menu("admin"))
