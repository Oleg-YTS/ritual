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

        if self.enabled:
            try:
                self.repo = Github(self.token).get_repo(self.repo_name)
                logger.info(f"GitHub подключён: {self.repo_name}")
            except Exception as e:
                logger.error(f"Ошибка подключения к GitHub: {e}")
                self.enabled = False

    def upload_file(self, path: str, content: str, message: str = "Автобэкап") -> bool:
        """Загрузить файл в GitHub"""
        if not self.enabled:
            logger.debug("GitHub отключён, пропуск бэкапа")
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
                logger.info(f"GitHub обновлён: {path}")
            except GithubException:
                # Файл не существует — создаём
                self.repo.create_file(
                    path=path,
                    message=message,
                    content=content,
                    branch="main"
                )
                logger.info(f"GitHub создан: {path}")
            return True
        except Exception as e:
            logger.error(f"Ошибка загрузки в GitHub: {e}")
            return False

    def backup_shift(self, shift_data: dict, morgue_id: str) -> bool:
        """Бэкап закрытой смены в GitHub"""
        if not self.enabled:
            return False

        shift_id = shift_data.get("shift_id", "unknown")
        start_time = shift_data.get("start_time", "")

        # Формируем путь: backups/morgue1/2026-04-09/shift_20260409_143000.json
        if start_time:
            date_str = start_time[:10]  # YYYY-MM-DD
        else:
            date_str = datetime.now().strftime("%Y-%m-%d")

        path = f"backups/{morgue_id}/{date_str}/{shift_id}.json"
        content = str(shift_data)  # Временно, ниже будет JSON

        import json
        content = json.dumps(shift_data, ensure_ascii=False, indent=2)
        message = f"Бэкап смены {shift_id} ({morgue_id})"

        return self.upload_file(path, content, message)

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
