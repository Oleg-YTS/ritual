"""
Клавиатуры бота — ЕДИНЫЙ источник
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# Все кнопки меню — для фильтрации от FSM
ALL_MENU_BUTTONS = [
    "➕ Добавить тело", "🗑️ Удалить тело",
    "⚰️ Похороны", "🔥 Кремация", "📋 Мои заказы",
    "🚕 Водителю", "🔒 Закрыть смена",
    "📈 Статистика", "📊 Отчёт за период",
    "🧪 Тест роли"
]

def kb_main_menu(role: str = None):
    """Меню с разделением по ролям"""
    b = ReplyKeyboardBuilder()
    
    # === ОБЩИЙ БЛОК (Для всех: Агент, Менеджер, Админ) ===
    b.row(KeyboardButton(text="➕ Добавить тело"), KeyboardButton(text="🗑️ Удалить тело"))
    b.row(KeyboardButton(text="⚰️ Похороны"), KeyboardButton(text="🔥 Кремация"))
    b.row(KeyboardButton(text="📋 Мои заказы"))
    
    # === БЛОК МЕНЕДЖЕРА (Менеджер + Админ) ===
    if role in ["admin", "manager_morg1", "manager_morg2"]:
        b.row(KeyboardButton(text="🔒 Закрыть смена"), KeyboardButton(text="📈 Статистика"))

    # === БЛОК АДМИНА (Только Админ) ===
    if role == "admin":
        b.row(KeyboardButton(text="📊 Отчёт за период"))

    return b.as_markup(resize_keyboard=True, input_field_placeholder="Выбери действие:")

def kb_select_morgue_add():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Первомайская 13", callback_data="add_m1")],
        [InlineKeyboardButton(text="Мира 11", callback_data="add_m2")]
    ])

def kb_select_morgue_close():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Первомайская 13", callback_data="close_m1")],
        [InlineKeyboardButton(text="Мира 11", callback_data="close_m2")]
    ])

def kb_select_morgue_remove():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Первомайская 13", callback_data="rm_m1")],
        [InlineKeyboardButton(text="Мира 11", callback_data="rm_m2")]
    ])

def kb_body_type():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Стандарт", callback_data="btype_std")],
        [InlineKeyboardButton(text="Не стандарт", callback_data="btype_nstd")]
    ])

def kb_body_source():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Стационар", callback_data="bsrc_stat")],
        [InlineKeyboardButton(text="Амбулаторно", callback_data="bsrc_amb")]
    ])

def kb_payment_status(bodies: list):
    b = InlineKeyboardBuilder()
    for i, body in enumerate(bodies):
        status = "✅" if body.get("paid") else "❌"
        b.row(InlineKeyboardButton(text=f"{status} {body['surname']}", callback_data=f"pay_{i}"))
    b.row(InlineKeyboardButton(text="РАССЧИТАТЬ", callback_data="calc_done"))
    return b.as_markup()

def kb_bodies_for_removal(bodies: list):
    b = InlineKeyboardBuilder()
    for i, body in enumerate(bodies):
        if body.get("removed"): continue
        b.row(InlineKeyboardButton(text=f"{body['surname']}", callback_data=f"rm_body_{i}"))
    return b.as_markup()

def kb_removal_reason():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="БСМЭ", callback_data="rmreason_bsme")],
        [InlineKeyboardButton(text="Другая причина", callback_data="rmreason_other")]
    ])

def kb_morgue_location():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Первомайская 13", callback_data="rloc_m1")],
        [InlineKeyboardButton(text="Мира 11", callback_data="rloc_m2")],
        [InlineKeyboardButton(text="Другое место", callback_data="rloc_other")]
    ])

def kb_urn_type():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Вечная память", callback_data="urn_cardboard")],
        [InlineKeyboardButton(text="Пластик", callback_data="urn_plastic")]
    ])

def kb_urn_color():
    return InlineKeyboardMarkup(inline_keyboard=[
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
        icon = "🔥" if order.get("type") == "cremation" else "⚰️"
        b.row(InlineKeyboardButton(text=f"{icon} {order.get('deceased', '?')}", callback_data=f"rorder_{i}"))
    return b.as_markup()

def kb_order_actions():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Водителю", callback_data="rsend_driver")],
        [InlineKeyboardButton(text="Крематорий", callback_data="rsend_crem")]
    ])

def kb_report_period():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Неделя", callback_data="speriod_week")],
        [InlineKeyboardButton(text="Месяц", callback_data="speriod_month")],
        [InlineKeyboardButton(text="Квартал", callback_data="speriod_quarter")]
    ])

def kb_role_switcher():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Админ", callback_data="test_role_admin")],
        [InlineKeyboardButton(text="Менеджер М13", callback_data="test_role_manager_morg1"), InlineKeyboardButton(text="Менеджер М11", callback_data="test_role_manager_morg2")],
        [InlineKeyboardButton(text="Агент М13", callback_data="test_role_agent_morg1"), InlineKeyboardButton(text="Агент М11", callback_data="test_role_agent_morg2")]
    ])
