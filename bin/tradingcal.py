"""NYSE 交易日曆：只讀 data/nyse-calendar.json，沒有覆蓋的年份一律視為未知，不猜。"""
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
TPE = ZoneInfo("Asia/Taipei")
OPEN = time(9, 30)


class CalendarUnknown(Exception):
    """平日卻查無該年資料或資料無效；與「確定休市」不同，要通知。"""


class Calendar:
    def __init__(self, data):
        if data.get("timezone") != "America/New_York" or not data.get("years"):
            raise CalendarUnknown("日曆格式不符")
        self.years = {}
        for year, entry in data["years"].items():
            closed = entry.get("closed_dates", [])
            early = entry.get("early_closes", {})
            try:
                closed_set = {date.fromisoformat(d) for d in closed}
                early_map = {date.fromisoformat(d): time.fromisoformat(t) for d, t in early.items()}
            except ValueError as e:
                raise CalendarUnknown(f"日曆日期無效：{e}") from e
            if len(closed_set) != len(closed):
                raise CalendarUnknown(f"{year} 有重複休市日")
            if closed_set & early_map.keys():
                raise CalendarUnknown(f"{year} 休市與早收日衝突")
            if any(d.year != int(year) for d in closed_set | early_map.keys()):
                raise CalendarUnknown(f"{year} 含其他年份的日期")
            if any(t <= OPEN for t in early_map.values()):
                raise CalendarUnknown(f"{year} 早收時間不晚於開盤")
            self.years[int(year)] = (closed_set, early_map)

    @classmethod
    def load(cls, path):
        try:
            with open(path, encoding="utf-8") as f:
                return cls(json.load(f))
        except (OSError, json.JSONDecodeError) as e:
            raise CalendarUnknown(f"無法讀取日曆：{e}") from e

    def status(self, d):
        """回傳 'closed' | 'early' | 'open'；週末直接 closed，平日缺年度丟 CalendarUnknown。"""
        if d.weekday() >= 5:
            return "closed"
        if d.year not in self.years:
            raise CalendarUnknown(f"日曆沒有 {d.year} 年資料")
        closed, early = self.years[d.year]
        if d in closed:
            return "closed"
        return "early" if d in early else "open"

    def early_close(self, d):
        return self.years[d.year][1].get(d)

    def previous_session(self, d):
        """上一個交易日；倒查途中遇到日曆沒有的年份，丟 CalendarUnknown（呼叫端降級）。"""
        cur = d - timedelta(days=1)
        for _ in range(15):
            if self.status(cur) != "closed":
                return cur
            cur -= timedelta(days=1)
        raise CalendarUnknown("找不到上一交易日")


def ny_at(d, hh, mm, ss=0):
    return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=NY)
