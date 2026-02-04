import logging
from datetime import datetime, date, timezone
from dateutil import parser

from ..const import (
    CONF_SUCCESS,
    CONF_ERR_INVALID_AUTH,
    STATUS_PAYMENT_NEEDED,
    STATUS_N_PAYMENT_NEEDED,
)
from .base import EVNRegion
from .utils import safe_float
from ..types import EVNUpdateResponse, DailyHistoryRecord, MonthlyBillRecord

_LOGGER = logging.getLogger(__name__)


def _to_date(value):
    """Local date normalizer (HCMC only)."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return parser.parse(value, dayfirst=True).date()
        except Exception:
            return None
    return None


class HCMCRegion(EVNRegion):
    def __init__(self, hass, session, evn_area):
        super().__init__(hass, session, evn_area)

    async def login(self, username, password, customer_id=None) -> str:
        payload = {"u": username, "p": password}
        status, resp_json = await self._request(
            "POST",
            self._evn_area.get("evn_login_url"),
            data=payload,
            api_name="HCMC Login",
        )

        if status != CONF_SUCCESS or not isinstance(resp_json, dict):
            return CONF_ERR_INVALID_AUTH

        if resp_json.get("state") not in ("success", "login"):
            return CONF_ERR_INVALID_AUTH

        cookies = self._session.cookie_jar.filter_cookies("https://cskh.evnhcmc.vn")
        evn_cookie = cookies.get("evn_session")
        if not evn_cookie:
            return CONF_ERR_INVALID_AUTH

        self._evn_area["evn_session"] = evn_cookie.value
        if evn_cookie["expires"]:
            self._evn_area["expires"] = parser.parse(evn_cookie["expires"]).astimezone(
                timezone.utc
            )

        return CONF_SUCCESS

    def _session_valid(self) -> bool:
        expires = self._evn_area.get("expires")
        if not expires:
            return False
        if isinstance(expires, str):
            expires = parser.parse(expires)
        return datetime.now(tz=timezone.utc) < expires

    async def request_update(self, username, password, customer_id, from_date, to_date):
        if not self._session_valid():
            if await self.login(username, password) != CONF_SUCCESS:
                return EVNUpdateResponse(status=CONF_ERR_INVALID_AUTH)

        headers = {"Cookie": f"evn_session={self._evn_area.get('evn_session')}"}
        payload = {
            "input_makh": customer_id,
            "input_tungay": from_date,
            "input_denngay": to_date,
        }

        status, resp_json = await self._request(
            "POST",
            self._evn_area.get("evn_data_url"),
            data=payload,
            headers=headers,
            api_name="HCMC Data",
        )

        if status != CONF_SUCCESS:
            return EVNUpdateResponse(status=status)

        if resp_json.get("state") != CONF_SUCCESS:
            return EVNUpdateResponse(
                status=CONF_ERR_INVALID_AUTH
                if resp_json.get("state") == "error_login"
                else resp_json.get("state"),
                data=resp_json,
            )

        data = resp_json.get("data", {}).get("sanluong_tungngay", [])
        if len(data) < 2:
            return EVNUpdateResponse(status=CONF_SUCCESS)

        f_date = _to_date(data[0].get("ngayFull"))
        t_date = _to_date(data[-2].get("ngayFull"))
        p_date = _to_date(data[-3].get("ngayFull")) if len(data) > 2 else None

        econ_total_old = safe_float(data[0].get("tong_p_giao"))
        econ_total_new = safe_float(data[-1].get("tong_p_giao"))

        record = EVNUpdateResponse(
            status=CONF_SUCCESS,
            econ_total_old=round(econ_total_old, 2),
            econ_total_new=round(econ_total_new, 2),
            econ_daily_new=round(safe_float(data[-2].get("Tong")), 2),
            econ_daily_old=round(
                safe_float(data[-3].get("Tong")) if len(data) > 2 else 0.0, 2
            ),
            econ_monthly_new=round(econ_total_new - econ_total_old, 2),
            from_date=f_date,
            to_date=t_date,
            previous_date=p_date,
            payment_needed=STATUS_N_PAYMENT_NEEDED,
            m_payment_needed=0,
        )

        p_status, p_json = await self._request(
            "POST",
            self._evn_area.get("evn_payment_url"),
            data={"input_makh": customer_id},
            headers=headers,
            api_name="HCMC Payment",
        )

        if p_status == CONF_SUCCESS and isinstance(p_json, dict):
            data_no = p_json.get("data", {})
            if data_no.get("isNo") == 1:
                record.payment_needed = STATUS_PAYMENT_NEEDED
                try:
                    record.m_payment_needed = int(
                        data_no.get("info_no", {})
                        .get("TONG_TIEN", "0")
                        .replace(".", "")
                    )
                except Exception:
                    pass

        return record

    async def fetch_daily_history(self, username, password, customer_id, start_date, end_date):
        start_date = _to_date(start_date)
        end_date = _to_date(end_date)

        if not start_date or not end_date:
            _LOGGER.error("HCMC: invalid date range")
            return []

        if not self._session_valid():
            if await self.login(username, password) != CONF_SUCCESS:
                return []

        headers = {"Cookie": f"evn_session={self._evn_area.get('evn_session')}"}
        status, resp_json = await self._request(
            "POST",
            "https://cskh.evnhcmc.vn/Tracuu/ajax_dienNangTieuThuTheoNgay",
            headers=headers,
            data={
                "input_makh": customer_id,
                "input_tungay": start_date.strftime("%d/%m/%Y"),
                "input_denngay": end_date.strftime("%d/%m/%Y"),
            },
            api_name="HCMC Daily History",
        )

        raw = resp_json.get("data", {}).get("sanluong_tungngay", []) if status == CONF_SUCCESS else []
        results = []

        for d in raw:
            dt = _to_date(d.get("ngayFull"))
            if not dt or not (start_date <= dt <= end_date):
                continue
            results.append(
                DailyHistoryRecord(
                    date=dt,
                    kwh=round(safe_float(d.get("Tong")), 3),
                )
            )

        return results

    async def fetch_monthly_history(self, username, password, customer_id, history_start_date):
        history_start_date = _to_date(history_start_date)
        if not history_start_date:
            return []

        if not self._session_valid():
            if await self.login(username, password) != CONF_SUCCESS:
                return []

        headers = {"Cookie": f"evn_session={self._evn_area.get('evn_session')}"}
        status, resp_json = await self._request(
            "POST",
            "https://cskh.evnhcmc.vn/Tracuu/ajax_dienNangTieuThuTheoKyHoaDon",
            headers=headers,
            data={"input_makh": customer_id},
            api_name="HCMC Monthly History",
        )

        raw = resp_json.get("data", {}).get("sanluong_hoadon", []) if status == CONF_SUCCESS else []
        results = []

        for b in raw:
            try:
                year = int(b.get("NAM"))
                month = int(b.get("THANG"))
                if (year, month) < (history_start_date.year, history_start_date.month):
                    continue
                results.append(
                    MonthlyBillRecord(
                        year=year,
                        month=month,
                        kwh=safe_float(b.get("SAN_LUONG")),
                        cost=int(safe_float(b.get("TONG_TIEN"))),
                    )
                )
            except Exception:
                continue

        return results
