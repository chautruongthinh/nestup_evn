import json
import os
import asyncio
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple, Optional

from homeassistant.core import HomeAssistant

DATE_FMT = "%d-%m-%Y"
DEFAULT_HISTORY_START_DATE = date(2025, 1, 1)

def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def next_month(year: int, month: int) -> Tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def month_range(start: Tuple[int, int], end: Tuple[int, int]):
    y, m = start
    while (y, m) <= end:
        yield y, m
        y, m = next_month(y, m)

class EVNDataStorage:
    def __init__(
        self,
        hass: HomeAssistant,
        customer_id: str,
        history_start_date: Optional[date] = None,
    ):
        self.hass = hass
        self.customer_id = customer_id

        self.storage_dir = hass.config.path("nestup_evn")
        os.makedirs(self.storage_dir, exist_ok=True)

        self.file_path = os.path.join(self.storage_dir, f"{customer_id}.json")

        self._lock = asyncio.Lock()
        self._backfill_task: Optional[asyncio.Task] = None

        self.data: Dict = self._load()

        meta = self.data.setdefault("meta", {})

        meta_start = meta.get("history_start_date")
        if meta_start:
            self.history_start_date = datetime.strptime(
                meta_start, "%Y-%m-%d"
            ).date()
        else:
            if history_start_date is None:
                history_start_date = DEFAULT_HISTORY_START_DATE

            self.history_start_date = history_start_date
            meta["history_start_date"] = history_start_date.isoformat()

        meta.setdefault("backfill_done", False)
        meta.setdefault("backfill_in_progress", False)

        self.data.setdefault("daily", [])
        self.data.setdefault("monthly", [])

        self.save()

    def _load(self) -> Dict:
        if not os.path.exists(self.file_path):
            return {}

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    async def async_load(self) -> None:
        return

    async def async_update_from_sensor_data(self, data: dict) -> None:
        return

    def _existing_daily_dates(self) -> set:
        dates = set()
        for d in self.data.get("daily", []):
            try:
                dates.add(datetime.strptime(d["Ngày"], DATE_FMT).date())
            except Exception:
                continue
        return dates

    def get_missing_daily_ranges(self) -> List[Tuple[date, date]]:
        today = date.today() - timedelta(days=1)
        existing = self._existing_daily_dates()

        if not existing:
            return [(self.history_start_date, today)]

        missing = []
        start = None

        for d in daterange(self.history_start_date, today):
            if d not in existing:
                if start is None:
                    start = d
            else:
                if start:
                    missing.append((start, d - timedelta(days=1)))
                    start = None

        if start:
            missing.append((start, today))

        return missing

    def add_daily_records(self, records: List[Dict]):
        existing = self._existing_daily_dates()

        for r in records:
            try:
                d = datetime.strptime(r["Ngày"], DATE_FMT).date()
            except Exception:
                continue

            if d not in existing:
                self.data["daily"].append(r)

        self.data["daily"].sort(
            key=lambda x: datetime.strptime(x["Ngày"], DATE_FMT)
        )

    def _existing_months(self) -> set:
        return {
            (m.get("Năm"), m.get("Tháng"))
            for m in self.data.get("monthly", [])
            if "Năm" in m and "Tháng" in m
        }

    def get_missing_months(self) -> List[Tuple[int, int]]:
        today = date.today()
        end = (today.year, today.month - 1 or 12)

        start = (
            self.history_start_date.year,
            self.history_start_date.month,
        )

        return [
            ym
            for ym in month_range(start, end)
            if ym not in self._existing_months()
        ]

    def add_monthly_record(self, record: Dict):
        ym = (record.get("Năm"), record.get("Tháng"))
        if ym not in self._existing_months():
            self.data["monthly"].append(record)

        self.data["monthly"].sort(
            key=lambda x: (x["Năm"], x["Tháng"])
        )

    def is_backfill_done(self) -> bool:
        return bool(self.data.get("meta", {}).get("backfill_done"))

    def start_background_backfill(self, api):
        if self.is_backfill_done():
            return
        if self._backfill_task and not self._backfill_task.done():
            return
        self._backfill_task = self.hass.async_create_task(
            self._async_run_backfill(api)
        )

    async def _async_run_backfill(self, api):
        async with self._lock:
            meta = self.data.setdefault("meta", {})

            if meta.get("backfill_done"):
                return

            meta["backfill_in_progress"] = True
            meta.setdefault(
                "backfill_started_at",
                datetime.now().isoformat()
            )
            self.save()

            import logging
            _LOGGER = logging.getLogger(__name__)

            _LOGGER.info(
                "[EVN] History backfill START / RESUME for %s",
                self.customer_id,
            )

            try:
                for start, end in self.get_missing_daily_ranges():
                    daily_raw = await api.fetch_daily_range_evnspc(
                        self.customer_id,
                        start.strftime("%Y%m%d"),
                        end.strftime("%Y%m%d"),
                    )

                    records = [
                        {
                            "Ngày": d["strTime"].replace("/", "-"),
                            "Điện tiêu thụ (kWh)": d.get("dSanLuongBT"),
                            "Tiền điện (VND)": None,
                        }
                        for d in daily_raw or []
                        if d.get("strTime")
                    ]

                    self.add_daily_records(records)
                    self.save()

                missing_months = self.get_missing_months()
                if missing_months:
                    sy, sm = missing_months[0]
                    ey, em = missing_months[-1]

                    bills = await api.fetch_monthly_bills_evnspc(
                        self.customer_id,
                        sm, sy,
                        em, ey,
                    )

                    for b in bills or []:
                        self.add_monthly_record(
                            {
                                "Tháng": b.get("iThang"),
                                "Năm": b.get("iNam"),
                                "Điện tiêu thụ (KWh)": b.get("dSanLuong"),
                                "Tiền Điện": b.get("lTongTien"),
                            }
                        )
                    self.save()

                meta["backfill_done"] = True
                meta["backfill_in_progress"] = False
                meta["backfill_at"] = datetime.now().isoformat()
                self.save()

                _LOGGER.info(
                    "[EVN] History backfill DONE for %s",
                    self.customer_id,
                )

            except Exception as ex:
                meta["backfill_in_progress"] = False
                self.save()
                _LOGGER.exception(
                    "[EVN] History backfill FAILED for %s: %s",
                    self.customer_id,
                    ex,
                )

    def get_data_for_webui(self) -> Dict:
        """
        Return data EXACTLY in the format expected by data.js
        """

        # -------- DAILY --------
        # data.js expects dailyData to be an ARRAY
        daily_out = []
        for d in self.data.get("daily", []):
            daily_out.append({
                "Ngày": d.get("Ngày"),
                "Điện tiêu thụ (kWh)": float(d.get("Điện tiêu thụ (kWh)") or 0),
                "Tiền điện (VND)": d.get("Tiền điện (VND)"),
            })

        # -------- MONTHLY --------
        monthly_sanluong = []
        monthly_tiendien = []

        for r in self.data.get("monthly", []):
            # Normalize keys
            kwh = (
                r.get("Điện tiêu thụ (KWh)")
                if r.get("Điện tiêu thụ (KWh)") is not None
                else r.get("Điện tiêu thụ (kWh)")
            ) or 0

            cost = (
                r.get("Tiền Điện")
                if r.get("Tiền Điện") is not None
                else r.get("Tiền điện (VND)")
            ) or 0

            monthly_sanluong.append({
                "Tháng": r.get("Tháng"),
                "Năm": r.get("Năm"),
                "Điện tiêu thụ (KWh)": int(kwh),
            })

            monthly_tiendien.append({
                "Tháng": r.get("Tháng"),
                "Năm": r.get("Năm"),
                "Tiền Điện": int(cost),
            })

        return {
            # daily API MUST return array
            "daily": daily_out,

            # monthly API returns object with SanLuong / TienDien
            "monthly": {
                "SanLuong": monthly_sanluong,
                "TienDien": monthly_tiendien,
            },
        }

