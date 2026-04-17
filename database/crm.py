"""
Модуль CRM — база заказов для обзвона и обратной связи
Хранится в backups/crm/orders_all.json на GitHub
"""
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from database.github_backup import gh_backup

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
CRM_DIR = os.path.join(os.path.dirname(__file__), "..", "backups", "crm")


class CRMStorage:
    """Хранилище заказов для CRM"""
    
    def __init__(self):
        self.filepath = os.path.join(CRM_DIR, "orders_all.json")
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        self._ensure_file()
    
    def _ensure_file(self):
        """Создаёт файл если не существует"""
        if not os.path.exists(self.filepath):
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump({"orders": []}, f, ensure_ascii=False, indent=2)
    
    def read(self) -> Dict[str, Any]:
        """Чтение данных"""
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"orders": []}
    
    def write(self, data: Dict[str, Any]):
        """Запись данных"""
        with open(self.filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def add_order(self, order_data: Dict[str, Any]) -> str:
        """
        Добавить заказ в базу
        Возвращает order_id
        """
        data = self.read()
        
        # Генерируем уникальный ID
        order_id = f"ord_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        order_data["order_id"] = order_id
        order_data["created_at"] = datetime.now().isoformat()
        
        # Если нет поля feedback, добавляем пустое
        if "feedback" not in order_data:
            order_data["feedback"] = {
                "called": False,
                "call_date": None,
                "rating": None,
                "comment": None
            }
        
        data["orders"].append(order_data)
        self.write(data)
        
        # Бэкап в GitHub
        self._backup_to_github()
        
        return order_id
    
    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Получить заказ по ID"""
        data = self.read()
        for order in data.get("orders", []):
            if order.get("order_id") == order_id:
                return order
        return None
    
    def update_order(self, order_id: str, updates: Dict[str, Any]) -> bool:
        """Обновить заказ"""
        data = self.read()
        for i, order in enumerate(data.get("orders", [])):
            if order.get("order_id") == order_id:
                data["orders"][i].update(updates)
                self.write(data)
                self._backup_to_github()
                return True
        return False
    
    def update_feedback(self, order_id: str, feedback_data: Dict[str, Any]) -> bool:
        """Обновить обратную связь по заказу"""
        data = self.read()
        for i, order in enumerate(data.get("orders", [])):
            if order.get("order_id") == order_id:
                if "feedback" not in data["orders"][i]:
                    data["orders"][i]["feedback"] = {}
                data["orders"][i]["feedback"].update(feedback_data)
                self.write(data)
                self._backup_to_github()
                return True
        return False
    
    def get_orders_for_calling(self, days_after_event: int = 3) -> List[Dict[str, Any]]:
        """
        Получить заказы для обзвона
        days_after_event: собирать заказы, где прошло N дней после события
        """
        data = self.read()
        target_date = datetime.now() - timedelta(days=days_after_event)
        target_date_str = target_date.strftime("%d.%m.%Y")
        
        orders_to_call = []
        for order in data.get("orders", []):
            event_date_str = order.get("event_date", "")
            if not event_date_str:
                continue
            
            try:
                # Парсим дату события ДД.ММ.ГГГГ
                day, month, year = map(int, event_date_str.split("."))
                event_date = datetime(year, month, day)
                
                # Проверяем что прошло нужное количество дней
                days_diff = (datetime.now() - event_date).days
                if days_diff == days_after_event:
                    # Проверяем что ещё не звонили
                    if not order.get("feedback", {}).get("called", False):
                        orders_to_call.append(order)
            except (ValueError, IndexError):
                continue
        
        return orders_to_call
    
    def get_orders_for_monuments(self, years_after: int = 1) -> List[Dict[str, Any]]:
        """
        Получить заказы для предложения памятников
        years_after: собирать заказы, где прошло N лет после события
        """
        data = self.read()
        target_date = datetime.now() - timedelta(days=years_after * 365)
        
        orders_for_monuments = []
        for order in data.get("orders", []):
            event_date_str = order.get("event_date", "")
            cemetery = order.get("cemetery", "")
            
            # Нужен кладбище для памятников
            if not cemetery:
                continue
            
            try:
                day, month, year = map(int, event_date_str.split("."))
                event_date = datetime(year, month, day)
                
                # Проверяем что прошло нужное количество лет
                years_diff = (datetime.now() - event_date).days / 365
                if years_diff >= years_after:
                    orders_for_monuments.append(order)
            except (ValueError, IndexError):
                continue
        
        return orders_for_monuments
    
    def get_all_orders(self) -> List[Dict[str, Any]]:
        """Получить все заказы"""
        data = self.read()
        return data.get("orders", [])
    
    def get_orders_by_period(self, days: int) -> List[Dict[str, Any]]:
        """Получить заказы за период (последние N дней)"""
        data = self.read()
        cutoff = datetime.now() - timedelta(days=days)
        
        result = []
        for order in data.get("orders", []):
            event_date_str = order.get("event_date", "")
            try:
                day, month, year = map(int, event_date_str.split("."))
                event_date = datetime(year, month, day)
                if event_date >= cutoff:
                    result.append(order)
            except (ValueError, IndexError):
                continue
        
        return result
    
    def _backup_to_github(self):
        """Бэкап всей базы в GitHub"""
        data = self.read()
        content = json.dumps(data, ensure_ascii=False, indent=2)
        gh_backup.upload_file(
            "backups/crm/orders_all.json",
            content,
            f"CRM бэкап {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )


# Синглтон
crm_storage = CRMStorage()


def add_order(order_data: Dict[str, Any]) -> str:
    """Добавить заказ в CRM"""
    return crm_storage.add_order(order_data)


def get_orders_for_calling(days_after_event: int = 3) -> List[Dict[str, Any]]:
    """Получить заказы для обзвона"""
    return crm_storage.get_orders_for_calling(days_after_event)


def get_orders_for_monuments(years_after: int = 1) -> List[Dict[str, Any]]:
    """Получить заказы для предложения памятников"""
    return crm_storage.get_orders_for_monuments(years_after)


def update_feedback(order_id: str, feedback_data: Dict[str, Any]) -> bool:
    """Обновить обратную связь"""
    return crm_storage.update_feedback(order_id, feedback_data)