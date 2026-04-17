"""
Хранилище заказов по папкам (по моргам и датам)
"""

import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

ORDERS_DIR = "backups/orders"

def ensure_dirs():
    """Создаёт структуру папок если их нет"""
    for morgue in ["morgue1", "morgue2"]:
        os.makedirs(os.path.join(ORDERS_DIR, morgue), exist_ok=True)

def get_order_file(morgue_id: str, date_str: str) -> str:
    """Получает путь к файлу заказов для морга и даты"""
    # date_str формат: ДД.ММ.ГГГГ
    return os.path.join(ORDERS_DIR, morgue_id, f"{date_str}.json")

def save_order(morgue_id: str, order: Dict[str, Any]) -> bool:
    """
    Сохраняет заказ в файл по ДАТЕ ОФОРМЛЕНИЯ (creation_date)
    Возвращает True при успехе
    """
    try:
        ensure_dirs()
        
        # Используем creation_date для имени файла (дата оформления заказа)
        creation_date = order.get("creation_date", "")
        if not creation_date:
            logger.error(f"У заказа нет creation_date: {order}")
            return False
        
        file_path = get_order_file(morgue_id, creation_date)
        
        # Читаем существующие заказы или создаём новый список
        orders = []
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                orders = json.load(f)
        
        # Добавляем новый заказ
        orders.append(order)
        
        # Записываем обратно
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Заказ сохранён: {file_path}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка сохранения заказа: {e}")
        return False

def get_orders_by_date(morgue_id: str, date_str: str) -> List[Dict[str, Any]]:
    """
    Получает все заказы для морга на указанную дату
    date_str формат: ДД.ММ.ГГГГ
    """
    try:
        ensure_dirs()
        
        file_path = get_order_file(morgue_id, date_str)
        
        if not os.path.exists(file_path):
            return []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
            
    except Exception as e:
        logger.error(f"Ошибка чтения заказов: {e}")
        return []

def get_all_orders_for_morgue(morgue_id: str) -> List[Dict[str, Any]]:
    """
    Получает ВСЕ заказы для морга из всех файлов
    """
    try:
        ensure_dirs()
        
        morgue_dir = os.path.join(ORDERS_DIR, morgue_id)
        all_orders = []
        
        if not os.path.exists(morgue_dir):
            return all_orders
        
        for filename in os.listdir(morgue_dir):
            if filename.endswith('.json'):
                file_path = os.path.join(morgue_dir, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        orders = json.load(f)
                        all_orders.extend(orders)
                except Exception as e:
                    logger.error(f"Ошибка чтения файла {filename}: {e}")
        
        return all_orders
        
    except Exception as e:
        logger.error(f"Ошибка получения заказов: {e}")
        return []