"""
Модуль для бэкапа данных в GitHub через PyGithub
Используется при закрытии смены для надёжного сохранения
"""
import os
import logging
from datetime import datetime
from typing import Optional

try:
    from github import Github, GithubException
    GITHUB_AVAILABLE = True
except ImportError:
    GITHUB_AVAILABLE = False
    Github = None
    GithubException = Exception

logger = logging.getLogger(__name__)


class GitHubBackup:
    """Бэкап данных в GitHub репозиторий"""

    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN", "")
        self.repo_name = os.getenv("REPO_NAME", "")
        self.enabled = bool(self.token and self.repo_name and GITHUB_AVAILABLE)
        self.repo = None
        self._initialized = False
        self._init_failed = False

    def _ensure_initialized(self):
        """Ленивая инициализация — попытка при каждом использовании"""
        if self._initialized:
            return

        if not self.enabled:
            return

        try:
            self.repo = Github(self.token).get_repo(self.repo_name)
            logger.info(f"✅ GitHub подключён: {self.repo_name}")
            self._initialized = True
        except Exception as e:
            logger.error(f"❌ GitHub ошибка подключения: {e}")
            logger.error(f"   Token: {'***' + self.token[-4:] if len(self.token) > 4 else 'пустой'}")
            logger.error(f"   Repo: {self.repo_name}")
            self.enabled = False

    def upload_file(self, path: str, content: str, message: str = "Автобэкап") -> bool:
        """Загрузить файл в GitHub"""
        self._ensure_initialized()
        if not self.enabled:
            logger.warning(f"⚠️ GitHub отключён, пропуск бэкапа: {path}")
            return False

        try:
            # Проверяем существует ли файл
            try:
                file = self.repo.get_contents(path)
                self.repo.update_file(
                    path=path,
                    message=message,
                    content=content,
                    sha=file.sha,
                    branch="main"
                )
                logger.info(f"✅ GitHub обновлён: {path}")
            except GithubException as e:
                # Файл не существует — создаём
                logger.debug(f"Файл не найден, создаём: {path}")
                self.repo.create_file(
                    path=path,
                    message=message,
                    content=content,
                    branch="main"
                )
                logger.info(f"✅ GitHub создан: {path}")
            return True
        except Exception as e:
            logger.error(f"❌ GitHub ошибка загрузки {path}: {e}")
            return False

    def backup_shift(self, shift_data: dict, morgue_id: str) -> bool:
        """Бэкап закрытой смены в GitHub (трупы + заказы)"""
        if not self.enabled:
            logger.warning(f"⚠️ GitHub бэкап смены отклонён (не включён): {morgue_id}")
            return False

        shift_id = shift_data.get("shift_id", "unknown")
        # Используем end_time (время закрытия) для уникальности пути
        # Если смена была открыта давно, но закрывается сейчас — нужен текущий путь
        end_time = shift_data.get("end_time", "")
        start_time = shift_data.get("start_time", "")
        bodies_count = len(shift_data.get("bodies", []))
        orders_count = len(shift_data.get("orders", []))

        # Формируем путь по времени ЗАКРЫТИЯ, а не открытия
        if end_time:
            date_str = end_time[:10]  # YYYY-MM-DD
            time_suffix = end_time[11:19].replace(":", "")  # HHMMSS
        else:
            now = datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            time_suffix = now.strftime("%H%M%S")

        # Уникальный путь: backups/morgue1/2026-04-17/shift_20260411_002012_closed_20260417_230000.json
        path = f"backups/{morgue_id}/{date_str}/{shift_id}_closed_{time_suffix}.json"
        
        import json
        content = json.dumps(shift_data, ensure_ascii=False, indent=2)
        message = f"Бэкап смены {shift_id} ({morgue_id}) закрыта {date_str} — {bodies_count} тел, {orders_count} заказов"

        logger.info(f"📦 GitHub бэкап смены: {path} ({bodies_count} тел, {orders_count} заказов)")
        result = self.upload_file(path, content, message)
        
        if result:
            logger.info(f"✅ Смена {shift_id} успешно сохранена в GitHub")
        else:
            logger.error(f"❌ Ошибка бэкапа смены {shift_id} в GitHub")
        
        return result

    def backup_users(self, users_data: dict) -> bool:
        """Бэкап пользователей"""
        if not self.enabled:
            return False

        import json
        path = "backups/users.json"
        content = json.dumps(users_data, ensure_ascii=False, indent=2)

        return self.upload_file(path, content, "Автобэкап пользователей")

    def backup_all_morgue(self, morgue_data: dict, morgue_id: str) -> bool:
        """Полный бэкап всех данных морга"""
        if not self.enabled:
            return False

        import json
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = f"backups/{morgue_id}/{date_str}/full_backup.json"
        content = json.dumps(morgue_data, ensure_ascii=False, indent=2)
        message = f"Полный бэкап {morgue_id} ({date_str})"

        return self.upload_file(path, content, message)


# Синглтон
gh_backup = GitHubBackup()
