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

        self.file_path = os.path.join(
            self.storage_dir, f"{customer_id}.json"
        )

        self._lock = asyncio.Lock()

        self.history_start_date = (
            history_start_date or DEFAULT_HISTORY_START_DATE
        )

        # load once
        self.data: Dict = self._load()
        self.data.setdefault("daily", [])
        self.data.setdefault("monthly", [])

        self.save()

    # ------------------------------------------------------------------
    # BASIC STORAGE
    # ------------------------------------------------------------------
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

    async def async_load(self):
        return

    # ------------------------------------------------------------------
    # DAILY REALTIME UPDATE (mỗi ngày)
    # ------------------------------------------------------------------
    async def async_update_from_sensor_data(self, data: dict):
        try:
            to_date = data.get("to_date")
            kwh = data.get("econ_daily_new")

            if not to_date or kwh is None:
                return

            record = {
                "Ngày": (
                    to_date.strftime(DATE_FMT)
                    if hasattr(to_date, "strftime")
                    else str(to_date)
                ),
                "Điện tiêu thụ (kWh)": float(kwh),
                "Tiền điện (VND)": None,
            }

            self._add_daily_record(record)
            self.save()

        except Exception:
            pass

    # ------------------------------------------------------------------
    # DAILY HELPERS
    # ------------------------------------------------------------------
    def _existing_daily_dates(self) -> set:
        out = set()
        for d in self.data.get("daily", []):
            try:
                out.add(
                    datetime.strptime(d["Ngày"], DATE_FMT).date()
                )
            except Exception:
                continue
        return out

    def _add_daily_record(self, record: Dict):
        try:
            d = datetime.strptime(record["Ngày"], DATE_FMT).date()
        except Exception:
            return

        existing = self._existing_daily_dates()
        if d in existing:
            return

        self.data["daily"].append(record)
        self.data["daily"].sort(
            key=lambda x: datetime.strptime(x["Ngày"], DATE_FMT)
        )

    def get_missing_daily_ranges(self) -> List[Tuple[date, date]]:
        today = date.today() - timedelta(days=1)

        if self.history_start_date > today:
            return []

        existing = self._existing_daily_dates()
        if not existing:
            return [(self.history_start_date, today)]

        missing = []
        start = None

        for d in daterange(self.history_start_date, today):
            if d not in existing:
                start = start or d
            else:
                if start:
                    missing.append((start, d - timedelta(days=1)))
                    start = None

        if start:
            missing.append((start, today))

        return missing

    # ------------------------------------------------------------------
    # DAILY BACKFILL (chạy nền)
    # ------------------------------------------------------------------
    def start_background_backfill(self, api):
        self.hass.async_create_task(
            self._async_run_daily_backfill(api)
        )

    async def _async_run_daily_backfill(self, api):
        async with self._lock:
            for start, end in self.get_missing_daily_ranges():
                if start > end:
                    continue

                daily_raw = await api.fetch_daily_range_evnspc(
                    self.customer_id,
                    start.strftime("%Y%m%d"),
                    end.strftime("%Y%m%d"),
                )

                if not isinstance(daily_raw, list):
                    continue

                for d in daily_raw:
                    if not d.get("strTime"):
                        continue

                    record = {
                        "Ngày": d["strTime"].replace("/", "-"),
                        "Điện tiêu thụ (kWh)": d.get("dSanLuongBT"),
                        "Tiền điện (VND)": None,
                    }
                    self._add_daily_record(record)

                self.save()

    # ------------------------------------------------------------------
    # MONTHLY SYNC (FULL + INCREMENTAL)
    # ------------------------------------------------------------------
    def _existing_months(self) -> set:
        return {
            (m.get("Năm"), m.get("Tháng"))
            for m in self.data.get("monthly", [])
            if m.get("Năm") and m.get("Tháng")
        }

    async def async_sync_monthly_history(self, api):
        """
        - First run: backfill ALL missing months
        - Later runs: only fetch new month when month changes
        """

        today = date.today()
        end_year = today.year
        end_month = today.month - 1 or 12

        existing = self._existing_months()
        updated = False

        y, m = (
            self.history_start_date.year,
            self.history_start_date.month,
        )

        while (y, m) <= (end_year, end_month):
            if (y, m) not in existing:
                bills = await api.fetch_monthly_bills_evnspc(
                    self.customer_id, m, y, m, y
                )

                if isinstance(bills, list):
                    for b in bills:
                        self.data["monthly"].append({
                            "Tháng": b.get("iThang"),
                            "Năm": b.get("iNam"),
                            "Điện tiêu thụ (KWh)": b.get("dSanLuong"),
                            "Tiền Điện": b.get("lTongTien"),
                        })
                    updated = True

            # next month
            if m == 12:
                y += 1
                m = 1
            else:
                m += 1

        if updated:
            self.data["monthly"].sort(
                key=lambda x: (x.get("Năm"), x.get("Tháng"))
            )
            self.save()

    # ------------------------------------------------------------------
    # WEB UI EXPORT
    # ------------------------------------------------------------------
    def get_data_for_webui(self) -> Dict:
        daily_out = [
            {
                "Ngày": d.get("Ngày"),
                "Điện tiêu thụ (kWh)": float(
                    d.get("Điện tiêu thụ (kWh)") or 0
                ),
                "Tiền điện (VND)": d.get("Tiền điện (VND)"),
            }
            for d in self.data.get("daily", [])
        ]

        monthly_sanluong = []
        monthly_tiendien = []

        for r in self.data.get("monthly", []):
            kwh = (
                r.get("Điện tiêu thụ (KWh)")
                or r.get("Điện tiêu thụ (kWh)")
                or 0
            )
            cost = (
                r.get("Tiền Điện")
                or r.get("Tiền điện (VND)")
                or 0
            )

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
            "daily": daily_out,
            "monthly": {
                "SanLuong": monthly_sanluong,
                "TienDien": monthly_tiendien,
            },
        }
