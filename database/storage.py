"""
Модуль для работы с JSON-хранилищем
"""
import json
import os
from typing import Dict, List, Any, Optional
from datetime import datetime
import asyncio
import aiofiles

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class JSONStorage:
    """Класс для работы с JSON-файлами"""
    
    def __init__(self, filepath: str):
        self.filepath = os.path.join(DATA_DIR, filepath)
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
    
    def _ensure_file(self):
        """Создаёт файл если не существует"""
        if not os.path.exists(self.filepath):
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
    
    def read(self) -> Any:
        """Чтение данных из файла"""
        self._ensure_file()
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    
    def write(self, data: Any):
        """Запись данных в файл"""
        self._ensure_file()
        with open(self.filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    async def read_async(self) -> Any:
        """Асинхронное чтение"""
        self._ensure_file()
        try:
            async with aiofiles.open(self.filepath, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    
    async def write_async(self, data: Any):
        """Асинхронная запись"""
        self._ensure_file()
        async with aiofiles.open(self.filepath, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))


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
        data = self.read()
        for shift in data.get("shifts", []):
            if shift["shift_id"] == shift_id:
                shift["orders"].append(order_data)
                self.write(data)
                return True
        return False
