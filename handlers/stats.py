"""
БЛОК 3: СТАТИСТИКА — отчёты, аналитика, статистика
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.storage import UsersStorage, MorgueStorage
from utils.reports import MORGUE_CONFIG, calculate_shift_finances
from keyboards.menus import kb_main_menu, kb_report_period

logger = logging.getLogger(__name__)

router = Router(name="stats")

# ============================================================
# ХРАНИЛИЩА
# ============================================================
users_db = UsersStorage()
morgue1_db = MorgueStorage("morgue1")
morgue2_db = MorgueStorage("morgue2")
MORGUE_DBS = {"morgue1": morgue1_db, "morgue2": morgue2_db}
MORGUE_NAMES = {"morgue1": "Первомайская 13", "morgue2": "Мира 11"}

# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def kb_main_menu(role: str):
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="➕ Добавить тело"))
    if role in ["admin", "manager_morg1", "manager_morg2"]:
        b.row(KeyboardButton(text="🔒 Закрыть смену"))
        b.row(KeyboardButton(text="🗑️ Удалить тело"))
    b.row(KeyboardButton(text="🕯️ Ритуальный заказ"))
    b.row(KeyboardButton(text="📋 Мои заказы"))
    if role in ["admin", "manager_morg1", "manager_morg2"]:
        b.row(KeyboardButton(text="📊 Отчёт за период"))
    if role == "admin":
        b.row(KeyboardButton(text="📈 Статистика"))
        b.row(KeyboardButton(text="👥 Пользователи"))
    return b.as_markup(resize_keyboard=True)

def kb_report_period():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Неделя", callback_data="speriod_week")],
        [InlineKeyboardButton(text="Месяц", callback_data="speriod_month")],
        [InlineKeyboardButton(text="Квартал", callback_data="speriod_quarter")]
    ])

# ============================================================
# FSM
# ============================================================

class StatsFSM(StatesGroup):
    select_period = State()
    select_morgue = State()

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

# ============================================================
# СТАТИСТИКА ПОСЛЕ ЗАКРЫТИЯ СМЕНЫ (добавление к отчёту)
# ============================================================

def build_shift_summary(shift: dict, morgue_id: str) -> str:
    """Дополнительная статистика: заказы в смене"""
    orders = shift.get("orders", [])
    if not orders:
        return ""

    text = "\n━━━━ ЗАКАЗЫ В СМЕНЕ ━━━━\n"
    for order in orders:
        icon = "⚰️" if order.get("type") == "funeral" else "🔥"
        label = "Похороны" if order.get("type") == "funeral" else "Кремация"
        text += f"{icon} {order.get('deceased', 'Без имени')} — {label} — {order.get('event_date', '?')}\n"

    return text

# ============================================================
# ОТЧЁТ ЗА ПЕРИОД
# ============================================================

@router.message(F.text == "📊 Отчёт за период")
async def start_period_report(message: types.Message, state: FSMContext):
    if not check_perm(message.from_user.id, "report"):
        await message.answer("⚠️ Нет прав."); return

    await state.clear()
    user_morgue = get_user_morgue(message.from_user.id)

    if user_morgue:
        await state.update_data(morgue_id=user_morgue)
        await message.answer("Выбери период:", reply_markup=kb_report_period())
    else:
        # Админ выбирает морг + период
        await message.answer(
            "Выбери морг:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Первомайская 13", callback_data="sreport_m1")],
                [InlineKeyboardButton(text="Мира 11", callback_data="sreport_m2")],
                [InlineKeyboardButton(text="Оба морга", callback_data="sreport_both")]
            ])
        )

# --- Админ: выбор морга ---
@router.callback_query(F.data.in_(["sreport_m1", "sreport_m2", "sreport_both"]))
async def admin_select_morgue(cb: types.CallbackQuery, state: FSMContext):
    mid_map = {"sreport_m1": "morgue1", "sreport_m2": "morgue2", "sreport_both": "both"}
    mid = mid_map[cb.data]
    await state.update_data(morgue_id=mid)
    await cb.answer()
    await cb.message.edit_text("Выбери период:", reply_markup=kb_report_period())
    await state.set_state(StatsFSM.select_period)

# --- Период (без стейт-фильтра — ловим всегда) ---
@router.callback_query(F.data.in_(["speriod_week", "speriod_month", "speriod_quarter"]))
async def select_period(cb: types.CallbackQuery, state: FSMContext):
    period_map = {"speriod_week": 7, "speriod_month": 30, "speriod_quarter": 90}
    days = period_map[cb.data]

    data = await state.get_data()
    morgue_id = data.get("morgue_id", "morgue1")

    report = _generate_period_report(days, morgue_id)
    await cb.message.edit_text(report)
    await cb.answer()
    await state.clear()

    user = get_user(cb.from_user.id)
    role = user["role"] if user else "admin"
    await cb.message.answer("Далее:", reply_markup=kb_main_menu(role))

def _generate_period_report(days: int, morgue_id: str) -> str:
    cutoff = datetime.now() - timedelta(days=days)
    period_name = {7: "неделю", 30: "месяц", 90: "квартал"}.get(days, f"{days} дней")

    if morgue_id == "both":
        mids = ["morgue1", "morgue2"]
    else:
        mids = [morgue_id]

    total_income = 0
    total_sanitary = 0
    total_transport = 0
    total_agent_salary = 0
    total_bodies = 0
    total_paid = 0
    total_unpaid = 0
    unstat_list = []
    removed_list = []
    orders_in_period = []

    for mid in mids:
        db = MORGUE_DBS[mid]
        shifts = db.get_shifts()
        for shift in shifts:
            start = datetime.fromisoformat(shift["start_time"]) if shift.get("start_time") else None
            if start and start < cutoff:
                continue
            if shift.get("closed"):
                finances = calculate_shift_finances(shift, mid)
                total_income += finances["income"]
                total_sanitary += finances["sanitary_expense"]
                total_transport += finances["transport_expense"]
                total_agent_salary += finances["agent_salary"]
                total_bodies += finances["total_bodies"]
                total_paid += finances["total_paid"]
                total_unpaid += finances["total_unpaid"]
                # БСМЭ — удалённые
                removed_list.extend([
                    {**b, "morgue": MORGUE_NAMES[mid], "date": start.strftime("%d.%m.%Y") if start else "?"}
                    for b in finances["removed_list"]
                ])
                # Неоплаченные стационарные — ГРС
                for body in shift.get("bodies", []):
                    if not body.get("paid") and not body.get("removed") and body.get("source") == "stat" and body.get("organization"):
                        unstat_list.append({
                            "surname": body.get("surname", "?"),
                            "org": body.get("organization", ""),
                            "date": start.strftime("%d.%m.%Y") if start else "?",
                            "morgue": MORGUE_NAMES[mid]
                        })
                # Заказы
                for order in shift.get("orders", []):
                    orders_in_period.append({
                        **order,
                        "morgue": MORGUE_NAMES[mid]
                    })

    total_expense = total_sanitary + total_transport + total_agent_salary
    profit = total_income - total_expense

    mname = "Оба морга" if morgue_id == "both" else MORGUE_NAMES.get(morgue_id, "?")
    text = f"📊 ОТЧЁТ ЗА {period_name.upper()}\n"
    text += f"{'━' * 30}\n"
    text += f"Морг: {mname}\n\n"
    text += f"📦 Всего тел: {total_bodies}\n"
    text += f"✅ Оплачено: {total_paid}\n"
    text += f"❌ Не оплачено: {total_unpaid}\n\n"
    text += f"💰 Доход: {total_income}₽\n"
    text += f"🧑‍⚕️ Санитары: {total_sanitary}₽\n"
    if total_transport > 0:
        text += f"🚚 Перевозка: {total_transport}₽\n"
    if total_agent_salary > 0:
        text += f"👤 Зарплата агентов: {total_agent_salary}₽\n"
    text += f"📉 Общий расход: {total_expense}₽\n"
    text += f"{'━' * 30}\n"
    text += f"✅ Прибыль: {profit}₽\n"

    # Неоплаченные стационарные — ГРС
    if unstat_list:
        text += f"\n{'━' * 30}\n"
        text += "🚗 КТО ВЫВЕЗ:\n"
        for r in unstat_list:
            text += f"{r['date']} / {r['surname']} / {r['morgue']} → {r['org']}\n"

    # БСМЭ — удалённые
    if removed_list:
        text += f"\n{'━' * 30}\n"
        text += "БСМЭ:\n"
        for r in removed_list:
            text += f"{r['date']} / {r['surname']} / {r['morgue']} → {r.get('removed_reason', '?')}\n"

    if orders_in_period:
        text += f"\n{'━' * 30}\n"
        text += "📋 ЗАКАЗЫ:\n"
        for o in orders_in_period:
            icon = "⚰️" if o.get("type") == "funeral" else "🔥"
            label = "Похороны" if o.get("type") == "funeral" else "Кремация"
            text += f"{icon} {o.get('deceased', '?')} — {label} — {o.get('event_date', '?')} — {o.get('morgue', '')}\n"

    return text

# ============================================================
# ОБЩАЯ СТАТИСТИКА ДЛЯ АДМИНА
# ============================================================

@router.message(F.text == "📈 Статистика")
async def start_admin_stats(message: types.Message, state: FSMContext):
    role = get_user(message.from_user.id)
    if not role or role.get("role") != "admin":
        await message.answer("⚠️ Только админ."); return

    await state.clear()
    await message.answer(
        "Выбери морг:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Первомайская 13", callback_data="sstats_m1")],
            [InlineKeyboardButton(text="Мира 11", callback_data="sstats_m2")],
            [InlineKeyboardButton(text="Оба морга", callback_data="sstats_both")]
        ])
    )
    await state.set_state(StatsFSM.select_morgue)

@router.callback_query(F.data.in_(["sstats_m1", "sstats_m2", "sstats_both"]), StatsFSM.select_morgue)
async def admin_select_morgue_stats(cb: types.CallbackQuery, state: FSMContext):
    mid_map = {"sstats_m1": "morgue1", "sstats_m2": "morgue2", "sstats_both": "both"}
    mid = mid_map[cb.data]

    # За всё время
    report = _generate_period_report(3650, mid)  # 10 лет
    await cb.message.edit_text(report)
    await cb.answer()
    await state.clear()
    await cb.message.answer("Далее:", reply_markup=kb_main_menu("admin"))
