import json
import logging
import os
import asyncio
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple, Optional

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from .utils import calc_ecost, parse_evnhanoi_money
from .const import (
    DOMAIN, 
    CONF_AREA, 
    ID_ECON_MONTHLY_NEW, 
    ID_ECOST_MONTHLY_NEW
)

DATE_FMT = "%d-%m-%Y"
_LOGGER = logging.getLogger(__name__)
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
        self.customer_id = customer_id.strip() if customer_id else ""

        self.storage_dir = hass.config.path("nestup_evn")
        os.makedirs(self.storage_dir, exist_ok=True)

        self.file_path = os.path.join(
            self.storage_dir, f"{self.customer_id}.json"
        )

        self._lock = asyncio.Lock()

        self.history_start_date = (
            history_start_date or DEFAULT_HISTORY_START_DATE
        )

        self._meta = {
            "last_monthly_sync": None
        }

        # Non-blocking initialization
        self.data: Dict = {"daily": [], "monthly": [], "_meta": self._meta}
        self._backfill_done = False

    # ------------------------------------------------------------------
    # BASIC STORAGE
    # ------------------------------------------------------------------
    def _load(self) -> Dict:
        """Synchronous load from file."""
        if not self.customer_id:
            return {"daily": [], "monthly": []}
        
        if not os.path.exists(self.file_path):
            _LOGGER.debug("Storage: File %s not found, starting fresh", self.file_path)
            return {"daily": [], "monthly": []}
            
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                content = f.read()
                if not content.strip():
                    _LOGGER.warning("Storage: File %s is empty", self.file_path)
                    return {"daily": [], "monthly": []}
                
                data = json.loads(content)
                if not isinstance(data, dict):
                    _LOGGER.error("Storage: Malformed data in %s (not a dict)", self.file_path)
                    return {"daily": [], "monthly": []}
                    
                data.setdefault("daily", [])
                data.setdefault("monthly", [])
                data.setdefault("_meta", {})

                self._meta = data["_meta"]
                self._meta.setdefault("last_monthly_sync", None)                
                
                _LOGGER.debug("Storage: Successfully loaded %d daily and %d monthly records for %s", len(data["daily"]), len(data["monthly"]), self.customer_id)
                return data
        except Exception as e:
            _LOGGER.error("Storage: Failed to load %s: %s", self.file_path, e)
            # We raise here to let async_load know it failed
            raise

    def _save_sync(self):
        """Synchronous save to file."""
        try:
            temp_path = self.file_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.file_path)
            _LOGGER.debug("Storage: Saved history for %s to %s", self.customer_id, self.file_path)
        except Exception as e:
            _LOGGER.error("Storage: Failed to save %s: %s", self.file_path, e)
            raise

    async def async_save(self):
        """Save data asynchronously."""
        # The lock should be handled by the caller if they are doing multiple operations.
        # But for a simple save, the executor job itself is atomic enough for the file write.
        await self.hass.async_add_executor_job(self._save_sync)

    async def async_load(self):
        """Load data asynchronously."""
        self.data = await self.hass.async_add_executor_job(self._load)

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

            async with self._lock:
                self._add_daily_record(record)
                
                # Update monthly data from sensor
                m_kwh = data.get(ID_ECON_MONTHLY_NEW)
                m_cost = data.get(ID_ECOST_MONTHLY_NEW)
                
                if hasattr(to_date, "month") and m_kwh is not None:
                    m_list = self.data.setdefault("monthly", [])
                    found_m = False
                    for m_rec in m_list:
                        if m_rec.get("Tháng") == to_date.month and m_rec.get("Năm") == to_date.year:
                            m_rec["Điện tiêu thụ (KWh)"] = float(m_kwh)
                            if m_cost is not None:
                                m_rec["Tiền Điện"] = int(m_cost)
                            found_m = True
                            break
                    
                    if not found_m:
                        rec = {
                            "Tháng": to_date.month,
                            "Năm": to_date.year,
                            "Điện tiêu thụ (KWh)": float(m_kwh),
                            "Tiền Điện": int(m_cost) if m_cost is not None else calc_ecost(float(m_kwh))
                        }
                        m_list.append(rec)

                await self.async_save()

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
            _LOGGER.warning("Storage: Invalid date format in record: %s", record)
            return

        if d in self._existing_daily_dates():
            return

        self.data["daily"].append(record)
        self.data["daily"].sort(
            key=lambda x: datetime.strptime(x["Ngày"], DATE_FMT)
        )
        _LOGGER.debug("Storage: Added daily record for %s to %s", record["Ngày"], self.customer_id)

    def get_missing_daily_ranges(self) -> List[Tuple[date, date]]:
        today = date.today() - timedelta(days=1)
        if self.history_start_date > today:
            return []

        existing = self._existing_daily_dates()
        ranges = []
        start = None

        for d in daterange(self.history_start_date, today + timedelta(days=1)):
            if d not in existing:
                start = start or d
            else:
                if start:
                    ranges.extend(self._split_range(start, d - timedelta(days=1)))
                    start = None

        if start:
            ranges.extend(self._split_range(start, today))

        return ranges

    def _split_range(self, start: date, end: date, chunk_days: int = 30) -> List[Tuple[date, date]]:
        """Split a large date range into smaller chunks."""
        chunks = []
        curr = start
        while curr <= end:
            next_end = min(curr + timedelta(days=chunk_days - 1), end)
            chunks.append((curr, next_end))
            curr = next_end + timedelta(days=1)
        return chunks

    # ------------------------------------------------------------------
    # DAILY BACKFILL
    # ------------------------------------------------------------------
    @property
    def backfill_done(self) -> bool:
        return self._backfill_done

    def start_background_backfill(self, api, area, username, password):
        if self._backfill_done:
            return
        self.hass.async_create_task(
            self._async_run_daily_backfill(api, area, username, password)
        )

    async def _async_run_daily_backfill(self, api, area, username, password):
        try:
            async with self._lock:
                missing = self.get_missing_daily_ranges()
            
            if not missing:
                self._backfill_done = True
                _LOGGER.debug("Storage: No missing daily ranges for %s", self.customer_id)
                return

            _LOGGER.debug("Storage: Starting backfill for %s (%d chunks)", self.customer_id, len(missing))
            instance = api.get_region_instance(area, self.customer_id)
            if not instance:
                _LOGGER.error("Storage: Could not get region instance for %s", area)
                return

            updated_any = False
            for start, end in missing:
                _LOGGER.debug("Storage: Fetching chunk %s to %s", start, end)
                try:
                    # Network call without lock
                    daily_records = await instance.fetch_daily_history(
                        username, password, self.customer_id, start, end
                    )
                    
                    if not daily_records:
                        _LOGGER.debug("Storage: No records for chunk %s to %s", start, end)
                        continue

                    updated = False
                    async with self._lock:
                        for record in daily_records:
                            self._add_daily_record(record.to_dict())
                            updated = True
                            updated_any = True

                        if updated:
                            await self.async_save()
                    
                    if updated:
                        _LOGGER.debug("Storage: Saved %d records for chunk %s to %s", len(daily_records), start, end)
                except Exception as e:
                    _LOGGER.error("Storage: Error fetching chunk %s to %s: %s", start, end, e)
                    continue
            
            self._backfill_done = True
            _LOGGER.debug("Storage: Backfill process finished for %s (Updated: %s)", self.customer_id, updated_any)
        except Exception as e:
            _LOGGER.error("Storage: Critical error in backfill for %s: %s", self.customer_id, e)

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
    def should_sync_monthly(self, interval_seconds: int = 10800) -> bool:
        """Check if monthly history should be synced (default: 3h)."""
        last = self._meta.get("last_monthly_sync")
        if not last:
            return True

        try:
            last_dt = datetime.fromisoformat(last)
        except Exception:
            return True

        return (dt_util.utcnow() - last_dt).total_seconds() >= interval_seconds

    async def async_sync_monthly_history(self, api, area, username, password):
        instance = api.get_region_instance(area, self.customer_id)
        if not instance: return

        monthly_records = await instance.fetch_monthly_history(
            username, password, self.customer_id, self.history_start_date
        )
        if not monthly_records: return

        existing_keys = self._existing_monthly_keys()
        updated = False

        for record in monthly_records:
            record_dict = record.to_dict()
            if record_dict.get("Tiền Điện") is None and record.kwh:
                record_dict["Tiền Điện"] = calc_ecost(record.kwh)
                
            key = self._monthly_record_key(record_dict)
            if key not in existing_keys:
                self.data["monthly"].append(record_dict)
                existing_keys.add(key)
                updated = True
       
        if updated:
            self.data["monthly"].sort(
                key=lambda x: (x.get("Năm"), x.get("Tháng"))
            )
            self._meta["last_monthly_sync"] = dt_util.utcnow().isoformat()
            await self.async_save()

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
