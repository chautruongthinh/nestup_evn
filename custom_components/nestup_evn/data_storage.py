import json
import os
import asyncio
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple, Optional

from homeassistant.core import HomeAssistant
from .utils import calc_ecost, parse_evnhanoi_money

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
    # DAILY REALTIME UPDATE (từ sensor)
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

        if d in self._existing_daily_dates():
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
    # DAILY BACKFILL
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

                updated = False

                # ===============================
                # EVN SPC
                # ===============================
                if api._evn_area.get("name") == "EVNSPC":
                    daily_raw = await api.fetch_daily_range_evnspc(
                        self.customer_id,
                        start.strftime("%d-%m-%Y"),
                        end.strftime("%d-%m-%Y"),
                    )

                    if isinstance(daily_raw, list):
                        for d in daily_raw:
                            if not d.get("strTime"):
                                continue
                            try:
                                d_date = datetime.strptime(
                                    d["strTime"], "%d/%m/%Y"
                                ).date()
                            except Exception:
                                continue

                            if not (start <= d_date <= end):
                                continue

                            record = {
                                "Ngày": d["strTime"].replace("/", "-"),
                                "Điện tiêu thụ (kWh)": float(
                                    d.get("dSanLuongBT") or 0
                                ),
                                "Tiền điện (VND)": None,
                            }
                            self._add_daily_record(record)
                            updated = True

                # ===============================
                # EVN NPC
                # ===============================
                elif api._evn_area.get("name") == "EVNNPC":
                    daily_raw = await api.fetch_daily_range_evnnpc(
                        self.customer_id,
                        start,
                        end,
                    )

                    if isinstance(daily_raw, list):
                        for d in daily_raw:
                            if not d.get("NGAY"):
                                continue
                            try:
                                d_date = datetime.strptime(
                                    d["NGAY"], "%d/%m/%Y"
                                ).date()
                            except Exception:
                                continue

                            if not (start <= d_date <= end):
                                continue

                            record = {
                                "Ngày": d["NGAY"].replace("/", "-"),
                                "Điện tiêu thụ (kWh)": float(
                                    d.get("DIEN_TTHU") or 0
                                ),
                                "Tiền điện (VND)": None,
                            }
                            self._add_daily_record(record)
                            updated = True

                # ===============================
                # EVN CPC
                # ===============================
                elif api._evn_area.get("name") == "EVNCPC":
                    daily_raw = await api.fetch_daily_range_evncpc(self.customer_id)

                    if not isinstance(daily_raw, list):
                        continue

                    for d in daily_raw:
                        ngay = d.get("ngay")
                        kwh = d.get("sanLuongNgay")

                        if not ngay or kwh is None:
                            continue

                        record = {
                            "Ngày": datetime.fromisoformat(
                                ngay.replace("Z", "")
                            ).strftime(DATE_FMT),
                            "Điện tiêu thụ (kWh)": float(kwh),
                            "Tiền điện (VND)": None,
                        }

                        self._add_daily_record(record)
                        updated = True

                # ===============================
                # EVN HCMC
                # ===============================
                elif api._evn_area.get("name") == "EVNHCMC":
                    daily_raw = await api.fetch_daily_range_evnhcmc(
                        self.customer_id,
                        start.strftime("%d/%m/%Y"),
                        end.strftime("%d/%m/%Y"),
                    )

                    if isinstance(daily_raw, list):
                        for d in daily_raw:
                            ngay = d.get("ngayFull")
                            kwh = d.get("Tong")

                            if not ngay or kwh is None:
                                continue

                            record = {
                                "Ngày": ngay.replace("/", "-"),
                                "Điện tiêu thụ (kWh)": float(kwh),
                                "Tiền điện (VND)": None,
                            }

                            self._add_daily_record(record)
                            updated = True

                # ===============================
                # EVN HANOI
                # ===============================
                elif api._evn_area.get("name") == "EVNHANOI":
                    fetch_start = start - timedelta(days=1)

                    raw = await api.fetch_daily_range_evnhanoi(
                        self.customer_id, fetch_start, end
                    )

                    if not isinstance(raw, list):
                        continue

                    parsed = []
                    for d in raw:
                        s = d.get("ngayShort") or d.get("ngay")
                        chi_so = d.get("chiSo")
                        if not s or chi_so is None:
                            continue
                        try:
                            parsed.append((
                                datetime.strptime(s, "%d/%m/%Y").date(),
                                float(chi_so),
                            ))
                        except Exception:
                            continue

                    if len(parsed) < 2:
                        continue

                    parsed.sort(key=lambda x: x[0])

                    prev_date, prev_index = parsed[0]

                    for cur_date, cur_index in parsed[1:]:
                        kwh = max(0.0, cur_index - prev_index)

                        if prev_date and start <= prev_date <= end:
                            record = {
                                "Ngày": prev_date.strftime(DATE_FMT),
                                "Điện tiêu thụ (kWh)": round(kwh, 3),
                                "Tiền điện (VND)": None,
                            }
                            self._add_daily_record(record)
                            updated = True
                        prev_date, prev_index = cur_date, cur_index

                if updated:
                    self.save()

    # ------------------------------------------------------------------
    # MONTHLY HELPERS
    # ------------------------------------------------------------------
    def _monthly_record_key(
        self,
        record: dict | None = None,
        *,
        invoice_id: str | None = None,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple | None:
        """
        Unified monthly key for SPC & NPC.
        - NPC: use invoice_id (NOT stored in JSON)
        - SPC: use (year, month)
        """

        if invoice_id:
            return ("NPC", invoice_id)

        if year and month:
            return ("MONTH", year, month)

        if record:
            y = record.get("Năm")
            m = record.get("Tháng")
            if y and m:
                return ("MONTH", y, m)

        return None


    def _existing_monthly_keys(self) -> set:
        keys = set()
        for r in self.data.get("monthly", []):
            k = self._monthly_record_key(record=r)
            if k:
                keys.add(k)
        return keys

    # ------------------------------------------------------------------
    # MONTHLY SYNC
    # ------------------------------------------------------------------
    async def async_sync_monthly_history(self, api):
        existing_keys = self._existing_monthly_keys()
        updated = False

        # ===============================
        # EVN SPC
        # ===============================
        if api._evn_area.get("name") == "EVNSPC":
            today = date.today()
            end_year = today.year
            end_month = today.month - 1 or 12

            y, m = (
                self.history_start_date.year,
                self.history_start_date.month,
            )

            while (y, m) <= (end_year, end_month):
                record = {
                    "Tháng": m,
                    "Năm": y,
                }
                key = self._monthly_record_key(record)

                if key not in existing_keys:
                    bills = await api.fetch_monthly_bills_evnspc(
                        self.customer_id, m, y, m, y
                    )

                    if isinstance(bills, list):
                        for b in bills:
                            record = {
                                "Tháng": b.get("iThang"),
                                "Năm": b.get("iNam"),
                                "Điện tiêu thụ (KWh)": b.get("dSanLuong"),
                                "Tiền Điện": b.get("lTongTien"),
                            }
                            k = self._monthly_record_key(record)
                            if k and k not in existing_keys:
                                self.data["monthly"].append(record)
                                existing_keys.add(k)
                                updated = True

                if m == 12:
                    y += 1
                    m = 1
                else:
                    m += 1

        # ===============================
        # EVN NPC
        # ===============================        
        elif api._evn_area.get("name") == "EVNNPC":
            bills = await api.fetch_monthly_bills_evnnpc(
                self.customer_id,
                self.history_start_date.month,
                self.history_start_date.year,
                date.today().month,
                date.today().year,
            )

            if not isinstance(bills, list):
                return

            bills.sort(key=lambda x: (x.get("NAM", 0), x.get("THANG", 0)))

            existing_keys = self._existing_monthly_keys()

            for b in bills:
                year = b.get("NAM")
                month = b.get("THANG")
                kwh = b.get("DIEN_TTHU")

                if not year or not month or kwh is None:
                    continue

                key = self._monthly_record_key(
                    year=year,
                    month=month,
                )

                if key in existing_keys:
                    continue

                record = {
                    "Tháng": month,
                    "Năm": year,
                    "Điện tiêu thụ (KWh)": float(kwh),
                    "Tiền Điện": calc_ecost(float(kwh)),
                }

                self.data["monthly"].append(record)
                existing_keys.add(key)
                updated = True

        # ===============================
        # EVN CPC
        # ===============================
        elif api._evn_area.get("name") == "EVNCPC":

            bills = await api.fetch_monthly_bills_evncpc(self.customer_id)
            if not isinstance(bills, list):
                return

            existing_keys = self._existing_monthly_keys()
            updated = False

            start_date = self.history_start_date

            for b in bills:
                try:
                    year = int(b.get("NAM"))
                    month = int(b.get("THANG"))
                    kwh = float(b.get("DIEN_TTHU") or b.get("SAN_LUONG") or 0)
                    cost = int(b.get("TONG_TIEN")) if b.get("TONG_TIEN") is not None else None
                except Exception:
                    continue

                try:
                    dky = b.get("NGAY_DKY")
                    if dky:
                        bill_date = datetime.fromisoformat(dky.replace("Z", "")).date()
                        if bill_date < start_date:
                            continue
                except Exception:
                    if (year, month) < (start_date.year, start_date.month):
                        continue

                key = self._monthly_record_key(year=year, month=month)
                if key in existing_keys:
                    continue

                record = {
                    "Tháng": month,
                    "Năm": year,
                    "Điện tiêu thụ (KWh)": kwh,
                    "Tiền Điện": cost,
                }

                self.data["monthly"].append(record)
                existing_keys.add(key)
                updated = True

            if updated:
                self.data["monthly"].sort(
                    key=lambda x: (x.get("Năm"), x.get("Tháng"))
                )
                self.save()


        # ===============================
        # EVN HCMC
        # ===============================
        elif api._evn_area.get("name") == "EVNHCMC":
            bills = await api.fetch_monthly_bills_evnhcmc(self.customer_id)

            if not isinstance(bills, list):
                return

            existing_keys = self._existing_monthly_keys()

            for b in bills:
                year = int(b.get("NAM"))
                month = int(b.get("THANG"))
                kwh = float(b.get("SAN_LUONG", 0))
                cost = float(b.get("TONG_TIEN", 0))

                key = self._monthly_record_key(year=year, month=month)
                if key in existing_keys:
                    continue

                record = {
                    "Tháng": month,
                    "Năm": year,
                    "Điện tiêu thụ (KWh)": kwh,
                    "Tiền Điện": int(cost),
                }

                self.data["monthly"].append(record)
                existing_keys.add(key)
                updated = True

        # ===============================
        # EVN HANOI
        # ===============================
        elif api._evn_area.get("name") == "EVNHANOI":

            bills = await api.fetch_monthly_bills_evnhanoi(self.customer_id)
            if not isinstance(bills, list):
                return

            existing_keys = self._existing_monthly_keys()
            updated = False

            for b in bills:
                try:
                    year = int(b.get("nam"))
                    month = int(b.get("thang"))
                    kwh = float(b.get("dienTthu"))
                except Exception:
                    continue

                cost = parse_evnhanoi_money(b.get("soTien"))

                key = self._monthly_record_key(year=year, month=month)
                if key in existing_keys:
                    continue

                record = {
                    "Tháng": month,
                    "Năm": year,
                    "Điện tiêu thụ (KWh)": kwh,
                    "Tiền Điện": cost,
                }

                self.data["monthly"].append(record)
                existing_keys.add(key)
                updated = True
       
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
