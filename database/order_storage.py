"""
Хранилище заказов через GitHub API
"""

import logging
from typing import List, Dict, Any
from database.github_storage import GitHubDataStorage

logger = logging.getLogger(__name__)

def save_order(morgue_id: str, order: Dict[str, Any]) -> bool:
    """
    Сохраняет заказ в GitHub в общий файл морга
    Возвращает True при успехе
    """
    try:
        # Путь в GitHub: backups/orders/morgue_id.json
        file_path = f"backups/orders/{morgue_id}.json"
        storage = GitHubDataStorage(file_path)
        
        # Получаем текущие заказы
        orders = storage.read()
        if not isinstance(orders, list):
            orders = []
        
        # Добавляем новый заказ
        orders.append(order)
        
        # Записываем обратно
        if storage.write(orders):
            logger.info(f"Заказ сохранён в GitHub: {file_path}")
            return True
        return False
        
    except Exception as e:
        logger.error(f"Ошибка сохранения заказа в GitHub: {e}")
        return False

def get_all_orders_for_morgue(morgue_id: str) -> List[Dict[str, Any]]:
    """
    Получает ВСЕ заказы для морга из GitHub
    """
    try:
        file_path = f"backups/orders/{morgue_id}.json"
        storage = GitHubDataStorage(file_path)
        orders = storage.read()
        if isinstance(orders, list):
            return orders
        return []
    except Exception as e:
        logger.error(f"Ошибка чтения заказов из GitHub: {e}")
        return []