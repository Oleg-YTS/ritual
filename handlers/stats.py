"""
БЛОК 3: СТАТИСТИКА — отчёты, аналитика
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
from utils.reports import (
    MORGUE_CONFIG, calculate_shift_finances, format_shift_report,
    generate_driver_tasks, generate_crematorium_tasks
)
from keyboards.menus import kb_main_menu, kb_report_period, ALL_MENU_BUTTONS

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
        "admin": ["stats", "report"],
        "manager_morg1": ["report"],
        "manager_morg2": ["report"],
        "agent_morg1": [],
        "agent_morg2": [],
    }
    return action in perms.get(role, [])

def check_auto_close(morgue_id: str):
    """Проверяет, не пора ли закрыть смену автоматически (смена вчерашняя, а уже новый день)"""
    db = MORGUE_DBS[morgue_id]
    shift = db.get_active_shift()
    if not shift: return

    try:
        start = datetime.fromisoformat(shift["start_time"])
        now = datetime.now()
        
        # Если смена начата вчера (или раньше) и сейчас уже следующий день
        if start.date() < now.date():
            logger.info(f"Авто-закрытие смены {shift['shift_id']} в {morgue_id}")
            
            # Закрываем смену задним числом (конец вчерашнего дня или сейчас)
            # Логичнее поставить end_time = начало текущего дня
            end_time = now.replace(hour=0, minute=0, second=0) # 00:00 сегодня
            
            db.close_shift(shift["shift_id"], 0, "System (Автозакрытие)")
            # Вручную правим время, т.к. close_shift ставит текущее
            # Но для простоты оставим текущее время закрытия, главное closed=True
            
            return True
    except Exception as e:
        logger.error(f"Ошибка авто-закрытия: {e}")
    return False

# ============================================================
# СТАТИСТИКА (Последняя закрытая смена)
# ============================================================

@router.message(F.text == "📈 Статистика")
async def start_stats(message: types.Message, state: FSMContext):
    user = get_user(message.from_user.id)
    if not user: return
    role = user.get("role", "")
    
    # Админ выбирает морг, сотрудник смотрит свой
    if role == "admin":
        await message.answer(
            "Статистика по последней закрытой смене. Выбери морг:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Первомайская 13", callback_data="stats_last_m1")],
                [InlineKeyboardButton(text="Мира 11", callback_data="stats_last_m2")]
            ])
        )
    else:
        user_morgue = get_user_morgue(message.from_user.id)
        if user_morgue:
            await _show_last_shift_stats(message, user_morgue, role)

@router.callback_query(F.data.startswith("stats_last_"))
async def admin_select_stats_morgue(cb: types.CallbackQuery, state: FSMContext):
    mid = "morgue1" if cb.data.endswith("m1") else "morgue2"
    user = get_user(cb.from_user.id)
    role = user["role"] if user else "admin"
    await _show_last_shift_stats(cb.message, mid, role)
    await cb.answer()

async def _show_last_shift_stats(message, morgue_id: str, role: str):
    # Перед показом проверяем, не зависла ли вчерашняя смена
    check_auto_close(morgue_id)
    
    db = MORGUE_DBS[morgue_id]
    shifts = db.get_shifts()
    
    # Ищем последнюю закрытую смену
    closed_shifts = [s for s in shifts if s.get("closed")]
    last_shift = closed_shifts[-1] if closed_shifts else None

    if not last_shift:
        await message.answer(f"🏥 {MORGUE_NAMES[morgue_id]}\nЗакрытых смен пока нет.")
        return

    report = format_shift_report(last_shift, morgue_id)
    
    # Добавим инфо о заказах
    orders = last_shift.get("orders", [])
    if orders:
        report += "\n━━━━ ЗАКАЗЫ В СМЕНЕ ━━━━\n"
        for o in orders:
            icon = "⚰️" if o.get("type") == "funeral" else "🔥"
            label = "Похороны" if o.get("type") == "funeral" else "Кремация"
            report += f"{icon} {o.get('deceased', '?')} — {label}\n"

    await message.answer(report)
    await message.answer("Далее:", reply_markup=kb_main_menu(role))

# ============================================================
# ОТЧЁТ ЗА ПЕРИОД (История)
# ============================================================

@router.message(F.text == "📊 Отчёт за период")
async def start_period_report(message: types.Message, state: FSMContext):
    if not check_perm(message.from_user.id, "report"):
        await message.answer("⚠️ Нет прав."); return
    
    await state.clear()
    user_morgue = get_user_morgue(message.from_user.id)

    if user_morgue:
        # Сотрудник — сразу выбор периода для своего морга
        await state.update_data(morgue_id=user_morgue)
        await message.answer("Выбери период для отчёта:", reply_markup=kb_report_period())
    else:
        # Админ — выбор морга
        await message.answer(
            "Выбери морг:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Первомайская 13", callback_data="sreport_m1")],
                [InlineKeyboardButton(text="Мира 11", callback_data="sreport_m2")],
                [InlineKeyboardButton(text="Оба морга", callback_data="sreport_both")]
            ])
        )

# Админ выбирает морг для отчета
@router.callback_query(F.data.in_(["sreport_m1", "sreport_m2", "sreport_both"]))
async def admin_select_morgue(cb: types.CallbackQuery, state: FSMContext):
    mid_map = {"sreport_m1": "morgue1", "sreport_m2": "morgue2", "sreport_both": "both"}
    mid = mid_map[cb.data]
    await state.update_data(morgue_id=mid)
    await cb.answer()
    await cb.message.edit_text("Выбери период для отчёта:", reply_markup=kb_report_period())

@router.callback_query(F.data.in_(["speriod_week", "speriod_month", "speriod_quarter"]))
async def select_period(cb: types.CallbackQuery, state: FSMContext):
    period_map = {"speriod_week": 7, "speriod_month": 30, "speriod_quarter": 90}
    days = period_map[cb.data]
    
    data = await state.get_data()
    morgue_id = data.get("morgue_id")
    if not morgue_id:
        await cb.message.answer("Ошибка: морг не выбран. Попробуй снова."); await cb.answer(); return

    report = _generate_period_report(days, morgue_id)
    await cb.message.edit_text(report)
    await cb.answer()
    
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
        
        # Проверяем авто-закрытие перед генерацией отчета
        check_auto_close(morgue_id)

    total_income = 0
    total_sanitary = 0
    total_transport = 0
    total_agent_salary = 0
    total_bodies = 0
    total_paid = 0
    total_unpaid = 0
    orders_in_period = []

    for mid in mids:
        db = MORGUE_DBS[mid]
        
        # Проверяем авто-закрытие перед генерацией отчета
        check_auto_close(mid)

        # 1. Сбор данных по телам из смен
        shifts = db.get_shifts()
        for shift in shifts:
            # Отчет только по ЗАКРЫТЫМ сменам
            if not shift.get("closed"): continue
            
            start = datetime.fromisoformat(shift["start_time"]) if shift.get("start_time") else None
            if start and start < cutoff: continue

            finances = calculate_shift_finances(shift, mid)
            total_income += finances["income"]
            total_sanitary += finances["sanitary_expense"]
            total_transport += finances["transport_expense"]
            total_agent_salary += finances["agent_salary"]
            total_bodies += finances["total_bodies"]
            total_paid += finances["total_paid"]
            total_unpaid += finances["total_unpaid"]

        # 2. Сбор данных по заказам из ГЛОБАЛЬНОГО списка
        global_orders = db.get_all_orders()
        for order in global_orders:
            # Фильтрация заказов по дате (event_date)
            ev_date = order.get("event_date", "") # ДД.ММ.ГГГГ
            try:
                if not ev_date or "." not in ev_date: continue
                # Пытаемся парсить дату заказа
                day, month, year = map(int, ev_date.split("."))
                order_dt = datetime(year, month, day)
                if order_dt < cutoff: continue
                
                # Добавляем в список
                orders_in_period.append({**order, "morgue": MORGUE_NAMES[mid]})
            except Exception as e:
                logger.warning(f"Ошибка парсинга даты заказа: {ev_date}, {e}")
                continue

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
    if total_transport > 0: text += f"🚚 Перевозка: {total_transport}₽\n"
    if total_agent_salary > 0: text += f"👤 Зарплата агентов: {total_agent_salary}₽\n"
    text += f"📉 Общий расход: {total_expense}₽\n"
    text += f"{'━' * 30}\n"
    text += f"✅ Прибыль: {profit}₽\n"

    if orders_in_period:
        text += f"\n{'━' * 30}\n"
        text += "📋 ЗАКАЗЫ:\n"
        for o in orders_in_period:
            icon = "⚰️" if o.get("type") == "funeral" else "🔥"
            label = "Похороны" if o.get("type") == "funeral" else "Кремация"
            text += f"{icon} {o.get('deceased', '?')} — {label} — {o.get('event_date', '?')} — {o.get('morgue', '')}\n"

    return text


# ============================================================
# ЗАДАНИЯ ВОДИТЕЛЯМ И КРЕМАТОРИЮ
# ============================================================

@router.message(F.text == "🚚 Задания водителям")
async def driver_tasks(message: types.Message, state: FSMContext):
    """Показать задания водителям по всем заказам"""
    user = get_user(message.from_user.id)
    if not user: return
    role = user.get("role", "")
    
    # Собираем все заказы из обоих моргов
    all_orders = []
    for mid in ["morgue1", "morgue2"]:
        db = MORGUE_DBS[mid]
        orders = db.get_all_orders()
        for order in orders:
            order["morgue_id"] = mid
            all_orders.append(order)
    
    # Фильтруем по моргу если не админ
    morgue_filter = None
    if role != "admin":
        user_morgue = get_user_morgue(message.from_user.id)
        if user_morgue:
            morgue_filter = user_morgue
    
    report = generate_driver_tasks(all_orders, morgue_filter)
    await message.answer(report)
    await message.answer("Далее:", reply_markup=kb_main_menu(role))


@router.message(F.text == "🔥 Задания крематорию")
async def crematorium_tasks(message: types.Message, state: FSMContext):
    """Показать задания в крематорий"""
    user = get_user(message.from_user.id)
    if not user: return
    role = user.get("role", "")
    
    # Собираем все заказы на кремацию
    all_orders = []
    for mid in ["morgue1", "morgue2"]:
        db = MORGUE_DBS[mid]
        orders = db.get_all_orders()
        for order in orders:
            if order.get("type") == "cremation":
                order["morgue_id"] = mid
                all_orders.append(order)
    
    report = generate_crematorium_tasks(all_orders)
    await message.answer(report)
    await message.answer("Далее:", reply_markup=kb_main_menu(role))


