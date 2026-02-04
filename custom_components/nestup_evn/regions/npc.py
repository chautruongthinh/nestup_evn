import logging
from datetime import timedelta, date
from dateutil import parser

from homeassistant.util import dt as dt_util

from ..utils import calc_ecost
from ..const import (
    CONF_SUCCESS,
    CONF_ERR_INVALID_AUTH,
    CONF_ERR_INVALID_ID,
    CONF_ERR_NO_MONITOR,
    STATUS_PAYMENT_NEEDED,
    STATUS_N_PAYMENT_NEEDED,
    STATUS_LOADSHEDDING,
)
from .base import EVNRegion
from ..types import EVNUpdateResponse, DailyHistoryRecord, MonthlyBillRecord

_LOGGER = logging.getLogger(__name__)

LOGIN_TTL = timedelta(minutes=10)


class NPCRegion(EVNRegion):
    def __init__(self, hass, session, evn_area):
        super().__init__(hass, session, evn_area)
        self._logged_in: bool = False
        self._last_login = None

    # ---------------------------------------------------------------------
    # Auth helpers
    # ---------------------------------------------------------------------
    def _need_login(self) -> bool:
        if not self._logged_in or not self._last_login:
            return True
        return (dt_util.utcnow() - self._last_login) > LOGIN_TTL

    async def login(self, username, password, customer_id) -> str:
        payload = {
            "username": username,
            "password": password,
            "deviceInfo": {
                "deviceId": f"ha-{customer_id}",
                "deviceType": "Android/HomeAssistant",
            },
        }
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
        }

        status, resp_json = await self._request(
            "POST",
            self._evn_area["evn_login_url"],
            json_data=payload,
            headers=headers,
            api_name="NPC Login",
        )

        if (
            status != CONF_SUCCESS
            or "data" not in resp_json
            or "accessToken" not in resp_json["data"]
        ):
            self._logged_in = False
            return CONF_ERR_INVALID_AUTH

        access_token = resp_json["data"]["accessToken"]
        ma_kh_login = resp_json["data"].get("data", {}).get("maKhang")
        self._evn_area["access_token"] = access_token

        # Switch customer if needed
        if ma_kh_login != customer_id:
            switch_url = f"https://cskh.evn.com.vn/cskh/v1/user/switch/{customer_id}"
            headers["authorization"] = f"Bearer {access_token}"

            status, switch_json = await self._request(
                "GET",
                switch_url,
                headers=headers,
                api_name="NPC Switch",
            )

            if (
                status != CONF_SUCCESS
                or not switch_json.get("data", {}).get("accessToken")
            ):
                self._logged_in = False
                return CONF_ERR_INVALID_ID

            self._evn_area["access_token"] = switch_json["data"]["accessToken"]

        self._logged_in = True
        self._last_login = dt_util.utcnow()
        return CONF_SUCCESS

    # ---------------------------------------------------------------------
    # Main realtime update
    # ---------------------------------------------------------------------
    async def request_update(
        self, username, password, customer_id, from_date, to_date
    ) -> EVNUpdateResponse:

        if self._need_login():
            if await self.login(username, password, customer_id) != CONF_SUCCESS:
                return EVNUpdateResponse(status=CONF_ERR_INVALID_AUTH)

        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {self._evn_area.get('access_token')}",
        }

        f_dt = parser.parse(from_date, dayfirst=True).date()
        t_dt = parser.parse(to_date, dayfirst=True).date() - timedelta(days=1)
        p_dt = f_dt - timedelta(days=1)

        payload = {
            "MA_DVIQLY": customer_id[:6],
            "MA_DDO": f"{customer_id}001",
            "TU_NGAY": p_dt.strftime("%d/%m/%Y"),
            "DEN_NGAY": t_dt.strftime("%d/%m/%Y"),
        }

        status, resp_json = await self._request(
            "POST",
            self._evn_area.get("evn_data_url"),
            json_data=payload,
            headers=headers,
            api_name="NPC Data",
        )

        data = resp_json.get("data", []) if status == CONF_SUCCESS else []
        if status != CONF_SUCCESS or not data or len(data) < 2:
            return EVNUpdateResponse(status=CONF_ERR_NO_MONITOR)

        t_new = round(float(data[0]["CHISO_MOI"]), 2)
        t_old = round(float(data[-1]["CHISO_MOI"]), 2)
        d_new = round(float(data[0]["CHISO_MOI"]) - float(data[1]["CHISO_MOI"]), 2)
        d_old = (
            round(float(data[1]["CHISO_MOI"]) - float(data[2]["CHISO_MOI"]), 2)
            if len(data) >= 3
            else 0.0
        )

        record_response = EVNUpdateResponse(
            status=CONF_SUCCESS,
            econ_total_old=t_old,
            econ_total_new=t_new,
            econ_daily_new=d_new,
            econ_daily_old=d_old,
            econ_monthly_new=round(t_new - t_old, 2),
            from_date=f_dt,
            to_date=t_dt,
            previous_date=p_dt,
            payment_needed=STATUS_N_PAYMENT_NEEDED,
            m_payment_needed=0,
        )

        # Payment status
        p_status, bill_json = await self._request(
            "POST",
            self._evn_area.get("evn_payment_url"),
            headers=headers,
            api_name="NPC Bill",
        )

        if p_status == CONF_SUCCESS and bill_json.get("data"):
            bill = bill_json["data"][0]
            if bill.get("TTRANG_TTOAN") == "CHUATT":
                record_response.payment_needed = STATUS_PAYMENT_NEEDED
                record_response.m_payment_needed = int(bill.get("TONG_TIEN", 0))

        # Load shedding
        try:
            status, shed = await self._request(
                "POST",
                self._evn_area.get("evn_loadshedding_url"),
                json_data={
                    "TU_NGAY": f_dt.strftime("%d/%m/%Y"),
                    "DEN_NGAY": t_dt.strftime("%d/%m/%Y"),
                },
                headers=headers,
                api_name="NPC Shed",
            )
            if status == CONF_SUCCESS and shed.get("data"):
                record_response.loadshedding = (
                    shed["data"][0].get("THOI_GIAN")
                    or shed["data"][0].get("NOI_DUNG")
                    or STATUS_LOADSHEDDING
                )
        except Exception:
            pass

        return record_response

    # ---------------------------------------------------------------------
    # Daily history
    # ---------------------------------------------------------------------
    async def fetch_daily_range(
        self,
        customer_id: str,
        from_date: date,
        to_date: date,
        endpoint: str = "diennangngay",
    ):
        headers = {
            "accept": "application/json, text/plain, */*",
            "authorization": f"Bearer {self._evn_area.get('access_token')}",
            "Content-Type": "application/json",
            "User-Agent": "okhttp/4.12.0",
            "Host": "apicskhevn.npc.com.vn",
            "Connection": "Keep-Alive",
        }

        url = f"https://apicskhevn.npc.com.vn/api/evn/tracuu/{endpoint}"
        ma_ddo = customer_id if len(customer_id) >= 16 else f"{customer_id}001"

        payload = {
            "MA_DVIQLY": customer_id[:6],
            "MA_DDO": ma_ddo,
            "TU_NGAY": from_date.strftime("%d/%m/%Y"),
            "DEN_NGAY": to_date.strftime("%d/%m/%Y"),
        }

        status, resp_json = await self._request(
            "POST",
            url,
            json_data=payload,
            headers=headers,
            use_ssl=False,
            api_name=f"NPC Daily ({endpoint})",
        )

        if status == CONF_SUCCESS:
            if isinstance(resp_json, list):
                return resp_json
            if isinstance(resp_json, dict):
                return resp_json.get("data", [])
        return []

    async def fetch_daily_history(
        self,
        username,
        password,
        customer_id: str,
        start_date: date,
        end_date: date,
    ) -> list[DailyHistoryRecord]:

        customer_id = customer_id.strip()
        _LOGGER.debug(
            "NPC: Fetching daily history for %s (%s to %s)",
            customer_id,
            start_date,
            end_date,
        )

        if self._need_login():
            if await self.login(username, password, customer_id) != CONF_SUCCESS:
                _LOGGER.error("NPC: Login failed during history fetch")
                return []

        results: list[DailyHistoryRecord] = []

        for endpoint in ["diennangngay", "chisongay"]:
            data = await self.fetch_daily_range(
                customer_id, start_date, end_date, endpoint
            )

            current_results: list[DailyHistoryRecord] = []

            if data and isinstance(data, list):
                for d in data:
                    try:
                        dt = parser.parse(d.get("NGAY"), dayfirst=True).date()
                        if start_date <= dt <= end_date:
                            kwh = float(d.get("DIEN_TTHU") or 0)
                            if kwh == 0 and "CS_MOI" in d and "CS_CU" in d:
                                kwh = float(d["CS_MOI"]) - float(d["CS_CU"])
                            current_results.append(
                                DailyHistoryRecord(date=dt, kwh=kwh)
                            )
                    except Exception:
                        continue

                if current_results:
                    results = current_results
                    if any(r.kwh > 0 for r in results):
                        break

        return results

    # ---------------------------------------------------------------------
    # Monthly history
    # ---------------------------------------------------------------------
    async def fetch_monthly_bills(
        self, customer_id: str, from_month, from_year, to_month, to_year
    ):
        headers = {
            "authorization": f"Bearer {self._evn_area.get('access_token')}"
        }
        payload = {
            "MA_DVIQLY": customer_id[:6],
            "MA_DDO": f"{customer_id}001",
            "TU_THANG_NAM": f"{from_month:02d}/{from_year}",
            "DEN_THANG_NAM": f"{to_month:02d}/{to_year}",
        }

        status, resp_json = await self._request(
            "POST",
            "https://apicskhevn.npc.com.vn/api/evn/tracuu/diennangthang",
            json_data=payload,
            headers=headers,
            api_name="NPC Monthly Bills",
        )

        return resp_json.get("data", []) if status == CONF_SUCCESS else []

    async def fetch_monthly_history(
        self,
        username,
        password,
        customer_id: str,
        history_start_date: date,
    ) -> list[MonthlyBillRecord]:

        if self._need_login():
            if await self.login(username, password, customer_id) != CONF_SUCCESS:
                _LOGGER.error("NPC: Login failed during history fetch")
                return []

        raw = await self.fetch_monthly_bills(
            customer_id,
            history_start_date.month,
            history_start_date.year,
            date.today().month,
            date.today().year,
        )

        results: list[MonthlyBillRecord] = []

        for b in raw:
            try:
                year = int(b.get("NAM"))
                month = int(b.get("THANG"))
                kwh = float(b.get("DIEN_TTHU"))
                results.append(
                    MonthlyBillRecord(
                        month=month,
                        year=year,
                        kwh=kwh,
                        cost=calc_ecost(kwh),
                    )
                )
            except Exception:
                continue

        return results
