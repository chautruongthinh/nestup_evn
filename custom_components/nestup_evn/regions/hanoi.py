import logging
import time
from datetime import date, timedelta, datetime
from dateutil import parser

from homeassistant.util import dt as dt_util

from ..const import (
    CONF_SUCCESS,
    CONF_ERR_INVALID_AUTH,
    CONF_ERR_INVALID_ID,
    CONF_ERR_NO_MONITOR,
    STATUS_PAYMENT_NEEDED,
    STATUS_N_PAYMENT_NEEDED,
)
from .base import EVNRegion
from ..utils import parse_evnhanoi_money
from ..types import EVNUpdateResponse, DailyHistoryRecord, MonthlyBillRecord

_LOGGER = logging.getLogger(__name__)

LOGIN_TTL = timedelta(minutes=10)


class HanoiRegion(EVNRegion):
    def __init__(self, hass, session, evn_area):
        super().__init__(hass, session, evn_area)
        self._evnhanoi_contract = None
        self._logged_in = False
        self._last_login = None

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------
    def _need_login(self) -> bool:
        if not self._logged_in or not self._last_login:
            return True
        if (dt_util.utcnow() - self._last_login) > LOGIN_TTL:
            return True
        expiry = self._evn_area.get("token_expiry")
        return bool(expiry and time.time() > expiry)

    async def login(self, username, password, customer_id) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }
        payload = {
            "username": username,
            "password": password,
            "client_id": "httplocalhost4500",
            "client_secret": "secret",
            "grant_type": "password",
        }

        status, resp_json = await self._request(
            "POST",
            self._evn_area.get("evn_login_url"),
            data=payload,
            headers=headers,
            api_name="Hanoi Login",
        )

        if status != CONF_SUCCESS:
            self._logged_in = False
            return status

        if resp_json.get("error") == "invalid_grant":
            self._logged_in = False
            return CONF_ERR_INVALID_AUTH

        token = resp_json.get("access_token")
        if not token:
            self._logged_in = False
            return CONF_ERR_INVALID_AUTH

        self._evn_area["access_token"] = token
        if resp_json.get("expires_in"):
            self._evn_area["token_expiry"] = time.time() + resp_json["expires_in"]

        self._logged_in = True
        self._last_login = dt_util.utcnow()
        self._evnhanoi_contract = None  # reset cache on new login
        return CONF_SUCCESS

    # ------------------------------------------------------------------
    # Realtime update
    # ------------------------------------------------------------------
    async def request_update(
        self, username, password, customer_id, from_date, to_date
    ) -> EVNUpdateResponse:
        return await self._request_update_internal(
            username, password, customer_id, from_date, to_date
        )

    async def _request_update_internal(
        self, username, password, customer_id, from_date, to_date, last_index="001"
    ) -> EVNUpdateResponse:

        if self._need_login():
            if await self.login(username, password, customer_id) != CONF_SUCCESS:
                return EVNUpdateResponse(status=CONF_ERR_INVALID_AUTH)

        headers = {
            "Authorization": f"Bearer {self._evn_area.get('access_token')}",
            "Content-Type": "application/json",
        }

        payload = {
            "maDiemDo": f"{customer_id}{last_index}",
            "maDonVi": customer_id[:6],
            "maXacThuc": "EVNHN",
            "ngayDau": from_date,
            "ngayCuoi": to_date,
        }

        status, resp_json = await self._request(
            "POST",
            self._evn_area.get("evn_data_url"),
            json_data=payload,
            headers=headers,
            api_name="Hanoi Data",
        )

        if status != CONF_SUCCESS:
            return EVNUpdateResponse(status=status)

        if resp_json.get("isError"):
            if resp_json.get("code") == 400 and last_index == "001":
                return await self._request_update_internal(
                    username, password, customer_id, from_date, to_date, last_index="1"
                )
            return EVNUpdateResponse(status=CONF_ERR_INVALID_ID)

        data = resp_json.get("data", {}).get("chiSoNgay", [])
        if not isinstance(data, list) or len(data) < 2:
            return EVNUpdateResponse(status=CONF_ERR_NO_MONITOR)

        data.sort(key=lambda x: parser.parse(x["ngay"], dayfirst=True))

        f_date = parser.parse(data[0]["ngay"], dayfirst=True).date()
        t_date = (
            parser.parse(data[-1]["ngay"], dayfirst=True).date()
            - timedelta(days=1)
        )
        p_date = (
            parser.parse(data[-2]["ngay"], dayfirst=True).date()
            - timedelta(days=1)
        )

        totals = [float(d["sg"]) for d in data]

        record = EVNUpdateResponse(
            status=CONF_SUCCESS,
            econ_total_old=round(totals[0], 2),
            econ_total_new=round(totals[-1], 2),
            econ_daily_new=round(totals[-1] - totals[-2], 2),
            econ_daily_old=round(totals[-2] - totals[-3], 2)
            if len(totals) >= 3
            else 0.0,
            econ_monthly_new=round(totals[-1] - totals[0], 2),
            from_date=f_date,
            to_date=t_date,
            previous_date=p_date,
            payment_needed=STATUS_N_PAYMENT_NEEDED,
            m_payment_needed=0,
        )

        # Payment
        pay_payload = {
            "maKhachHang": customer_id,
            "maDonViQuanLy": customer_id[:6],
        }
        p_status, p_json = await self._request(
            "POST",
            self._evn_area.get("evn_payment_url"),
            json_data=pay_payload,
            headers=headers,
            api_name="Hanoi Payment",
        )

        if (
            p_status == CONF_SUCCESS
            and not p_json.get("isError")
            and p_json.get("data", {}).get("listThongTinNoKhachHangVm")
        ):
            record.payment_needed = STATUS_PAYMENT_NEEDED
            record.m_payment_needed = int(
                p_json["data"]["listThongTinNoKhachHangVm"][0]["tongTien"].replace(".", "")
            )

        return record

    # ------------------------------------------------------------------
    # Contract helper
    # ------------------------------------------------------------------
    async def fetch_contract(self, customer_id: str):
        if self._evnhanoi_contract:
            return self._evnhanoi_contract

        headers = {
            "Authorization": f"Bearer {self._evn_area.get('access_token')}",
            "Accept": "application/json",
        }

        status, data = await self._request(
            "GET",
            "https://evnhanoi.vn/api/TraCuu/GetDanhSachHopDongByUserName",
            headers=headers,
            api_name="Hanoi Contract",
        )

        if status == CONF_SUCCESS:
            contracts = data.get("data", {}).get("thongTinHopDongDtos", [])
            for c in contracts:
                if c.get("maKhachHang") == customer_id:
                    self._evnhanoi_contract = c
                    return c
        return None

    # ------------------------------------------------------------------
    # Daily history
    # ------------------------------------------------------------------
    async def fetch_daily_history(
        self, username, password, customer_id: str, start_date: date, end_date: date
    ) -> list[DailyHistoryRecord]:

        if self._need_login():
            if await self.login(username, password, customer_id) != CONF_SUCCESS:
                return []

        f_dt = start_date - timedelta(days=1)

        async def _fetch_phase1(idx):
            headers = {
                "Authorization": f"Bearer {self._evn_area.get('access_token')}",
                "Content-Type": "application/json",
            }
            payload = {
                "maDiemDo": f"{customer_id}{idx}",
                "maDonVi": customer_id[:6],
                "maXacThuc": "EVNHN",
                "ngayDau": f_dt.strftime("%d/%m/%Y"),
                "ngayCuoi": end_date.strftime("%d/%m/%Y"),
            }
            status, resp = await self._request(
                "POST",
                self._evn_area.get("evn_data_url"),
                json_data=payload,
                headers=headers,
                api_name=f"Hanoi History ({idx})",
            )
            if status == CONF_SUCCESS:
                return resp.get("data", {}).get("chiSoNgay", [])
            return []

        async def _fetch_phase2():
            contract = await self.fetch_contract(customer_id)
            if not contract:
                return []
            payload = {
                "maDonVi": contract["maDonViQuanLy"],
                "maDiemDo": f"{contract['maKhachHang']}001",
                "maXacThuc": "EVNHN",
                "ngayDau": f_dt.strftime("%d/%m/%Y"),
                "ngayCuoi": end_date.strftime("%d/%m/%Y"),
            }
            headers = {
                "Authorization": f"Bearer {self._evn_area.get('access_token')}"
            }
            status, resp = await self._request(
                "POST",
                "https://evnhanoi.vn/api/TraCuu/LayChiSoDoXaPharse2",
                json_data=payload,
                headers=headers,
                api_name="Hanoi History (P2)",
            )
            if status == CONF_SUCCESS:
                return resp.get("data", {}).get("chiSoNgayFull", [])
            return []

        data = await _fetch_phase1("001") or await _fetch_phase1("1") or await _fetch_phase2()
        if not data:
            return []

        parsed = []
        for d in data:
            try:
                parsed.append(
                    (
                        parser.parse(d["ngay"], dayfirst=True).date(),
                        float(d["sg"]),
                    )
                )
            except Exception:
                continue

        parsed.sort(key=lambda x: x[0])
        if len(parsed) < 2:
            return []

        results = []
        for i in range(len(parsed) - 1):
            dt, val = parsed[i]
            nxt_val = parsed[i + 1][1]
            if start_date <= dt <= end_date:
                results.append(
                    DailyHistoryRecord(
                        date=dt, kwh=round(max(0.0, nxt_val - val), 3)
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Monthly history
    # ------------------------------------------------------------------
    async def fetch_monthly_history(
        self, username, password, customer_id: str, history_start_date: date
    ) -> list[MonthlyBillRecord]:

        if self._need_login():
            if await self.login(username, password, customer_id) != CONF_SUCCESS:
                return []

        raw = await self.fetch_monthly_bills(customer_id)
        if not isinstance(raw, list):
            return []

        results = []
        for b in raw:
            try:
                year = int(b.get("nam"))
                month = int(b.get("thang"))
                if (year, month) < (
                    history_start_date.year,
                    history_start_date.month,
                ):
                    continue
                results.append(
                    MonthlyBillRecord(
                        month=month,
                        year=year,
                        kwh=float(b.get("dienTthu")),
                        cost=parse_evnhanoi_money(b.get("soTien")),
                    )
                )
            except Exception:
                continue

        return results

    async def fetch_monthly_bills(self, customer_id: str):
        contract = await self.fetch_contract(customer_id)
        if not contract:
            return []
        today = date.today()
        headers = {
            "Authorization": f"Bearer {self._evn_area.get('access_token')}"
        }
        params = {
            "maDvQly": contract["maDonViQuanLy"],
            "maKh": customer_id,
            "thang": today.month,
            "nam": today.year,
        }
        status, data = await self._request(
            "GET",
            "https://evnhanoi.vn/api/TraCuu/GetLichSuThanhToan",
            params=params,
            headers=headers,
            api_name="Hanoi Bills",
        )
        return data.get("data", {}).get("dmLichSuThanhToanList", []) if status == CONF_SUCCESS else []
