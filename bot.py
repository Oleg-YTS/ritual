"""
Telegram-бот для учёта моргов и ритуальных услуг
Версия: 8.0 — Полная реализация по ТЗ
"""

import os
import sys
import logging
import asyncio
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Импорт локальных модулей
sys.path.insert(0, os.path.dirname(__file__))
from database.storage import UsersStorage, MorgueStorage
from database.github_backup import gh_backup
from keyboards.menus import *
from utils.reports import MORGUE_CONFIG, calculate_shift_finances, format_shift_report
from utils.reports import build_driver_card, build_crematorium_card
from utils.reports import generate_removed_report, generate_period_report

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN не найден!")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Хранилища
users_db = UsersStorage()
morgue1_db = MorgueStorage("morgue1")
morgue2_db = MorgueStorage("morgue2")

MORGUE_DBS = {
    "morgue1": morgue1_db,
    "morgue2": morgue2_db
}

# Временное хранилище заказов в памяти (для отправки карточек)
active_orders: List[Dict[str, Any]] = []

# ============================================================
# FSM STATES
# ============================================================
class BodyFSM(StatesGroup):
    """Состояния для добавления тела"""
    select_morgue = State()
    surname = State()
    body_type = State()
    source = State()


class RemovalFSM(StatesGroup):
    """Состояния для удаления тела"""
    select_body = State()
    reason = State()
    custom_reason = State()


class ClosingFSM(StatesGroup):
    """Состояния для закрытия смены"""
    select_morgue = State()
    payment_mark = State()
    org_input = State()
    agent_salary = State()
    confirm_close = State()


class RitualFSM(StatesGroup):
    """Состояния для ритуального заказа"""
    order_type = State()
    event_date = State()
    customer_name = State()
    customer_phone = State()
    deceased_name = State()
    morgue_location = State()
    other_location = State()
    # Похороны
    coffin = State()
    temple = State()
    cemetery = State()
    # Кремация
    cremation_date = State()
    urn_type = State()
    urn_color = State()
    extras = State()
    temple_cremation = State()


class UserManagementFSM(StatesGroup):
    """Состояния для управления пользователями"""
    add_user_id = State()
    add_user_name = State()
    add_user_role = State()
    remove_user_id = State()


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def get_user_role(telegram_id: int) -> Optional[str]:
    """Получение роли пользователя"""
    user = users_db.get_user(telegram_id)
    return user.get("role") if user else None


def get_user_name(telegram_id: int) -> Optional[str]:
    """Получение имени пользователя"""
    user = users_db.get_user(telegram_id)
    return user.get("name") if user else None


def get_user_morgue(telegram_id: int) -> Optional[str]:
    """Определение морга пользователя по роли"""
    role = get_user_role(telegram_id)
    if role == "admin":
        return None  # Админ работает с обоими моргами
    elif role in ["manager_morg1", "agent_morg1"]:
        return "morgue1"
    elif role in ["manager_morg2", "agent_morg2"]:
        return "morgue2"
    return None


def check_permission(telegram_id: int, action: str, morgue_id: str = None) -> bool:
    """Проверка прав доступа"""
    role = get_user_role(telegram_id)
    if not role:
        return False
    
    permissions = {
        "admin": ["add_body", "remove_body", "close_shift", "view_stats", 
                  "view_report", "view_removed", "create_order", "send_cards",
                  "manage_users"],
        "manager_morg1": ["add_body", "remove_body", "close_shift", 
                          "view_report", "view_removed", "create_order", "send_cards"],
        "manager_morg2": ["add_body", "remove_body", "close_shift",
                          "view_report", "view_removed", "create_order", "send_cards"],
        "agent_morg1": ["add_body", "create_order", "send_cards"],
        "agent_morg2": ["add_body", "create_order", "send_cards"]
    }
    
    user_perms = permissions.get(role, [])
    if action not in user_perms:
        return False
    
    # Проверка доступа к конкретному моргу
    if morgue_id:
        user_morgue = get_user_morgue(telegram_id)
        if role != "admin" and user_morgue != morgue_id:
            return False
    
    return True


def get_or_create_shift(telegram_id: int, morgue_id: str) -> Dict[str, Any]:
    """Получение или создание активной смены"""
    db = MORGUE_DBS.get(morgue_id)
    if not db:
        return None
    
    shift = db.get_active_shift()
    if not shift:
        user_name = get_user_name(telegram_id)
        shift = db.create_shift(telegram_id, user_name)
        logger.info(f"Создана новая смена {shift['shift_id']} в {morgue_id}")
    
    return shift


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    """Команда /start"""
    telegram_id = message.from_user.id
    user = users_db.get_user(telegram_id)
    
    if not user:
        await message.answer(
            f"⚠️ Вас нет в списке пользователей.\n"
            f"Ваш Telegram ID: {telegram_id}\n"
            f"Обратитесь к администратору для добавления."
        )
        return
    
    role = user["role"]
    name = user["name"]
    
    role_names = {
        "admin": "Администратор",
        "manager_morg1": "Менеджер (Первомайская 13)",
        "manager_morg2": "Менеджер (Мира 11)",
        "agent_morg1": "Агент (Первомайская 13)",
        "agent_morg2": "Агент (Мира 11)"
    }
    
    role_name = role_names.get(role, role)
    
    await message.answer(
        f"👋 Добро пожаловать, {name}!\n"
        f"📋 Ваша роль: {role_name}\n\n"
        f"Выберите действие из меню:",
        reply_markup=kb_main_menu(role)
    )


# ============================================================
# ДОБАВЛЕНИЕ ТЕЛА
# ============================================================
@dp.message(F.text == "➕ Добавить тело")
async def start_add_body(message: types.Message, state: FSMContext):
    """Начало добавления тела"""
    telegram_id = message.from_user.id
    
    if not check_permission(telegram_id, "add_body"):
        await message.answer("⚠️ У вас нет прав для этого действия.")
        return
    
    user_morgue = get_user_morgue(telegram_id)
    
    if user_morgue:
        # У пользователя привязка к одному моргу
        await state.update_data(morgue_id=user_morgue)
        await message.answer("Введите фамилию умершего:")
        await state.set_state(BodyFSM.surname)
    else:
        # Админ выбирает морг
        await message.answer("Выберите морг:", reply_markup=kb_select_morgue())
        await state.set_state(BodyFSM.select_morgue)


@dp.callback_query(F.data.in_(["morgue1", "morgue2"]), BodyFSM.select_morgue)
async def select_morgue(callback: types.CallbackQuery, state: FSMContext):
    """Выбор морга админом"""
    morgue_id = callback.data
    await state.update_data(morgue_id=morgue_id)
    
    morgue_name = MORGUE_CONFIG[morgue_id]["name"]
    await callback.message.edit_text(f"🏥 {morgue_name}\n\nВведите фамилию умершего:")
    await callback.answer()
    await state.set_state(BodyFSM.surname)


@dp.message(BodyFSM.surname)
async def input_surname(message: types.Message, state: FSMContext):
    """Ввод фамилии"""
    surname = message.text.strip().upper()
    if not surname:
        await message.answer("⚠️ Введите фамилию:")
        return
    
    await state.update_data(surname=surname)
    await message.answer("Выберите тип:", reply_markup=kb_body_type())
    await state.set_state(BodyFSM.body_type)


@dp.callback_query(F.data.in_(["body_std", "body_nstd"]), BodyFSM.body_type)
async def select_body_type(callback: types.CallbackQuery, state: FSMContext):
    """Выбор типа тела"""
    body_type = "std" if callback.data == "body_std" else "nstd"
    type_name = "Стандарт" if body_type == "std" else "Не стандарт"
    
    await state.update_data(body_type=body_type)
    await callback.message.edit_text(f"Тип: {type_name}\n\nВыберите источник поступления:", reply_markup=kb_body_source())
    await callback.answer()
    await state.set_state(BodyFSM.source)


@dp.callback_query(F.data.in_(["source_stat", "source_amb"]), BodyFSM.source)
async def select_source(callback: types.CallbackQuery, state: FSMContext):
    """Выбор источника поступления"""
    source = "stat" if callback.data == "source_stat" else "amb"
    source_name = "Стационар" if source == "stat" else "Амбулаторно"
    
    data = await state.get_data()
    morgue_id = data.get("morgue_id")
    surname = data.get("surname")
    body_type = data.get("body_type")
    
    # Получаем или создаём смену
    shift = get_or_create_shift(callback.from_user.id, morgue_id)
    
    # Добавляем тело в смену
    body_data = {
        "surname": surname,
        "type": body_type,
        "source": source,
        "paid": False,
        "removed": False,
        "organization": ""
    }
    
    db = MORGUE_DBS[morgue_id]
    db.add_body(shift["shift_id"], body_data)
    
    await callback.message.edit_text(f"✅ {surname} ({source_name}) добавлен(а) в смену.")
    await callback.answer()
    
    # Предлагаем добавить ещё или закрыть
    await callback.message.answer(
        "Тело добавлено.\n\n"
        "Добавить ещё одно тело или перейти в главное меню?",
        reply_markup=kb_main_menu(get_user_role(callback.from_user.id))
    )
    await state.clear()


# ============================================================
# УДАЛЕНИЕ ТЕЛА
# ============================================================
@dp.message(F.text == "🗑️ Удалить тело")
async def start_remove_body(message: types.Message, state: FSMContext):
    """Начало удаления тела"""
    telegram_id = message.from_user.id
    
    if not check_permission(telegram_id, "remove_body"):
        await message.answer("⚠️ У вас нет прав для удаления тел.")
        return
    
    user_morgue = get_user_morgue(telegram_id)
    
    if user_morgue:
        await show_bodies_for_removal(message, user_morgue, state)
    else:
        await message.answer("Выберите морг:", reply_markup=kb_select_morgue())
        await state.set_state(RemovalFSM.select_body)


async def show_bodies_for_removal(message, morgue_id: str, state: FSMContext):
    """Показ списка тел для удаления"""
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()
    
    if not shift or not shift.get("bodies"):
        await message.answer("⚠️ В смене нет тел для удаления.")
        return
    
    active_bodies = [b for b in shift["bodies"] if not b.get("removed")]
    if not active_bodies:
        await message.answer("⚠️ Все тела уже удалены или отсутствуют.")
        return
    
    await state.update_data(morgue_id=morgue_id, shift_id=shift["shift_id"])
    await message.answer("Выберите тело для удаления:", reply_markup=kb_bodies_list(active_bodies))
    await state.set_state(RemovalFSM.select_body)


@dp.callback_query(F.data.startswith("body_select_"), RemovalFSM.select_body)
async def select_body_for_removal(callback: types.CallbackQuery, state: FSMContext):
    """Выбор тела для удаления"""
    body_index = int(callback.data.split("_")[-1])
    await state.update_data(body_index=body_index)
    
    await callback.message.edit_text("Выберите причину удаления:", reply_markup=kb_removal_reason())
    await callback.answer()
    await state.set_state(RemovalFSM.reason)


@dp.callback_query(F.data.in_(["remove_bsme", "remove_other"]), RemovalFSM.reason)
async def select_removal_reason(callback: types.CallbackQuery, state: FSMContext):
    """Выбор причины удаления"""
    reason = "БСМЭ" if callback.data == "remove_bsme" else ""
    
    if reason:
        data = await state.get_data()
        morgue_id = data.get("morgue_id")
        shift_id = data.get("shift_id")
        body_index = data.get("body_index")
        
        db = MORGUE_DBS[morgue_id]
        db.remove_body(shift_id, body_index, reason)
        
        await callback.message.edit_text(f"✅ Тело помечено как удалённое. Причина: {reason}")
        await callback.answer()
        await state.clear()
    else:
        await callback.message.edit_text("Введите причину удаления:")
        await callback.answer()
        await state.set_state(RemovalFSM.custom_reason)


@dp.message(RemovalFSM.custom_reason)
async def input_custom_removal_reason(message: types.Message, state: FSMContext):
    """Ввод пользовательской причины удаления"""
    reason = message.text.strip()
    if not reason:
        await message.answer("⚠️ Введите причину:")
        return
    
    data = await state.get_data()
    morgue_id = data.get("morgue_id")
    shift_id = data.get("shift_id")
    body_index = data.get("body_index")
    
    db = MORGUE_DBS[morgue_id]
    db.remove_body(shift_id, body_index, reason)
    
    await message.answer(f"✅ Тело помечено как удалённое. Причина: {reason}")
    await state.clear()


# ============================================================
# ЗАКРЫТИЕ СМЕНЫ
# ============================================================
@dp.message(F.text == "🔒 Закрыть смену")
async def start_close_shift(message: types.Message, state: FSMContext):
    """Начало закрытия смены"""
    telegram_id = message.from_user.id

    if not check_permission(telegram_id, "close_shift"):
        await message.answer("⚠️ У вас нет прав для закрытия смены.")
        return

    user_morgue = get_user_morgue(telegram_id)

    if user_morgue:
        await start_shift_closing(message, user_morgue, state)
    else:
        await message.answer(
            "Выберите морг для закрытия смены:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏥 Первомайская 13", callback_data="close_morgue1")],
                [InlineKeyboardButton(text="🏥 Мира 11", callback_data="close_morgue2")]
            ])
        )
        await state.set_state(ClosingFSM.select_morgue)


@dp.callback_query(F.data.in_(["close_morgue1", "close_morgue2"]), ClosingFSM.select_morgue)
async def select_morgue_for_close(callback: types.CallbackQuery, state: FSMContext):
    """Выбор морга для закрытия смены"""
    morgue_id = "morgue1" if callback.data == "close_morgue1" else "morgue2"
    await callback.answer()
    await start_shift_closing(callback.message, morgue_id, state)


async def start_shift_closing(message, morgue_id: str, state: FSMContext):
    """Начало процесса закрытия смены"""
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()
    
    if not shift:
        await message.answer("⚠️ Нет активной смены для закрытия.")
        return
    
    if not shift.get("bodies"):
        await message.answer("⚠️ В смене нет тел. Добавьте хотя бы одно тело перед закрытием.")
        return
    
    await state.update_data(morgue_id=morgue_id, shift_id=shift["shift_id"])
    
    # Показываем список для отметки оплаты
    bodies = shift["bodies"]
    active_bodies = [b for b in bodies if not b.get("removed")]
    
    if not active_bodies:
        await message.answer("⚠️ Все тела удалены. Невозможно закрыть смену.")
        return
    
    await message.answer(
        f"📋 {MORGUE_CONFIG[morgue_id]['name']} — Отметка оплаты:\n"
        f"Нажмите на фамилию для переключения статуса оплаты.",
        reply_markup=kb_payment_status(active_bodies)
    )
    await state.set_state(ClosingFSM.payment_mark)


@dp.callback_query(F.data.startswith("payment_"), ClosingFSM.payment_mark)
async def toggle_payment(callback: types.CallbackQuery, state: FSMContext):
    """Переключение статуса оплаты"""
    body_index = int(callback.data.split("_")[-1])
    data = await state.get_data()
    morgue_id = data.get("morgue_id")
    shift_id = data.get("shift_id")
    
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()
    
    if shift and shift["shift_id"] == shift_id:
        bodies = shift["bodies"]
        active_bodies = [b for b in bodies if not b.get("removed")]
        
        if 0 <= body_index < len(active_bodies):
            # Находим реальный индекс в полном списке
            real_index = None
            active_counter = 0
            for i, b in enumerate(bodies):
                if not b.get("removed"):
                    if active_counter == body_index:
                        real_index = i
                        break
                    active_counter += 1
            
            if real_index is not None:
                bodies[real_index]["paid"] = not bodies[real_index].get("paid", False)
                db.write({**shift, "bodies": bodies})
    
    # Обновляем клавиатуру
    active_bodies = [b for b in bodies if not b.get("removed")]
    await callback.message.edit_reply_markup(reply_markup=kb_payment_status(active_bodies))
    await callback.answer()


@dp.callback_query(F.data == "calc_shift", ClosingFSM.payment_mark)
async def calculate_shift(callback: types.CallbackQuery, state: FSMContext):
    """Расчёт смены после отметки оплаты"""
    data = await state.get_data()
    morgue_id = data.get("morgue_id")
    shift_id = data.get("shift_id")
    
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()
    
    if not shift:
        await callback.message.answer("⚠️ Смена не найдена.")
        await callback.answer()
        return
    
    bodies = shift["bodies"]
    active_bodies = [b for b in bodies if not b.get("removed")]
    
    # Проверяем, у всех ли оплаченных стоит организация для неоплаченных
    unpaid_bodies = [b for b in active_bodies if not b.get("paid")]
    
    if unpaid_bodies:
        # Нужно ввести организацию для каждого неоплаченного
        first_unpaid = unpaid_bodies[0]
        first_index = active_bodies.index(first_unpaid)
        
        await state.update_data(unpaid_index=first_index, unpaid_bodies=unpaid_bodies)
        await callback.message.edit_text(f"Кто вывез {first_unpaid['surname']}?\nВведите название организации:")
        await callback.answer()
        await state.set_state(ClosingFSM.org_input)
    else:
        # Все оплачены - проверяем заказы
        await callback.answer()
        await check_orders_and_salary(callback.message, morgue_id, shift_id, state)


@dp.message(ClosingFSM.org_input)
async def input_organization(message: types.Message, state: FSMContext):
    """Ввод организации для неоплаченного тела"""
    org = message.text.strip().upper()
    if not org:
        await message.answer("⚠️ Введите название организации:")
        return
    
    data = await state.get_data()
    morgue_id = data.get("morgue_id")
    shift_id = data.get("shift_id")
    unpaid_index = data.get("unpaid_index", 0)
    
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()
    
    if shift:
        bodies = shift["bodies"]
        active_bodies = [b for b in bodies if not b.get("removed")]
        
        if unpaid_index < len(active_bodies):
            # Находим реальный индекс
            real_index = None
            active_counter = 0
            for i, b in enumerate(bodies):
                if not b.get("removed"):
                    if active_counter == unpaid_index:
                        real_index = i
                        break
                    active_counter += 1
            
            if real_index is not None:
                bodies[real_index]["organization"] = org
                db.write({**shift, "bodies": bodies})
    
    # Проверяем следующие неоплаченные
    unpaid_bodies = data.get("unpaid_bodies", [])
    current_idx = unpaid_bodies.index(unpaid_bodies[0]) if unpaid_bodies else 0
    remaining_unpaid = unpaid_bodies[current_idx + 1:]
    
    if remaining_unpaid:
        next_unpaid = remaining_unpaid[0]
        next_index = active_bodies.index(next_unpaid)
        
        await state.update_data(unpaid_index=next_index, unpaid_bodies=remaining_unpaid)
        await message.answer(f"Кто вывез {next_unpaid['surname']}?\nВведите название организации:")
    else:
        await message.answer("✅ Организации указаны для всех неоплаченных.")
        await check_orders_and_salary(message, morgue_id, shift_id, state)


async def check_orders_and_salary(message, morgue_id: str, shift_id: str, state: FSMContext):
    """Проверка заказов и запрос зарплаты агента"""
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()
    
    if not shift:
        return
    
    orders = shift.get("orders", [])
    
    if orders:
        # Есть заказы - запрашиваем зарплату агента
        await state.update_data(order_idx=0, orders=orders, total_salary=0)
        first_order = orders[0]
        order_type = "Похороны" if first_order.get("type") == "funeral" else "Кремация"
        
        await message.answer(
            f"💰 В смене есть заказы на ритуальные услуги.\n\n"
            f"{order_type} ({first_order.get('deceased', 'Без имени')})\n"
            f"Введите зарплату агента (число):"
        )
        await state.set_state(ClosingFSM.agent_salary)
    else:
        # Заказов нет - сразу закрываем смену
        await close_shift_final(message, morgue_id, shift_id, state, 0)


@dp.message(ClosingFSM.agent_salary)
async def input_agent_salary(message: types.Message, state: FSMContext):
    """Ввод зарплаты агента"""
    try:
        salary = int(message.text.strip())
        if salary < 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Введите положительное число:")
        return
    
    data = await state.get_data()
    total_salary = data.get("total_salary", 0) + salary
    order_idx = data.get("order_idx", 0) + 1
    orders = data.get("orders", [])
    
    if order_idx < len(orders):
        # Запрашиваем зарплату для следующего заказа
        next_order = orders[order_idx]
        order_type = "Похороны" if next_order.get("type") == "funeral" else "Кремация"
        
        await state.update_data(order_idx=order_idx, total_salary=total_salary)
        await message.answer(
            f"{order_type} ({next_order.get('deceased', 'Без имени')})\n"
            f"Введите зарплату агента (число):"
        )
    else:
        # Все зарплаты введены - закрываем смену
        morgue_id = data.get("morgue_id")
        shift_id = data.get("shift_id")
        await message.answer(f"✅ Зарплата агента записана: {total_salary}₽")
        await close_shift_final(message, morgue_id, shift_id, state, total_salary)


async def close_shift_final(message, morgue_id: str, shift_id: str, state: FSMContext, agent_salary: int):
    """Финальное закрытие смены"""
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()

    if not shift:
        await message.answer("⚠️ Смена не найдена.")
        return

    # Сохраняем зарплату агента
    shift["agent_salary"] = agent_salary

    # Закрываем смену
    user_name = get_user_name(message.from_user.id)
    db.close_shift(shift_id, message.from_user.id, user_name)

    # БЭКАП В GITHUB
    backup_ok = gh_backup.backup_shift(shift, morgue_id)

    # Формируем отчёт
    report = format_shift_report(shift)

    await message.answer(report)

    if backup_ok:
        await message.answer("✅ Данные смены сохранены в GitHub (бэкап)")
    else:
        await message.answer("⚠️ Бэкап в GitHub не выполнен (проверьте GITHUB_TOKEN)")

    await state.clear()


# ============================================================
# РИТУАЛЬНЫЕ ЗАКАЗЫ
# ============================================================
@dp.message(F.text == "🕯️ Ритуальный заказ")
async def start_ritual_order(message: types.Message, state: FSMContext):
    """Начало создания ритуального заказа"""
    telegram_id = message.from_user.id
    
    if not check_permission(telegram_id, "create_order"):
        await message.answer("⚠️ У вас нет прав для создания заказов.")
        return
    
    await message.answer("Выберите тип заказа:", reply_markup=kb_ritual_type())
    await state.set_state(RitualFSM.order_type)


@dp.callback_query(F.data.in_(["ritual_funeral", "ritual_cremation"]), RitualFSM.order_type)
async def select_ritual_type(callback: types.CallbackQuery, state: FSMContext):
    """Выбор типа ритуального заказа"""
    order_type = "funeral" if callback.data == "ritual_funeral" else "cremation"
    await state.update_data(type=order_type, extras=[])
    
    await callback.message.edit_text("📅 Введите дату мероприятия (ДД.ММ.ГГГГ):")
    await callback.answer()
    await state.set_state(RitualFSM.event_date)


@dp.message(RitualFSM.event_date)
async def input_event_date(message: types.Message, state: FSMContext):
    """Ввод даты мероприятия"""
    date_str = message.text.strip()
    
    # Простая валидация
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        await message.answer("⚠️ Введите дату в формате ДД.ММ.ГГГГ:")
        return
    
    await state.update_data(event_date=date_str)
    await message.answer("👤 ФИО заказчика:")
    await state.set_state(RitualFSM.customer_name)


@dp.message(RitualFSM.customer_name)
async def input_customer_name(message: types.Message, state: FSMContext):
    """Ввод ФИО заказчика"""
    name = message.text.strip().upper()
    if not name:
        await message.answer("⚠️ Введите ФИО:")
        return
    
    await state.update_data(customer_name=name)
    await message.answer("☎️ Телефон заказчика:")
    await state.set_state(RitualFSM.customer_phone)


@dp.message(RitualFSM.customer_phone)
async def input_customer_phone(message: types.Message, state: FSMContext):
    """Ввод телефона заказчика"""
    phone = message.text.strip()
    if not phone:
        await message.answer("⚠️ Введите телефон:")
        return
    
    await state.update_data(customer_phone=phone)
    await message.answer("👤 ФИО усопшего:")
    await state.set_state(RitualFSM.deceased_name)


@dp.message(RitualFSM.deceased_name)
async def input_deceased_name(message: types.Message, state: FSMContext):
    """Ввод ФИО усопшего"""
    name = message.text.strip().upper()
    if not name:
        await message.answer("⚠️ Введите ФИО:")
        return
    
    await state.update_data(deceased_name=name)
    
    # Спрашиваем местоположение тела
    await message.answer(
        "📍 Где находится тело?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏥 Первомайская 13", callback_data="loc_morgue1")],
            [InlineKeyboardButton(text="🏥 Мира 11", callback_data="loc_morgue2")],
            [InlineKeyboardButton(text="📍 Другое место", callback_data="loc_other")]
        ])
    )
    await state.set_state(RitualFSM.morgue_location)


@dp.callback_query(F.data.in_(["loc_morgue1", "loc_morgue2", "loc_other"]), RitualFSM.morgue_location)
async def select_morgue_location(callback: types.CallbackQuery, state: FSMContext):
    """Выбор местоположения тела"""
    loc_map = {
        "loc_morgue1": "Первомайская 13",
        "loc_morgue2": "Мира 11",
        "loc_other": "other"
    }
    
    location = loc_map[callback.data]
    await state.update_data(morgue_location=location)
    
    if location == "other":
        await callback.message.edit_text("📍 Введите адрес:")
        await callback.answer()
        await state.set_state(RitualFSM.other_location)
    else:
        data = await state.get_data()
        if data.get("type") == "funeral":
            await callback.message.edit_text("⚰️ Введите тип гроба:")
            await callback.answer()
            await state.set_state(RitualFSM.coffin)
        else:
            await callback.message.edit_text("📅 Введите дату кремации (ДД.ММ.ГГГГ):")
            await callback.answer()
            await state.set_state(RitualFSM.cremation_date)


@dp.message(RitualFSM.other_location)
async def input_other_location(message: types.Message, state: FSMContext):
    """Ввод другого местоположения"""
    location = message.text.strip().upper()
    if not location:
        await message.answer("⚠️ Введите адрес:")
        return
    
    await state.update_data(morgue_location=location)
    
    data = await state.get_data()
    if data.get("type") == "funeral":
        await message.answer("⚰️ Введите тип гроба:")
        await state.set_state(RitualFSM.coffin)
    else:
        await message.answer("📅 Введите дату кремации (ДД.ММ.ГГГГ):")
        await state.set_state(RitualFSM.cremation_date)


# Похороны
@dp.message(RitualFSM.coffin)
async def input_coffin(message: types.Message, state: FSMContext):
    """Ввод типа гроба"""
    coffin = message.text.strip().upper()
    if not coffin:
        await message.answer("⚠️ Введите тип гроба:")
        return
    
    await state.update_data(coffin=coffin)
    await message.answer("⛪ Где отпевают (храм/место):")
    await state.set_state(RitualFSM.temple)


@dp.message(RitualFSM.temple)
async def input_temple(message: types.Message, state: FSMContext):
    """Ввод храма"""
    temple = message.text.strip().upper()
    if not temple:
        await message.answer("⚠️ Введите название храма:")
        return
    
    await state.update_data(temple=temple)
    await message.answer("🪦 Кладбище:")
    await state.set_state(RitualFSM.cemetery)


@dp.message(RitualFSM.cemetery)
async def input_cemetery(message: types.Message, state: FSMContext):
    """Ввод кладбища"""
    cemetery = message.text.strip().upper()
    if not cemetery:
        await message.answer("⚠️ Введите название кладбища:")
        return
    
    await state.update_data(cemetery=cemetery)
    await save_ritual_order(message, state)


# Кремация
@dp.message(RitualFSM.cremation_date)
async def input_cremation_date(message: types.Message, state: FSMContext):
    """Ввод даты кремации"""
    date_str = message.text.strip()
    
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        await message.answer("⚠️ Введите дату в формате ДД.ММ.ГГГГ:")
        return
    
    await state.update_data(cremation_date=date_str)
    await message.answer("📦 Выберите тип урны:", reply_markup=kb_urn_type())
    await state.set_state(RitualFSM.urn_type)


@dp.callback_query(F.data.in_(["urn_cardboard", "urn_plastic"]), RitualFSM.urn_type)
async def select_urn_type(callback: types.CallbackQuery, state: FSMContext):
    """Выбор типа урны"""
    urn_type = "cardboard" if callback.data == "urn_cardboard" else "plastic"
    await state.update_data(urn_type=urn_type)
    
    if urn_type == "plastic":
        await callback.message.edit_text("Выберите цвет урны:", reply_markup=kb_urn_color())
        await callback.answer()
        await state.set_state(RitualFSM.urn_color)
    else:
        await callback.message.edit_text("Выберите дополнительныеные услуги:")
        await state.update_data(extras=[])
        await callback.message.answer("Отметьте нужные услуги:", reply_markup=kb_extras([]))
        await state.set_state(RitualFSM.extras)


@dp.callback_query(F.data == "color_white" or F.data == "color_black" or F.data == "color_blue", RitualFSM.urn_color)
async def select_urn_color(callback: types.CallbackQuery, state: FSMContext):
    """Выбор цвета урны"""
    color_map = {
        "color_white": "Белый",
        "color_black": "Чёрный",
        "color_blue": "Синий"
    }
    
    color = color_map[callback.data]
    await state.update_data(urn_color=color)
    
    await callback.message.edit_text("Выберите дополнительные услуги:")
    await state.update_data(extras=[])
    await callback.message.answer("Отметьте нужные услуги:", reply_markup=kb_extras([]))
    await callback.answer()
    await state.set_state(RitualFSM.extras)


@dp.callback_query(F.data.startswith("extra_"), RitualFSM.extras)
async def handle_extras(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора доп. услуг"""
    if callback.data == "extra_done":
        await callback.answer()
        await save_ritual_order(callback.message, state)
        return
    
    extra_key = callback.data.replace("extra_", "")
    data = await state.get_data()
    extras = data.get("extras", [])
    
    if extra_key in extras:
        extras.remove(extra_key)
    else:
        extras.append(extra_key)
    
    await state.update_data(extras=extras)
    
    # Проверяем, если выбран зал - сразу сохраняем
    if "hall" in extras or "hall_blessing" in extras:
        await state.update_data(temple="Зал отпевания", cemetery="Крематорий")
        await callback.answer()
        await save_ritual_order(callback.message, state)
    else:
        await callback.message.edit_reply_markup(reply_markup=kb_extras(extras))
        await callback.answer()


async def save_ritual_order(message, state: FSMContext):
    """Сохранение ритуального заказа"""
    data = await state.get_data()
    order_type = data.get("type")
    
    # Собираем данные заказа
    order = {
        "order_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "event_date": data.get("event_date", ""),
        "type": order_type,
        "customer_name": data.get("customer_name", ""),
        "customer_phone": data.get("customer_phone", ""),
        "deceased": data.get("deceased_name", ""),
        "morgue_location": data.get("morgue_location", ""),
        "phone": data.get("customer_phone", "")
    }
    
    if order_type == "funeral":
        order["coffin"] = data.get("coffin", "")
        order["temple"] = data.get("temple", "")
        order["cemetery"] = data.get("cemetery", "")
    else:
        order["cremation_date"] = data.get("cremation_date", "")
        order["urn_type"] = data.get("urn_type", "")
        order["urn_color"] = data.get("urn_color", "")
        order["extras"] = data.get("extras", [])
        order["temple"] = data.get("temple", "")
        order["cemetery"] = data.get("cemetery", "")
    
    # Сохраняем в активную смену (если есть)
    user_morgue = get_user_morgue(message.from_user.id)
    if user_morgue:
        db = MORGUE_DBS[user_morgue]
        shift = db.get_active_shift()
        if shift:
            db.add_order(shift["shift_id"], order)
    
    # Добавляем в активные заказы для отправки карточек
    active_orders.append(order)
    
    # Формируем карточки
    driver_card = build_driver_card(order)
    crem_card = build_crematorium_card(order) if order_type == "cremation" else None
    
    response = "✅ Заказ сохранён\n\n"
    response += "━━ 📋 ВОДИТЕЛЮ ━━\n" + driver_card
    
    if crem_card:
        response += "\n\n━━ 🔥 КРЕМАТОРИЙ ━━\n" + crem_card
    
    response += "\n\nИспользуйте кнопку '📋 Мои заказы' для отправки карточек."
    
    await message.answer(response)
    await state.clear()


# ============================================================
# МОИ ЗАКАЗЫ И ОТПРАВКА КАРТОЧЕК
# ============================================================
@dp.message(F.text == "📋 Мои заказы")
async def show_my_orders(message: types.Message, state: FSMContext):
    """Показ списка заказов"""
    telegram_id = message.from_user.id
    
    if not check_permission(telegram_id, "send_cards"):
        await message.answer("⚠️ У вас нет прав для просмотра заказов.")
        return
    
    if not active_orders:
        await message.answer("⚠️ Нет сохранённых заказов.")
        return
    
    if len(active_orders) == 1:
        order = active_orders[0]
        order_type = "🔥" if order.get("type") == "cremation" else "⚰️"
        
        await message.answer(
            f"{order_type} {order.get('deceased', 'Без имени')}",
            reply_markup=kb_order_actions()
        )
    else:
        await message.answer("Выберите заказ:", reply_markup=kb_order_select(active_orders))


@dp.callback_query(F.data.startswith("order_select_"))
async def select_order(callback: types.CallbackQuery, state: FSMContext):
    """Выбор заказа из списка"""
    order_index = int(callback.data.split("_")[-1])
    
    if 0 <= order_index < len(active_orders):
        order = active_orders[order_index]
        order_type = "🔥" if order.get("type") == "cremation" else "⚰️"
        
        await state.update_data(selected_order_index=order_index)
        await callback.message.edit_text(
            f"{order_type} {order.get('deceased', 'Без имени')}",
            reply_markup=kb_order_actions()
        )
    
    await callback.answer()


@dp.callback_query(F.data == "send_driver")
async def send_driver_card(callback: types.CallbackQuery, state: FSMContext):
    """Отправка карточки водителя"""
    data = await state.get_data()
    order_index = data.get("selected_order_index", 0)
    
    if 0 <= order_index < len(active_orders):
        order = active_orders[order_index]
        card = build_driver_card(order)
        await callback.message.answer(card)
    
    await callback.answer("Карточка отправлена")


@dp.callback_query(F.data == "send_crematorium")
async def send_crematorium_card(callback: types.CallbackQuery, state: FSMContext):
    """Отправка карточки крематорию"""
    data = await state.get_data()
    order_index = data.get("selected_order_index", 0)
    
    if 0 <= order_index < len(active_orders):
        order = active_orders[order_index]
        
        if order.get("type") == "cremation":
            card = build_crematorium_card(order)
            await callback.message.answer(card)
        else:
            await callback.message.answer("⚠️ Это заказ на похороны, а не кремацию.")
    
    await callback.answer("Карточка отправлена")


# ============================================================
# ОТЧЁТЫ
# ============================================================
@dp.message(F.text == "📊 Отчёт за период")
async def start_period_report(message: types.Message, state: FSMContext):
    """Начало отчёта за период"""
    telegram_id = message.from_user.id
    
    if not check_permission(telegram_id, "view_report"):
        await message.answer("⚠️ У вас нет прав для просмотра отчётов.")
        return
    
    await message.answer("Выберите период:", reply_markup=kb_report_period())
    await state.set_state(BodyFSM.select_morgue)  # Переиспользуем состояние


@dp.callback_query(F.data.in_(["period_week", "period_month", "period_quarter"]))
async def select_report_period(callback: types.CallbackQuery, state: FSMContext):
    """Выбор периода отчёта"""
    period_map = {
        "period_week": 7,
        "period_month": 30,
        "period_quarter": 90
    }
    
    days = period_map[callback.data]
    user_morgue = get_user_morgue(callback.from_user.id)
    role = get_user_role(callback.from_user.id)
    
    if role == "admin":
        # Админ выбирает морг
        await state.update_data(period_days=days)
        await callback.message.edit_text("Выберите морг для отчёта:", reply_markup=kb_select_morgue())
    else:
        await callback.answer()
        await generate_report(callback.message, user_morgue, days, state)


@dp.callback_query(F.data.in_(["morgue1", "morgue2"]))
async def select_morgue_for_report(callback: types.CallbackQuery, state: FSMContext):
    """Выбор морга для отчёта"""
    morgue_id = callback.data
    data = await state.get_data()
    period_days = data.get("period_days", 7)
    
    await callback.answer()
    await generate_report(callback.message, morgue_id, period_days, state)


async def generate_report(message, morgue_id: str, period_days: int, state: FSMContext):
    """Генерация отчёта"""
    db = MORGUE_DBS[morgue_id]
    shifts = db.get_shifts()
    
    report = generate_period_report(shifts, period_days, morgue_id)
    await message.answer(report)
    await state.clear()


# ============================================================
# АНАЛИТИКА «КТО ВЫВЕЗ»
# ============================================================
@dp.message(F.text == "🚗 Кто вывез")
async def start_removed_report(message: types.Message, state: FSMContext):
    """Начало отчёта «кто вывез»"""
    telegram_id = message.from_user.id
    
    if not check_permission(telegram_id, "view_removed"):
        await message.answer("⚠️ У вас нет прав для просмотра этого отчёта.")
        return
    
    await message.answer("Выберите период:", reply_markup=kb_report_period())
    await state.set_state(BodyFSM.select_morgue)


@dp.callback_query(F.data.in_(["period_week", "period_month", "period_quarter"]))
async def select_removed_period(callback: types.CallbackQuery, state: FSMContext):
    """Выбор периода для отчёта «кто вывез»"""
    period_map = {
        "period_week": 7,
        "period_month": 30,
        "period_quarter": 90
    }
    
    days = period_map[callback.data]
    user_morgue = get_user_morgue(callback.from_user.id)
    role = get_user_role(callback.from_user.id)
    
    if role == "admin":
        await state.update_data(period_days=days)
        await callback.message.edit_text("Выберите морг:", reply_markup=kb_select_morgue())
    else:
        await callback.answer()
        await generate_removed_callback(callback.message, user_morgue, days, state)


@dp.callback_query(F.data.in_(["morgue1", "morgue2"]))
async def select_morgue_for_removed(callback: types.CallbackQuery, state: FSMContext):
    """Выбор морга для отчёта «кто вывез»"""
    morgue_id = callback.data
    data = await state.get_data()
    period_days = data.get("period_days", 7)
    
    await callback.answer()
    await generate_removed_callback(callback.message, morgue_id, period_days, state)


async def generate_removed_callback(message, morgue_id: str, period_days: int, state: FSMContext):
    """Генерация отчёта «кто вывез»"""
    db = MORGUE_DBS[morgue_id]
    shifts = db.get_shifts()
    
    report = generate_removed_report(shifts, period_days)
    await message.answer(report)
    await state.clear()


# ============================================================
# СТАТИСТИКА ДЛЯ АДМИНА
# ============================================================
@dp.message(F.text == "📈 Статистика")
async def start_admin_stats(message: types.Message, state: FSMContext):
    """Начало статистики для админа"""
    telegram_id = message.from_user.id
    
    if not check_permission(telegram_id, "view_stats"):
        await message.answer("⚠️ Только для администратора.")
        return
    
    await message.answer("Выберите морг для статистики:", reply_markup=kb_admin_stats())


@dp.callback_query(F.data.in_(["stats_morgue1", "stats_morgue2", "stats_both"]))
async def select_stats_morgue(callback: types.CallbackQuery, state: FSMContext):
    """Выбор морга для статистики"""
    morgue_map = {
        "stats_morgue1": "morgue1",
        "stats_morgue2": "morgue2",
        "stats_both": None
    }
    
    morgue_id = morgue_map[callback.data]
    
    # Собираем статистику
    if morgue_id:
        db = MORGUE_DBS[morgue_id]
        shifts = db.get_shifts()
        report = generate_period_report(shifts, 365, morgue_id)  # За год
    else:
        # Оба морга
        all_shifts = []
        for db in MORGUE_DBS.values():
            all_shifts.extend(db.get_shifts())
        report = generate_period_report(all_shifts, 365)
    
    await callback.message.answer(report)
    await callback.answer()


# ============================================================
# УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ
# ============================================================
@dp.message(F.text == "👥 Пользователи")
async def start_user_management(message: types.Message, state: FSMContext):
    """Начало управления пользователями"""
    telegram_id = message.from_user.id
    
    if not check_permission(telegram_id, "manage_users"):
        await message.answer("⚠️ Только для администратора.")
        return
    
    await message.answer("Управление пользователями:", reply_markup=kb_user_management())


@dp.callback_query(F.data == "user_add")
async def start_add_user(callback: types.CallbackQuery, state: FSMContext):
    """Начало добавления пользователя"""
    await callback.message.edit_text("Введите Telegram ID нового пользователя:")
    await callback.answer()
    await state.set_state(UserManagementFSM.add_user_id)


@dp.message(UserManagementFSM.add_user_id)
async def input_user_id(message: types.Message, state: FSMContext):
    """Ввод Telegram ID"""
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введите числовой ID:")
        return
    
    await state.update_data(new_user_id=user_id)
    await message.answer("Введите имя пользователя:")
    await state.set_state(UserManagementFSM.add_user_name)


@dp.message(UserManagementFSM.add_user_name)
async def input_user_name(message: types.Message, state: FSMContext):
    """Ввод имени пользователя"""
    name = message.text.strip()
    if not name:
        await message.answer("⚠️ Введите имя:")
        return
    
    await state.update_data(new_user_name=name)
    await message.answer("Выберите роль:", reply_markup=kb_role_select())
    await state.set_state(UserManagementFSM.add_user_role)


@dp.callback_query(F.data.startswith("role_"), UserManagementFSM.add_user_role)
async def select_user_role(callback: types.CallbackQuery, state: FSMContext):
    """Выбор роли для нового пользователя"""
    role_map = {
        "role_admin": "admin",
        "role_manager_morg1": "manager_morg1",
        "role_manager_morg2": "manager_morg2",
        "role_agent_morg1": "agent_morg1",
        "role_agent_morg2": "agent_morg2"
    }
    
    role = role_map[callback.data]
    data = await state.get_data()
    user_id = data.get("new_user_id")
    user_name = data.get("new_user_name")
    
    users_db.add_user(user_id, role, user_name)
    
    await callback.message.edit_text(f"✅ Пользователь {user_name} (ID: {user_id}) добавлен с ролью: {role}")
    await callback.answer()
    await state.clear()


@dp.callback_query(F.data == "user_list")
async def show_user_list(callback: types.CallbackQuery, state: FSMContext):
    """Показ списка пользователей"""
    users = users_db.get_all_users()
    
    role_names = {
        "admin": "Админ",
        "manager_morg1": "Менеджер М13",
        "manager_morg2": "Менеджер М11",
        "agent_morg1": "Агент М13",
        "agent_morg2": "Агент М11"
    }
    
    text = "👥 СПИСОК ПОЛЬЗОВАТЕЛЕЙ\n"
    text += "━" * 30 + "\n"
    
    for uid, udata in users.items():
        role_name = role_names.get(udata["role"], udata["role"])
        text += f"• ID: {uid} | {udata['name']} | {role_name}\n"
    
    await callback.message.edit_text(text)
    await callback.answer()


@dp.callback_query(F.data == "user_remove")
async def start_remove_user(callback: types.CallbackQuery, state: FSMContext):
    """Начало удаления пользователя"""
    await callback.message.edit_text("Введите Telegram ID пользователя для удаления:")
    await callback.answer()
    await state.set_state(UserManagementFSM.remove_user_id)


@dp.message(UserManagementFSM.remove_user_id)
async def remove_user_by_id(message: types.Message, state: FSMContext):
    """Удаление пользователя по ID"""
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введите числовой ID:")
        return
    
    user = users_db.get_user(user_id)
    if not user:
        await message.answer("⚠️ Пользователь не найден.")
        return
    
    users_db.remove_user(user_id)
    await message.answer(f"✅ Пользователь {user['name']} (ID: {user_id}) удалён.")
    await state.clear()


# ============================================================
# КНОПКА ОТМЕНЫ
# ============================================================
@dp.callback_query(F.data == "cancel")
async def cancel_action(callback: types.CallbackQuery, state: FSMContext):
    """Отмена текущего действия"""
    await state.clear()
    
    role = get_user_role(callback.from_user.id)
    await callback.message.edit_text("❌ Действие отменено.")
    await callback.message.answer("Выберите действие:", reply_markup=kb_main_menu(role))
    await callback.answer()


# ============================================================
# ОБРАБОТКА НЕИЗВЕСТНЫХ СООБЩЕНИЙ
# ============================================================
@dp.message()
async def handle_unknown_message(message: types.Message):
    """Обработка неизвестных сообщений"""
    await message.answer("⚠️ Неизвестная команда. Используйте меню или нажмите /start")


# ============================================================
# ЗАПУСК БОТА
# ============================================================
async def on_startup():
    """Установка webhook при запуске с повторными попытками"""
    url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")

    if not url and host:
        url = f"https://{host}"

    if not url:
        logger.warning("Webhook URL не установлен. Бот не будет получать обновления через webhook.")
        return

    webhook_url = f"{url}{WEBHOOK_PATH}"
    secret = WEBHOOK_SECRET if WEBHOOK_SECRET else None

    # Retry при DNS-ошибках (Render иногда тупит при старте)
    max_retries = 5
    for attempt in range(max_retries):
        try:
            await bot.set_webhook(webhook_url, secret_token=secret)
            logger.info(f"✅ Webhook установлен: {webhook_url}")
            return
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                logger.warning(f"⚠️ Webhook не установлен (попытка {attempt+1}/{max_retries}): {e}")
                logger.info(f"⏳ Повторная попытка через {wait} сек...")
                await asyncio.sleep(wait)
            else:
                logger.error(f"❌ Не удалось установить webhook после {max_retries} попыток: {e}")
                logger.warning("⚠️ Бот запущен, но webhook не установлен. Проверьте сеть.")


def create_app() -> web.Application:
    """Создание FastAPI приложения для webhook"""
    app = web.Application()
    
    # Health check
    async def health_handler(request):
        return web.Response(text="OK")
    
    app.router.add_get("/health", health_handler)
    app.router.add_get("/", health_handler)
    
    # Webhook handler
    SimpleRequestHandler(
        dp, bot, 
        secret_token=WEBHOOK_SECRET if WEBHOOK_SECRET else None
    ).register(app, path=WEBHOOK_PATH)
    
    setup_application(app, dp, bot=bot)
    
    return app


def main():
    """Главная функция запуска"""
    parser = argparse.ArgumentParser(description="MorgueBot - Telegram бот для учёта моргов")
    parser.add_argument("--polling", action="store_true", help="Запуск в режиме polling (для локальной разработки)")
    args = parser.parse_args()
    
    dp.startup.register(on_startup)
    
    if args.polling:
        logger.info("🚀 Запуск бота в режиме POLLING...")
        logger.info("Нажмите Ctrl+C для остановки.")
        asyncio.run(dp.start_polling(bot, skip_updates=True))
    else:
        logger.info("🚀 Запуск бота в режиме WEBHOOK...")
        app = create_app()
        port = int(os.getenv("PORT", 10000))
        web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
