"""Daily habit records, independent of course assessments and learning scores."""

from __future__ import annotations

import calendar
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3


SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")


class CheckInStore:
    """One row per Beijing calendar day in the account's existing database."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS study_checkins (
                day TEXT PRIMARY KEY,
                checked_in_at TEXT NOT NULL
            )""")

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _now(now=None):
        return (now or datetime.now(timezone.utc)).astimezone(SHANGHAI)

    def status(self, month=None, *, now=None):
        current = self._now(now)
        today = current.date()
        month = month if month is not None else today.strftime("%Y-%m")
        if not isinstance(month, str) or not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", month):
            raise ValueError("签到月份格式应为 YYYY-MM。")
        year, number = map(int, month.split("-"))
        if not 1 <= year <= 9999:
            raise ValueError("签到月份无效。")
        with self._connection() as connection:
            dates = [row[0] for row in connection.execute(
                "SELECT day FROM study_checkins WHERE day <= ? ORDER BY day", (today.isoformat(),))]
        checked = set(dates)
        cursor = today if today.isoformat() in checked else today - timedelta(days=1)
        streak = 0
        while cursor.isoformat() in checked:
            streak += 1
            cursor -= timedelta(days=1)
        first_weekday, days_in_month = calendar.monthrange(year, number)
        month_dates = [day for day in dates if day.startswith(month + "-")]
        return {
            "today": today.isoformat(), "timezone": "Asia/Shanghai",
            "checkedInToday": today.isoformat() in checked,
            "currentStreak": streak, "totalDays": len(dates),
            "month": month, "checkedDates": month_dates, "monthCount": len(month_dates),
            "firstWeekday": first_weekday, "daysInMonth": days_in_month,
        }

    def check_in(self, *, now=None):
        current = self._now(now)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO study_checkins (day, checked_in_at) VALUES (?, ?)",
                (current.date().isoformat(), current.isoformat(timespec="seconds")),
            )
            recorded = cursor.rowcount == 1
        return {**self.status(now=current), "recorded": recorded}
