"""
Модуль для работы с данными через GitHub API
Заменяет локальное хранилище JSON файлов
"""

import os
import json
import base64
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
import requests

logger = logging.getLogger(__name__)

class GitHubDataStorage:
    """Хранилище данных через GitHub API"""
    
    def __init__(self, filepath: str):
        self.filepath = filepath  # Например: "data/morgue1.json"
        self.token = os.getenv("GITHUB_TOKEN", "")
        self.repo = os.getenv("GITHUB_REPO", "Oleg-YTS/ritual")
        self.enabled = bool(self.token and self.repo)
        
        if not self.enabled:
            logger.warning(f"GitHub storage отключен для {filepath} — нет токена или репозитория")
    
    def _get_headers(self) -> Dict[str, str]:
        """Получить заголовки для GitHub API"""
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }
    
    def _get_api_url(self, path: str) -> str:
        """Получить URL для файла в GitHub API"""
        return f"https://api.github.com/repos/{self.repo}/contents/{path}"
    
    def read(self) -> Any:
        """Чтение данных из GitHub"""
        if not self.enabled:
            # Если GitHub недоступен — возвращаем пустую структуру
            if "users.json" in self.filepath:
                return {}
            elif "morgue" in self.filepath:
                return {"shifts": [], "orders": []}
            else:
                return {}
        
        try:
            url = self._get_api_url(self.filepath)
            headers = self._get_headers()
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                content = base64.b64decode(data["content"]).decode("utf-8")
                result = json.loads(content)
                logger.info(f"✅ Загружены данные из GitHub: {self.filepath}")
                return result
            elif response.status_code == 404:
                # Файл не существует — возвращаем пустую структуру
                logger.info(f"⚠️ Файл не найден в GitHub, создаём пустой: {self.filepath}")
                if "users.json" in self.filepath:
                    return {}
                elif "morgue" in self.filepath:
                    return {"shifts": [], "orders": []}
                else:
                    return {}
            else:
                logger.error(f"❌ Ошибка загрузки из GitHub: {response.status_code} - {response.text}")
                # Возвращаем пустую структуру в случае ошибки
                if "users.json" in self.filepath:
                    return {}
                elif "morgue" in self.filepath:
                    return {"shifts": [], "orders": []}
                else:
                    return {}
                    
        except Exception as e:
            logger.error(f"❌ Ошибка чтения из GitHub: {e}")
            # Возвращаем пустую структуру в случае ошибки
            if "users.json" in self.filepath:
                return {}
            elif "morgue" in self.filepath:
                return {"shifts": [], "orders": []}
            else:
                return {}
    
    def write(self, data: Any) -> bool:
        """Запись данных в GitHub"""
        if not self.enabled:
            logger.error(f"❌ GitHub storage отключен — невозможно записать: {self.filepath}")
            return False
        
        try:
            url = self._get_api_url(self.filepath)
            headers = self._get_headers()
            
            # Сначала проверяем, существует ли файл
            check_response = requests.get(url, headers=headers, timeout=10)
            
            payload = {
                "message": f"Обновление данных: {self.filepath}",
                "content": base64.b64encode(
                    json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
                ).decode("utf-8")
            }
            
            if check_response.status_code == 200:
                # Файл существует — обновляем
                existing_data = check_response.json()
                payload["sha"] = existing_data["sha"]
                method = "PUT"
            else:
                # Файл не существует — создаём
                method = "PUT"
            
            response = requests.request(method, url, headers=headers, json=payload, timeout=10)
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ Данные сохранены в GitHub: {self.filepath}")
                return True
            else:
                logger.error(f"❌ Ошибка сохранения в GitHub: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка записи в GitHub: {e}")
            return False

# Совместимость с существующим кодом
JSONStorage = GitHubDataStorage
UsersStorage = lambda: GitHubDataStorage("data/users.json")
MorgueStorage = lambda morgue_id: GitHubDataStorage(f"data/{morgue_id}.json")

# Глобальный словарь для тестовых ролей
_test_roles = {}

def set_test_role(user_id: int, role: str):
    _test_roles[user_id] = role

def clear_test_role(user_id: int):
    _test_roles.pop(user_id, None)

class UsersStorage(JSONStorage):
    """Хранилище пользователей"""
    
    def __init__(self):
        super().__init__("users.json")
        self._ensure_file()
    
    def _ensure_file(self):
        if not os.path.exists(self.filepath):
            default_users = {
                "747600306": {"role": "admin", "name": "Евсеев"},
                "7819002363": {"role": "manager_morg1", "name": "Семенов"},
                "387529965": {"role": "agent_morg1", "name": "Жуков"}
            }
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(default_users, f, ensure_ascii=False, indent=2)
    
    def get_user(self, telegram_id: int) -> Optional[Dict[str, str]]:
        # Проверка тестовой роли
        if telegram_id in _test_roles:
            return {"role": _test_roles[telegram_id], "name": f"ТЕСТ({_test_roles[telegram_id]})"}
        users = self.read()
        return users.get(str(telegram_id))
    
    def add_user(self, telegram_id: int, role: str, name: str):
        users = self.read()
        users[str(telegram_id)] = {"role": role, "name": name}
        self.write(users)
    
    def remove_user(self, telegram_id: int):
        users = self.read()
        if str(telegram_id) in users:
            del users[str(telegram_id)]
            self.write(users)
    
    def get_all_users(self) -> Dict[str, Dict[str, str]]:
        return self.read()


class MorgueStorage(JSONStorage):
    """Хранилище данных морга"""
    
    def __init__(self, morgue_id: str):
        """morgue_id: morgue1 (Первомайская 13) или morgue2 (Мира 11)"""
        super().__init__(f"{morgue_id}.json")
    
    def get_shifts(self) -> List[Dict[str, Any]]:
        return self.read().get("shifts", [])
    
    def get_active_shift(self) -> Optional[Dict[str, Any]]:
        data = self.read()
        shifts = data.get("shifts", [])
        for shift in shifts:
            if not shift.get("closed", True):
                return shift
        return None
    
    def create_shift(self, opened_by: int, opened_by_name: str) -> Dict[str, Any]:
        data = self.read()
        if "shifts" not in data:
            data["shifts"] = []

        shift_id = f"shift_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shift = {
            "shift_id": shift_id,
            "start_time": datetime.now().isoformat(),
            "opened_by": opened_by,
            "opened_by_name": opened_by_name,
            "bodies": [],
            "orders": [],
            "closed": False
        }
        data["shifts"].append(shift)
        self.write(data)
        return shift

    def add_body(self, shift_id: str, body_data: Dict[str, Any]):
        data = self.read()
        for shift in data.get("shifts", []):
            if shift["shift_id"] == shift_id:
                shift["bodies"].append(body_data)
                self.write(data)
                return True
        return False
    
    def remove_body(self, shift_id: str, body_index: int, reason: str):
        data = self.read()
        for shift in data.get("shifts", []):
            if shift["shift_id"] == shift_id:
                if 0 <= body_index < len(shift["bodies"]):
                    shift["bodies"][body_index]["removed"] = True
                    shift["bodies"][body_index]["removed_reason"] = reason
                    self.write(data)
                    return True
        return False
    
    def update_shift(self, shift_id: str, updated_shift: Dict[str, Any]):
        """Обновить конкретную смену в файле (сохраняя остальные)"""
        data = self.read()
        for i, shift in enumerate(data.get("shifts", [])):
            if shift["shift_id"] == shift_id:
                data["shifts"][i] = updated_shift
                self.write(data)
                return True
        return False

    def close_shift(self, shift_id: str, closed_by: int, closed_by_name: str):
        data = self.read()
        for shift in data.get("shifts", []):
            if shift["shift_id"] == shift_id:
                shift["closed"] = True
                shift["end_time"] = datetime.now().isoformat()
                shift["closed_by"] = closed_by
                shift["closed_by_name"] = closed_by_name
                self.write(data)
                return True
        return False
    
    def add_order(self, shift_id: str, order_data: Dict[str, Any]):
        """Старый метод (для совместимости, если нужен) - добавляет в смену"""
        # Теперь заказы будем хранить отдельно, но этот метод оставим для безопасности
        # Логика переехала в add_global_order
        return self.add_global_order(order_data)

    def add_global_order(self, order_data: Dict[str, Any]):
        """Сохраняет заказ в общий список заказов морга (не привязано к смене)"""
        data = self.read()
        if "orders" not in data:
            data["orders"] = []
        data["orders"].append(order_data)
        self.write(data)
        return True

    def get_all_orders(self) -> List[Dict[str, Any]]:
        """Возвращает все заказы из общего списка"""
        data = self.read()
        return data.get("orders", [])
