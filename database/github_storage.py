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