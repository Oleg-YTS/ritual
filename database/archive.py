"""
Модуль архивации — объединение данных в недельные/месячные/квартальные архивы
"""
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from database.github_backup import gh_backup
from database.storage import MorgueStorage

ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "..", "backups")


def get_week_number(date: datetime) -> int:
    """Получить номер недели в году"""
    return date.isocalendar()[1]


def get_quarter(month: int) -> int:
    """Получить квартал по месяцу"""
    return (month - 1) // 3 + 1


def is_quarter_end(date: datetime) -> bool:
    """Проверить что это конец квартала (последний день марта/июня/сентября/декабря)"""
    last_day_of_month = {3: 31, 6: 30, 9: 30, 12: 31}
    if date.month in last_day_of_month:
        if date.day == last_day_of_month[date.month]:
            return True
    return False


def is_weekend_sunday_evening(date: datetime) -> bool:
    """Проверить что воскресенье вечер (для недельной архивации)"""
    return date.weekday() == 6  # 6 = воскресенье


class ArchiveManager:
    """Управление архивами"""
    
    def __init__(self):
        self.morgue1_db = MorgueStorage("morgue1")
        self.morgue2_db = MorgueStorage("morgue2")
    
    def _get_shifts_for_period(self, db: MorgueStorage, days: int) -> List[Dict[str, Any]]:
        """Получить закрытые смены за период"""
        shifts = db.get_shifts()
        cutoff = datetime.now() - timedelta(days=days)
        
        result = []
        for shift in shifts:
            if not shift.get("closed"):
                continue
            start_time_str = shift.get("start_time", "")
            if not start_time_str:
                continue
            try:
                start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00").split("+")[0])
                if start_time >= cutoff:
                    result.append(shift)
            except (ValueError, IndexError):
                continue
        
        return result
    
    def _calculate_summary(self, shifts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Рассчитать сводку по сменам"""
        total_bodies = 0
        total_paid = 0
        total_unpaid = 0
        income = 0
        expenses = 0
        profit = 0
        
        for shift in shifts:
            bodies = shift.get("bodies", [])
            active_bodies = [b for b in bodies if not b.get("removed")]
            paid_bodies = [b for b in active_bodies if b.get("paid")]
            
            total_bodies += len(active_bodies)
            total_paid += len(paid_bodies)
            total_unpaid += len(active_bodies) - len(paid_bodies)
            
            # Упрощённый расчёт доходов
            for body in active_bodies:
                body_type = body.get("type", "std")
                base_income = 8000 if body_type == "std" else 10000
                if body.get("paid"):
                    income += base_income
            
            expenses += shift.get("agent_salary", 0)
        
        profit = income - expenses
        
        return {
            "total_shifts": len(shifts),
            "total_bodies": total_bodies,
            "total_paid": total_paid,
            "total_unpaid": total_unpaid,
            "income": income,
            "expenses": expenses,
            "profit": profit
        }
    
    def archive_weekly(self, morgue_id: str) -> bool:
        """Создать недельный архив"""
        db = MorgueStorage(morgue_id)
        shifts = self._get_shifts_for_period(db, 7)
        
        if not shifts:
            return False
        
        now = datetime.now()
        week_num = get_week_number(now)
        year = now.year
        
        period_name = f"{year}-W{week_num:02d}"
        path = f"backups/weekly/{period_name}/{morgue_id}.json"
        
        summary = self._calculate_summary(shifts)
        
        archive_data = {
            "period": period_name,
            "period_type": "weekly",
            "morgue": morgue_id,
            "week_start": (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d"),
            "week_end": now.strftime("%Y-%m-%d"),
            "summary": summary,
            "shifts": shifts
        }
        
        content = json.dumps(archive_data, ensure_ascii=False, indent=2)
        message = f"Недельный архив {morgue_id} {period_name}"
        
        return gh_backup.upload_file(path, content, message)
    
    def archive_monthly(self, morgue_id: str) -> bool:
        """Создать месячный архив"""
        db = MorgueStorage(morgue_id)
        shifts = self._get_shifts_for_period(db, 30)
        
        if not shifts:
            return False
        
        now = datetime.now()
        year = now.year
        month = now.month
        
        period_name = f"{year}-{month:02d}"
        path = f"backups/monthly/{period_name}/{morgue_id}.json"
        
        summary = self._calculate_summary(shifts)
        
        archive_data = {
            "period": period_name,
            "period_type": "monthly",
            "morgue": morgue_id,
            "month_start": f"{year}-{month:02d}-01",
            "month_end": now.strftime("%Y-%m-%d"),
            "summary": summary,
            "shifts": shifts
        }
        
        content = json.dumps(archive_data, ensure_ascii=False, indent=2)
        message = f"Месячный архив {morgue_id} {period_name}"
        
        return gh_backup.upload_file(path, content, message)
    
    def archive_quarterly(self, morgue_id: str) -> bool:
        """Создать квартальный архив"""
        db = MorgueStorage(morgue_id)
        shifts = self._get_shifts_for_period(db, 90)
        
        if not shifts:
            return False
        
        now = datetime.now()
        year = now.year
        quarter = get_quarter(now.month)
        
        period_name = f"{year}-Q{quarter}"
        path = f"backups/quarterly/{period_name}/{morgue_id}.json"
        
        summary = self._calculate_summary(shifts)
        
        # Начало и конец квартала
        quarter_months = {1: 1, 2: 4, 3: 7, 4: 10}
        quarter_start_month = quarter_months[quarter]
        
        archive_data = {
            "period": period_name,
            "period_type": "quarterly",
            "morgue": morgue_id,
            "quarter_start": f"{year}-{quarter_start_month:02d}-01",
            "quarter_end": now.strftime("%Y-%m-%d"),
            "summary": summary,
            "shifts": shifts
        }
        
        content = json.dumps(archive_data, ensure_ascii=False, indent=2)
        message = f"Квартальный архив {morgue_id} {period_name}"
        
        return gh_backup.upload_file(path, content, message)
    
    def check_weekly_backup_exists(self) -> bool:
        """Проверить выполнен ли недельный бэкап сегодня"""
        now = datetime.now()
        if not is_weekend_sunday_evening(now):
            return True  # Не воскресенье, не проверяем
        
        # Проверяем существует ли файл архива за сегодня
        week_num = get_week_number(now)
        year = now.year
        period_name = f"{year}-W{week_num:02d}"
        
        # Проверяем локально
        for morgue_id in ["morgue1", "morgue2"]:
            filepath = os.path.join(ARCHIVE_DIR, "weekly", period_name, f"{morgue_id}.json")
            if not os.path.exists(filepath):
                return False
        
        return True
    
    def run_weekly_archive(self) -> Dict[str, bool]:
        """Выполнить недельную архивацию для обоих моргов"""
        results = {}
        for morgue_id in ["morgue1", "morgue2"]:
            results[morgue_id] = self.archive_weekly(morgue_id)
        return results
    
    def run_monthly_archive(self) -> Dict[str, bool]:
        """Выполнить месячную архивацию для обоих моргов"""
        results = {}
        for morgue_id in ["morgue1", "morgue2"]:
            results[morgue_id] = self.archive_monthly(morgue_id)
        return results
    
    def run_quarterly_archive(self) -> Dict[str, bool]:
        """Выполнить квартальную архивацию для обоих моргов"""
        results = {}
        for morgue_id in ["morgue1", "morgue2"]:
            results[morgue_id] = self.archive_quarterly(morgue_id)
        return results


# Синглтон
archive_manager = ArchiveManager()


def archive_weekly(morgue_id: str) -> bool:
    """Создать недельный архив"""
    return archive_manager.archive_weekly(morgue_id)


def archive_monthly(morgue_id: str) -> bool:
    """Создать месячный архив"""
    return archive_manager.archive_monthly(morgue_id)


def archive_quarterly(morgue_id: str) -> bool:
    """Создать квартальный архив"""
    return archive_manager.archive_quarterly(morgue_id)


def check_weekly_backup() -> bool:
    """Проверить выполнен ли недельный бэкап"""
    return archive_manager.check_weekly_backup_exists()