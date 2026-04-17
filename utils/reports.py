"""
Модуль с утилитами для расчётов, отчётов и карточек
"""
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple


# Конфигурация моргов
MORGUE_CONFIG = {
    "morgue1": {
        "name": "Первомайская 13",
        "income": {"std": 8000, "nstd": 10000},
        "sanitary": {"std": 5500, "nstd": 8000},
        "transport": {"std": 0, "nstd": 0}
    },
    "morgue2": {
        "name": "Мира 11",
        "income": {"std": 8000, "nstd": 10000},
        "sanitary": {"std": 6500, "nstd": 8000},
        "transport_stat": {"std": 1500, "nstd": 2000},  # только стационар
        "transport_amb": {"std": 0, "nstd": 0}          # амбулаторно = 0
    }
}


def calculate_shift_finances(shift: Dict[str, Any], morgue_id: str) -> Dict[str, Any]:
    """Расчёт финансов смены"""
    bodies = shift.get("bodies", [])

    total_bodies = len([b for b in bodies if not b.get("removed")])
    total_paid = len([b for b in bodies if b.get("paid") and not b.get("removed")])
    total_unpaid = len([b for b in bodies if not b.get("paid") and not b.get("removed")])

    income = 0
    sanitary_expense = 0
    transport_expense = 0

    paid_bodies = [b for b in bodies if b.get("paid") and not b.get("removed")]
    all_active = [b for b in bodies if not b.get("removed")]

    for body in paid_bodies:
        body_type = "std" if body.get("type") == "std" else "nstd"
        income += MORGUE_CONFIG[morgue_id]["income"][body_type]
        sanitary_expense += MORGUE_CONFIG[morgue_id]["sanitary"][body_type]

    # Перевозка — за ВСЕ стационарные тела (оплачены или нет)
    for body in all_active:
        if body.get("source") == "stat" and not body.get("removed"):
            body_type = "std" if body.get("type") == "std" else "nstd"
            if morgue_id == "morgue2":
                transport_expense += MORGUE_CONFIG[morgue_id]["transport_stat"][body_type]
    
    agent_salary = shift.get("agent_salary", 0)
    total_expense = sanitary_expense + transport_expense + agent_salary
    profit = income - total_expense
    
    unpaid_list = [b for b in bodies if not b.get("paid") and not b.get("removed")]
    removed_list = [b for b in bodies if b.get("removed")]
    
    return {
        "total_bodies": total_bodies,
        "total_paid": total_paid,
        "total_unpaid": total_unpaid,
        "income": income,
        "sanitary_expense": sanitary_expense,
        "transport_expense": transport_expense,
        "agent_salary": agent_salary,
        "total_expense": total_expense,
        "profit": profit,
        "unpaid_list": unpaid_list,
        "removed_list": removed_list
    }


def format_orders_section(morgue_id: str) -> str:
    """Форматирование раздела заказов в отчёте смены (читает из файлов на текущую дату)"""
    from database.order_storage import get_orders_by_date
    from datetime import datetime
    
    today_str = datetime.now().strftime("%d.%m.%Y")
    orders = get_orders_by_date(morgue_id, today_str)
    
    if not orders:
        return "📋 ЗАКАЗЫ: -"
    
    section = "📋 ЗАКАЗЫ:\n"
    for order in orders:
        icon = "⚰️" if order.get("type") == "funeral" else "🔥"
        label = "Похороны" if order.get("type") == "funeral" else "Кремация"
        event_date = order.get("event_date", "?")
        deceased = order.get("deceased", "?")
        section += f"{icon} {deceased} | {event_date} | {label}\n"
    
    return section


def format_shift_report(shift: Dict[str, Any], morgue_id: str) -> str:
    """Форматирование отчёта по смене"""
    finances = calculate_shift_finances(shift, morgue_id)
    morgue_name = MORGUE_CONFIG.get(morgue_id, {}).get("name", "Морг")
    
    start_time = datetime.fromisoformat(shift["start_time"]) if shift.get("start_time") else datetime.now()
    date_str = start_time.strftime("%d.%m.%Y")
    
    report = f"📊 {morgue_name} | {date_str}\n"
    report += f"{'_' * 30}\n"
    report += f"Всего тел: {finances['total_bodies']}\n"
    report += f"Оплачено: {finances['total_paid']}\n"
    report += f"Не оплачено: {finances['total_unpaid']}\n\n"
    
    # Группировка по типу поступления
    stationary = [b for b in shift.get("bodies", []) if b.get("source") == "stat" and not b.get("removed")]
    ambulatory = [b for b in shift.get("bodies", []) if b.get("source") == "amb" and not b.get("removed")]

    if stationary:
        report += "🏥 СТАЦИОНАР:\n"
        for i, body in enumerate(stationary, 1):
            if body.get("paid"):
                san = MORGUE_CONFIG[morgue_id]["sanitary"][body.get("type", "std")]
                report += f"{i}. ✅ {body['surname']} — {san}₽\n"
            else:
                org = body.get("organization", "Не указано")
                report += f"{i}. ❌ {body['surname']} → {org}\n"
        report += "\n"

    if ambulatory:
        report += "🚗 АМБУЛАТОРНО:\n"
        for i, body in enumerate(ambulatory, 1):
            if body.get("paid"):
                san = MORGUE_CONFIG[morgue_id]["sanitary"][body.get("type", "std")]
                report += f"{i}. ✅ {body['surname']} — {san}₽\n"
            else:
                org = body.get("organization", "Не указано")
                report += f"{i}. ❌ {body['surname']} → {org}\n"
        report += "\n"

    report += f"{'_' * 30}\n"
    report += f"💰 Доход: {finances['income']}₽\n"
    report += f"🧑‍⚕️ Санитары: {finances['sanitary_expense']}₽\n"

    if finances['transport_expense'] > 0:
        report += f"🚚 Перевозка: {finances['transport_expense']}₽\n"

    if finances['agent_salary'] > 0:
        report += f"👤 Зарплата агента: {finances['agent_salary']}₽\n"

    report += f"📉 Общий расход: {finances['total_expense']}₽\n"
    report += f"✅ Чистая прибыль: {finances['profit']}₽\n"
    
    # Раздел удалённых тел
    if finances['removed_list']:
        report += f"\n{'_' * 30}\n"
        report += "🗑️ УДАЛЁННЫЕ (БСМЭ):\n"
        for body in finances['removed_list']:
            reason = body.get("removed_reason", "Не указано")
            report += f"• {body['surname']} → {reason}\n"
    
    # Раздел заказов (актуальные на текущую дату из файлов)
    orders_section = format_orders_section(morgue_id)
    report += f"\n{'_' * 30}\n"
    report += orders_section
    
    return report


def generate_driver_tasks(orders: List[Dict[str, Any]], morgue_filter: str = None) -> str:
    """Формирование заданий водителям по заказам"""
    if not orders:
        return "🚚 ЗАДАНИЯ ВОДИТЕЛЯМ\n" + "_" * 30 + "\nНет активных заказов."
    
    # Фильтрация по моргу
    if morgue_filter:
        orders = [o for o in orders if o.get("morgue_id") == morgue_filter]
    
    # Сортировка по дате
    def parse_date(order):
        ev_date = order.get("event_date", "")
        try:
            if "." in ev_date:
                d, m, y = ev_date.split(".")
                return datetime(int(y), int(m), int(d))
        except:
            pass
        return datetime.now()
    
    sorted_orders = sorted(orders, key=parse_date)
    
    report = "🚚 ЗАДАНИЯ ВОДИТЕЛЯМ\n"
    report += "_" * 30 + "\n"
    
    for i, order in enumerate(sorted_orders, 1):
        order_type = order.get("type", "funeral")
        icon = "⚰️" if order_type == "funeral" else "🔥"
        label = "Похороны" if order_type == "funeral" else "Кремация"
        
        report += f"\n{i}. {icon} {label} | {order.get('event_date', '?')}\n"
        report += f"   Усопший: {order.get('deceased', '?')}\n"
        report += f"   Морг: {order.get('morgue_location', '?')}\n"
        
        if order_type == "funeral":
            if order.get("temple"):
                report += f"   Храм: {order['temple']}\n"
            if order.get("cemetery"):
                report += f"   Кладбище: {order['cemetery']}\n"
        else:
            if order.get("temple"):
                report += f"   Храм: {order['temple']}\n"
            report += f"   → Крематорий\n"
        
        if order.get("phone"):
            report += f"   Тел: {order['phone']}\n"
    
    report += "\n" + "_" * 30 + f"\nВсего: {len(sorted_orders)} заказов"
    return report


def generate_crematorium_tasks(orders: List[Dict[str, Any]]) -> str:
    """Формирование заданий в крематорий"""
    cremation_orders = [o for o in orders if o.get("type") == "cremation"]
    
    if not cremation_orders:
        return "🔥 ЗАДАНИЯ КРЕМАТОРИЮ\n" + "_" * 30 + "\nНет заказов на кремацию."
    
    # Сортировка по дате
    def parse_date(order):
        ev_date = order.get("event_date", "")
        try:
            if "." in ev_date:
                d, m, y = ev_date.split(".")
                return datetime(int(y), int(m), int(d))
        except:
            pass
        return datetime.now()
    
    sorted_orders = sorted(cremation_orders, key=parse_date)
    
    report = "🔥 ЗАДАНИЯ КРЕМАТОРИЮ\n"
    report += "_" * 30 + "\n"
    
    for i, order in enumerate(sorted_orders, 1):
        report += f"\n{i}. {order.get('deceased', '?')} | {order.get('event_date', '?')}\n"
        report += f"   Морг: {order.get('morgue_location', '?')}\n"
        
        # Урна
        urn_str = order.get("urn", "")
        if not urn_str:
            urn_type = order.get("urn_type", "")
            urn_color = order.get("urn_color", "")
            if urn_type == "plastic" and urn_color:
                urn_str = f"Пластик ({urn_color})"
            elif urn_type == "cardboard":
                urn_str = "Вечная память"
            else:
                urn_str = urn_type or "Не указано"
        report += f"   Урна: {urn_str}\n"
        
        # Дополнительные услуги
        extras = order.get("extras", [])
        if extras:
            extras_map = {
                "large_body": "Крупное тело",
                "polished_coffin": "Полированный гроб",
                "short_farewell": "Короткое прощание",
                "hall": "Зал",
                "hall_blessing": "Зал + отпевание",
                "urgent": "Срочная"
            }
            extras_str = ", ".join([extras_map.get(e, e) for e in extras])
            report += f"   Допы: {extras_str}\n"
        
        if order.get("temple"):
            report += f"   Отпевание: {order['temple']}\n"
    
    report += "\n" + "_" * 30 + f"\nВсего: {len(sorted_orders)} кремаций"
    return report


def build_driver_card(order: Dict[str, Any]) -> str:
    """Создание карточки для водителя"""
    order_type = order.get("type", "funeral")

    if order_type == "funeral":
        card = "ЗАКАЗ ВОДИТЕЛЮ\n"
        card += f"{'_' * 30}\n"
        card += f"Дата: {order.get('event_date', 'Не указано')}\n"
        card += f"Усопший: {order.get('deceased', 'Не указано')}\n"
        card += f"Морг: {order.get('morgue_location', 'Не указано')}\n"

        if order.get("temple"):
            card += f"Отпевание: {order['temple']}\n"

        if order.get("cemetery"):
            card += f"Кладбище: {order['cemetery']}\n"

        card += f"Телефон: {order.get('phone', 'Не указано')}\n"

    else:  # cremation
        extras = order.get("extras", [])
        has_hall = "hall" in extras or "hall_blessing" in extras

        card = "ЗАКАЗ ВОДИТЕЛЮ (Кремация)\n"
        card += f"{'_' * 30}\n"
        card += f"Дата: {order.get('event_date', 'Не указано')}\n"
        card += f"Усопший: {order.get('deceased', 'Не указано')}\n"
        card += f"Морг: {order.get('morgue_location', 'Не указано')}\n"

        if has_hall:
            card += f"Зал отпевания\n"
            card += f"Конечная точка: Крематорий\n"
        else:
            if order.get("temple"):
                card += f"Храм: {order['temple']}\n"
            card += f"Конечная точка: Крематорий\n"

        card += f"Телефон: {order.get('phone', 'Не указано')}\n"

    return card


def build_crematorium_card(order: Dict[str, Any]) -> str:
    """Создание карточки для крематория"""
    urn_str = order.get("urn", "")
    if not urn_str:
        urn_type = order.get("urn_type", "")
        urn_color = order.get("urn_color", "")
        if urn_type == "plastic" and urn_color:
            urn_str = f"Пластик ({urn_color})"
        elif urn_type == "cardboard":
            urn_str = "Вечная память"
        else:
            urn_str = urn_type

    extras = order.get("extras", [])
    extras_map = {
        "large_body": "Крупное тело",
        "polished_coffin": "Полированный гроб",
        "short_farewell": "Короткое прощание",
        "hall": "Зал",
        "hall_blessing": "Зал + отпевание",
        "urgent": "Срочная кремация"
    }

    extras_list = [extras_map.get(e, e) for e in extras]
    if extras_list:
        extras_str = "\n".join([f"• {e}" for e in extras_list])
    else:
        extras_str = "Нет"

    card = "КРЕМАТОРИЙ\n"
    card += f"{'_' * 30}\n"
    card += f"ФИО: {order.get('deceased', 'Не указано')}\n"
    card += f"Дата кремации: {order.get('event_date', 'Не указано')}\n"
    card += f"Урна: {urn_str}\n"
    card += f"Допы:\n{extras_str}\n\n"
    card += "Все стандартно, оплата наличными, оформление в день кремации.\n"

    return card


def generate_removed_report(shifts: List[Dict[str, Any]], period_days: int = 7) -> str:
    """Генерация отчёта «кто вывез»"""
    cutoff_date = datetime.now() - timedelta(days=period_days)
    
    report = f"🚗 ОТЧЁТ «КТО ВЫВЕЗ» за {period_days} дней\n"
    report += f"{'_' * 30}\n"
    
    removed_bodies = []
    
    for shift in shifts:
        shift_date = datetime.fromisoformat(shift["start_time"]) if shift.get("start_time") else None
        if shift_date and shift_date < cutoff_date:
            continue
        
        for body in shift.get("bodies", []):
            if body.get("removed"):
                removed_bodies.append({
                    "surname": body.get("surname", ""),
                    "organization": body.get("organization", "Не указано"),
                    "date": shift_date.strftime("%d.%m.%Y") if shift_date else "Неизвестно",
                    "morgue": MORGUE_CONFIG.get(shift.get("morgue_id", ""), {}).get("name", "")
                })
    
    if not removed_bodies:
        report += "Нет удалённых тел за этот период\n"
        return report
    
    # Группировка по организациям
    by_org = {}
    for body in removed_bodies:
        org = body["organization"]
        if org not in by_org:
            by_org[org] = []
        by_org[org].append(body)
    
    for org, bodies in by_org.items():
        report += f"\n📍 {org}:\n"
        for body in bodies:
            report += f"  • {body['date']} | {body['surname']} | {body['morgue']}\n"
    
    report += f"\n{'_' * 30}\n"
    report += f"Всего вывезено: {len(removed_bodies)}\n"
    
    return report


def generate_period_report(shifts: List[Dict[str, Any]], period_days: int = 7, morgue_id: str = None) -> str:
    """Генерация отчёта за период"""
    cutoff_date = datetime.now() - timedelta(days=period_days)
    
    period_name = "неделю" if period_days == 7 else "месяц" if period_days <= 31 else "квартал"
    
    report = f"📊 ОТЧЁТ ЗА {period_name.upper()}\n"
    report += f"{'_' * 30}\n"
    
    total_income = 0
    total_sanitary = 0
    total_transport = 0
    total_agent_salary = 0
    total_bodies = 0
    removed_bodies = []
    
    for shift in shifts:
        shift_date = datetime.fromisoformat(shift["start_time"]) if shift.get("start_time") else None
        if shift_date and shift_date < cutoff_date:
            continue
        
        if morgue_id and shift.get("morgue_id") != morgue_id:
            continue
        
        finances = calculate_shift_finances(shift)
        
        total_income += finances["income"]
        total_sanitary += finances["sanitary_expense"]
        total_transport += finances["transport_expense"]
        total_agent_salary += finances["agent_salary"]
        total_bodies += finances["total_bodies"]
        
        removed_bodies.extend(finances["removed_list"])
    
    total_expense = total_sanitary + total_transport + total_agent_salary
    total_profit = total_income - total_expense
    
    morgue_name = MORGUE_CONFIG.get(morgue_id, {}).get("name", "Все морги") if morgue_id else "Все морги"
    report += f"🏥 Морг: {morgue_name}\n\n"
    
    report += f"📦 Всего тел: {total_bodies}\n"
    report += f"💰 Доход: {total_income}₽\n"
    report += f"🧑‍⚕️ Санитары: {total_sanitary}₽\n"
    report += f"🚚 Перевозка: {total_transport}₽\n"
    report += f"👤 Зарплата агентов: {total_agent_salary}₽\n"
    report += f"📉 Общий расход: {total_expense}₽\n"
    report += f"✅ Чистая прибыль: {total_profit}₽\n"
    
    if removed_bodies:
        report += f"\n{'_' * 30}\n"
        report += "🗑️ Удалённые тела (БСМЭ):\n"
        for body in removed_bodies:
            reason = body.get("removed_reason", "Не указано")
            report += f"• {body.get('surname', '')} → {reason}\n"
    
    return report
