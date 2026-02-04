import logging
import base64
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from ..const import (
    CONF_SUCCESS,
    CONF_ERR_INVALID_AUTH,
    CONF_ERR_NO_MONITOR,
    STATUS_PAYMENT_NEEDED,
    STATUS_N_PAYMENT_NEEDED,
)
from .base import EVNRegion
from ..types import EVNUpdateResponse, DailyHistoryRecord, MonthlyBillRecord

_LOGGER = logging.getLogger(__name__)

LOGIN_TTL = timedelta(minutes=10)


class CPCRegion(EVNRegion):
    def __init__(self, hass, session, evn_area):
        super().__init__(hass, session, evn_area)
        self._logged_in = False
        self._last_login = None

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------
    def _need_login(self) -> bool:
        if not self._logged_in or not self._last_login:
            return True
        return (dt_util.utcnow() - self._last_login) > LOGIN_TTL

    async def login(self, username, password, customer_id=None) -> str:
        payload = {
            "username": username,
            "password": password,
            "scope": "CSKH offline_access",
            "grant_type": "password",
        }

        basic_auth = "CSKH_Mobile_Notification:Evncpc@CC2023!Annv1609#"
        headers = {
            "Authorization": f"Basic {base64.b64encode(basic_auth.encode()).decode()}"
        }

        status, resp_json = await self._request(
            "POST",
            self._evn_area["evn_login_url"],
            data=payload,
            headers=headers,
            api_name="CPC Login",
        )

        if status == CONF_SUCCESS and resp_json.get("access_token"):
            self._evn_area["access_token"] = resp_json["access_token"]
            self._logged_in = True
            self._last_login = dt_util.utcnow()
            return CONF_SUCCESS

        self._logged_in = False
        return CONF_ERR_INVALID_AUTH

    # ------------------------------------------------------------------
    # Realtime update
    # ------------------------------------------------------------------
    async def request_update(
        self, username, password, customer_id, from_date=None, to_date=None
    ) -> EVNUpdateResponse:

        if self._need_login():
            if await self.login(username, password, customer_id) != CONF_SUCCESS:
                return EVNUpdateResponse(status=CONF_ERR_INVALID_AUTH)

        headers = {
            "Authorization": f"Bearer {self._evn_area.get('access_token')}"
        }

        status, resp_json = await self._request(
            "GET",
            f"{self._evn_area.get('evn_data_url')}{customer_id}",
            headers=headers,
            api_name="CPC Data",
        )

        if status != CONF_SUCCESS:
            return EVNUpdateResponse(status=status)

        electric = (
            resp_json.get("electricConsumption")
            if isinstance(resp_json, dict)
            else None
        )
        if not electric or not isinstance(electric, dict):
            return EVNUpdateResponse(status=CONF_ERR_NO_MONITOR)

        from_dt = None
        if from_date:
            try:
                from_dt = datetime.strptime(from_date, "%d/%m/%Y").date()
            except Exception:
                pass

        record = EVNUpdateResponse(
            status=CONF_SUCCESS,
            econ_daily_new=round(
                float(electric.get("electricConsumptionToday", 0)), 2
            ),
            econ_daily_old=round(
                float(electric.get("electricConsumptionYesterday", 0)), 2
            ),
            econ_monthly_new=round(
                float(electric.get("electricConsumptionThisMonth", 0)), 2
            ),
            from_date=from_dt,
            payment_needed=STATUS_N_PAYMENT_NEEDED,
            m_payment_needed=0,
        )

        # --------------------------------------------------------------
        # Billing info
        # --------------------------------------------------------------
        status, resp_json = await self._request(
            "GET",
            f"{self._evn_area.get('evn_payment_url')}{customer_id}",
            headers=headers,
            api_name="CPC Bill",
        )

        response = (
            resp_json.get("response")
            if status == CONF_SUCCESS and isinstance(resp_json, dict)
            else None
        )

        if response:
            if response.get("tinhTrangThanhToan") != "Đã thanh toán":
                record.payment_needed = STATUS_PAYMENT_NEEDED
                try:
                    record.m_payment_needed = int(
                        response.get("tienHoaDon", "0")
                        .replace(".", "")
                        .replace("đ", "")
                    )
                except Exception:
                    pass

            curr = response.get("dienNangHienTai", {})
            try:
                t_dt = datetime.strptime(curr.get("thoiDiem"), "%Hh%M - %d/%m/%Y")
            except Exception:
                t_dt = datetime.now()

            try:
                record.econ_total_new = round(
                    float(
                        curr.get("chiSo", "0")
                        .replace(".", "")
                        .replace(",", ".")
                    ),
                    2,
                )
            except Exception:
                pass

            try:
                record.econ_total_old = round(
                    float(
                        response.get("chiSoCuoiKy", "0")
                        .replace(".", "")
                        .replace(",", ".")
                    ),
                    2,
                )
            except Exception:
                pass

            record.to_date = t_dt.date()
            record.previous_date = (t_dt - timedelta(days=1)).date()

        return record

    # ------------------------------------------------------------------
    # Daily history
    # ------------------------------------------------------------------
    async def fetch_daily_range(self, customer_id: str):
        headers = {
            "Authorization": f"Bearer {self._evn_area.get('access_token')}"
        }
        status, resp_json = await self._request(
            "GET",
            "https://cskh-api.cpc.vn/api/remote/meter/rf/sl-tieu-thu-view",
            params={
                "customerCode": customer_id,
                "orgCode": customer_id[:6],
            },
            headers=headers,
            api_name="CPC Daily Range",
        )
        return resp_json if status == CONF_SUCCESS else []

    async def fetch_daily_history(
        self, username, password, customer_id: str, start_date, end_date
    ) -> list[DailyHistoryRecord]:

        if self._need_login():
            if await self.login(username, password, customer_id) != CONF_SUCCESS:
                return []

        raw = await self.fetch_daily_range(customer_id)
        if not isinstance(raw, list):
            return []

        results = []
        for d in raw:
            ngay, kwh = d.get("ngay"), d.get("sanLuongNgay")
            if not ngay or kwh is None:
                continue
            try:
                dt = datetime.fromisoformat(ngay.replace("Z", "")).date()
                if start_date <= dt <= end_date:
                    results.append(
                        DailyHistoryRecord(date=dt, kwh=float(kwh))
                    )
            except Exception:
                continue

        return results

    # ------------------------------------------------------------------
    # Monthly history
    # ------------------------------------------------------------------
    async def fetch_monthly_bills(self, customer_id: str):
        headers = {
            "Authorization": f"Bearer {self._evn_area.get('access_token')}"
        }
        status, resp_json = await self._request(
            "GET",
            "https://cskh-api.cpc.vn/api/remote/thongTinHoaDonSpider",
            params={
                "customerCode": customer_id,
                "maDonViQuanLy": customer_id[:6],
            },
            headers=headers,
            api_name="CPC Bills",
        )
        return resp_json.get("result", []) if status == CONF_SUCCESS else []

    async def fetch_monthly_history(
        self, username, password, customer_id: str, history_start_date
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
                year = int(b.get("NAM"))
                month = int(b.get("THANG"))
                kwh = float(b.get("DIEN_TTHU") or b.get("SAN_LUONG") or 0)
                cost = (
                    int(b.get("TONG_TIEN"))
                    if b.get("TONG_TIEN") is not None
                    else None
                )

                dky = b.get("NGAY_DKY")
                if dky:
                    bill_date = datetime.fromisoformat(
                        dky.replace("Z", "")
                    ).date()
                    if bill_date < history_start_date:
                        continue
                elif (year, month) < (
                    history_start_date.year,
                    history_start_date.month,
                ):
                    continue

                results.append(
                    MonthlyBillRecord(
                        month=month,
                        year=year,
                        kwh=kwh,
                        cost=cost,
                    )
                )
            except Exception:
                continue

        return results
