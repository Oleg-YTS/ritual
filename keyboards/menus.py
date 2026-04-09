"""
Модуль с клавиатурами для бота
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


def kb_main_menu(role: str) -> ReplyKeyboardMarkup:
    """Главное меню в зависимости от роли"""
    builder = ReplyKeyboardBuilder()
    
    # Общие кнопки
    builder.row(KeyboardButton(text="➕ Добавить тело"))
    
    if role in ["admin", "manager_morg1", "manager_morg2"]:
        builder.row(KeyboardButton(text="🔒 Закрыть смену"))
    
    builder.row(KeyboardButton(text="🕯️ Ритуальный заказ"))
    
    if role in ["admin", "manager_morg1", "manager_morg2", "agent_morg1", "agent_morg2"]:
        builder.row(KeyboardButton(text="📋 Мои заказы"))
    
    if role in ["admin", "manager_morg1", "manager_morg2"]:
        builder.row(KeyboardButton(text="📊 Отчёт за период"))
    
    if role in ["admin", "manager_morg1", "manager_morg2"]:
        builder.row(KeyboardButton(text="🚗 Кто вывез"))
    
    if role == "admin":
        builder.row(KeyboardButton(text="📈 Статистика"))
        builder.row(KeyboardButton(text="👥 Пользователи"))
    
    return builder.as_markup(resize_keyboard=True)


def kb_select_morgue() -> InlineKeyboardMarkup:
    """Выбор морга"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏥 Первомайская 13", callback_data="morgue1")],
        [InlineKeyboardButton(text="🏥 Мира 11", callback_data="morgue2")]
    ])


def kb_body_type() -> InlineKeyboardMarkup:
    """Тип тела"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Стандарт", callback_data="body_std")],
        [InlineKeyboardButton(text="Не стандарт", callback_data="body_nstd")]
    ])


def kb_body_source() -> InlineKeyboardMarkup:
    """Источник поступления"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Стационар", callback_data="source_stat")],
        [InlineKeyboardButton(text="Амбулаторно", callback_data="source_amb")]
    ])


def kb_bodies_list(bodies: list) -> InlineKeyboardMarkup:
    """Список тел для удаления"""
    builder = InlineKeyboardBuilder()
    for i, body in enumerate(bodies):
        status = "✅" if body.get("paid") else "❌"
        removed = "🗑️ " if body.get("removed") else ""
        builder.row(InlineKeyboardButton(
            text=f"{removed}{status} {body['surname']}",
            callback_data=f"body_select_{i}"
        ))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel"))
    return builder.as_markup()


def kb_payment_status(bodies: list) -> InlineKeyboardMarkup:
    """Клавиатура для отметки оплаты"""
    builder = InlineKeyboardBuilder()
    for i, body in enumerate(bodies):
        if body.get("removed"):
            continue
        status = "✅" if body.get("paid") else "❌"
        builder.row(InlineKeyboardButton(
            text=f"{status} {body['surname']}",
            callback_data=f"payment_{i}"
        ))
    builder.row(InlineKeyboardButton(text="💰 РАССЧИТАТЬ", callback_data="calc_shift"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel"))
    return builder.as_markup()


def kb_ritual_type() -> InlineKeyboardMarkup:
    """Тип ритуального заказа"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚰️ Похороны", callback_data="ritual_funeral")],
        [InlineKeyboardButton(text="🔥 Кремация", callback_data="ritual_cremation")]
    ])


def kb_urn_type() -> InlineKeyboardMarkup:
    """Тип урны"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Картон", callback_data="urn_cardboard")],
        [InlineKeyboardButton(text="🏺 Пластик", callback_data="urn_plastic")]
    ])


def kb_urn_color() -> InlineKeyboardMarkup:
    """Цвет урны"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚪ Белая", callback_data="color_white")],
        [InlineKeyboardButton(text="⚫ Чёрная", callback_data="color_black")],
        [InlineKeyboardButton(text="🔵 Синяя", callback_data="color_blue")]
    ])


def kb_extras(selected: list = None) -> InlineKeyboardMarkup:
    """Дополнительные услуги для кремации"""
    if selected is None:
        selected = []
    
    extras = {
        "large_body": "Крупное тело",
        "polished_coffin": "Полированный гроб",
        "short_farewell": "Короткое прощание",
        "hall": "Зал",
        "hall_blessing": "Зал + отпевание",
        "urgent": "Срочная кремация"
    }
    
    builder = InlineKeyboardBuilder()
    for key, label in extras.items():
        mark = "✅" if key in selected else "⬜"
        builder.row(InlineKeyboardButton(
            text=f"{mark} {label}",
            callback_data=f"extra_{key}"
        ))
    builder.row(InlineKeyboardButton(text="ДАЛЕЕ ➡️", callback_data="extras_done"))
    return builder.as_markup()


def kb_order_select(orders: list) -> InlineKeyboardMarkup:
    """Выбор заказа"""
    builder = InlineKeyboardBuilder()
    for i, order in enumerate(orders):
        icon = "🔥" if order.get("type") == "cremation" else "⚰️"
        builder.row(InlineKeyboardButton(
            text=f"{icon} {order.get('deceased', 'Без имени')}",
            callback_data=f"order_select_{i}"
        ))
    return builder.as_markup()


def kb_order_actions() -> InlineKeyboardMarkup:
    """Действия с заказом"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚕 Водителю", callback_data="send_driver")],
        [InlineKeyboardButton(text="🔥 Крематорий", callback_data="send_crematorium")]
    ])


def kb_removal_reason() -> InlineKeyboardMarkup:
    """Причина удаления тела"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="БСМЭ", callback_data="remove_bsme")],
        [InlineKeyboardButton(text="Другая причина", callback_data="remove_other")]
    ])


def kb_report_period() -> InlineKeyboardMarkup:
    """Выбор периода отчёта"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Неделя", callback_data="period_week")],
        [InlineKeyboardButton(text="Месяц", callback_data="period_month")],
        [InlineKeyboardButton(text="Квартал", callback_data="period_quarter")]
    ])


def kb_admin_stats() -> InlineKeyboardMarkup:
    """Статистика для админа"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Первомайская 13", callback_data="stats_morgue1")],
        [InlineKeyboardButton(text="Мира 11", callback_data="stats_morgue2")],
        [InlineKeyboardButton(text="Оба морга", callback_data="stats_both")]
    ])


def kb_user_management() -> InlineKeyboardMarkup:
    """Управление пользователями"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="user_add")],
        [InlineKeyboardButton(text="🗑️ Удалить пользователя", callback_data="user_remove")],
        [InlineKeyboardButton(text="📋 Список пользователей", callback_data="user_list")]
    ])


def kb_role_select() -> InlineKeyboardMarkup:
    """Выбор роли"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Админ", callback_data="role_admin")],
        [InlineKeyboardButton(text="Менеджер М13", callback_data="role_manager_morg1")],
        [InlineKeyboardButton(text="Менеджер М11", callback_data="role_manager_morg2")],
        [InlineKeyboardButton(text="Агент М13", callback_data="role_agent_morg1")],
        [InlineKeyboardButton(text="Агент М11", callback_data="role_agent_morg2")]
    ])
