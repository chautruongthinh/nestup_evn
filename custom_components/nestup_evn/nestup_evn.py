"""Setup and manage the EVN API."""

import base64
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
import gzip
import logging
import os
import ssl
import time
from typing import Any

from dateutil import parser

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)

from .data_storage import EVNDataStorage
from .const import CONF_HISTORY_START_DATE
from .utils import calc_ecost

from .const import (
    CONF_EMPTY,
    CONF_ERR_CANNOT_CONNECT,
    CONF_ERR_INVALID_AUTH,
    CONF_ERR_INVALID_ID,
    CONF_ERR_NO_MONITOR,
    CONF_ERR_NOT_SUPPORTED,
    CONF_ERR_UNKNOWN,
    CONF_SUCCESS,
    ID_ECON_DAILY_NEW,
    ID_ECON_DAILY_OLD,
    ID_ECON_MONTHLY_NEW,
    ID_ECON_TOTAL_NEW,
    ID_ECON_TOTAL_OLD,
    ID_ECOST_DAILY_NEW,
    ID_ECOST_DAILY_OLD,
    ID_ECOST_MONTHLY_NEW,
    ID_FROM_DATE,
    ID_LATEST_UPDATE,
    ID_M_PAYMENT_NEEDED,
    ID_PAYMENT_NEEDED,
    ID_LOADSHEDDING,    
    ID_TO_DATE,
    STATUS_N_PAYMENT_NEEDED,
    STATUS_PAYMENT_NEEDED,
    STATUS_LOADSHEDDING,    
    VIETNAM_ECOST_STAGES,
    VIETNAM_ECOST_VAT,
)
from .types import EVN_NAME, VIETNAM_EVN_AREA, Area

_LOGGER = logging.getLogger(__name__)

def create_ssl_context():
    """Create SSL context with cipher settings"""
    context = ssl.create_default_context()
    context.set_ciphers("ALL:@SECLEVEL=1")
    return context

def read_evn_branches_file(file_path):
    """Read EVN branches file synchronously"""
    with open(file_path) as f:
        return json.load(f)

class EVNAPI:
    def __init__(self, hass: HomeAssistant, is_new_session=False):
        """Construct EVNAPI wrapper."""
        self.hass = hass  # Store hass instance
        self._session = (
            async_create_clientsession(hass)
            if is_new_session
            else async_get_clientsession(hass)
        )
        self._evn_area = {}

    async def login(self, evn_area, username, password, customer_id) -> str:
        """Try login into EVN corresponding with different EVN areas"""

        self._evn_area = evn_area

        if (username is None) or (password is None):
            return CONF_ERR_UNKNOWN

        if evn_area.get("name") == EVN_NAME.HCMC:
            return await self.login_evnhcmc(username, password)

        elif evn_area.get("name") == EVN_NAME.HANOI:
            return await self.login_evnhanoi(username, password)

        elif evn_area.get("name") == EVN_NAME.CPC:
            return await self.login_evncpc(username, password)

        elif evn_area.get("name") == EVN_NAME.SPC:
            return await self.login_evnspc(username, password, customer_id)

        elif evn_area.get("name") == EVN_NAME.NPC:
            return await self.login_evnnpc(username, password, customer_id)

        return CONF_ERR_UNKNOWN

    async def request_update(
        self, evn_area: Area, username, password, customer_id, monthly_start=None
    ) -> dict[str, Any]:
        """Request new update from EVN Server, corresponding with the last session"""

        self._evn_area = evn_area

        fetch_data = {}        
        
        from_date, to_date = generate_datetime(1 if evn_area.get("name") == EVN_NAME.CPC else monthly_start, offset=1)

        if evn_area.get("name") == EVN_NAME.CPC:
            fetch_data = await self.request_update_evncpc(customer_id)
            
        elif evn_area.get("name") == EVN_NAME.HANOI:            
            fetch_data = await self.request_update_evnhanoi(
                username, password, customer_id, from_date, to_date
            )

        elif evn_area.get("name") == EVN_NAME.SPC:
            fetch_data = await self.request_update_evnspc(
                customer_id, from_date, to_date
            )

        elif evn_area.get("name") == EVN_NAME.NPC:            
            login_status = await self.login_evnnpc(username, password, customer_id)
            if login_status != CONF_SUCCESS:
                return {"status": login_status}
                
            fetch_data = await self.request_update_evnnpc(
                customer_id, from_date, to_date
            )

        elif evn_area.get("name") == EVN_NAME.HCMC:
            fetch_data = await self.request_update_evnhcmc(
                username, password, customer_id, from_date, to_date
            )

        if fetch_data["status"] == CONF_SUCCESS:
            return formatted_result(fetch_data)

        return fetch_data

    async def login_evnhanoi(self, username, password) -> str:
        """Create EVN login session corresponding with EVNHANOI Endpoint"""

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Connection": "keep-alive",
        }

        payload = {
            "username": username,
            "password": password,
            "client_id": "httplocalhost4500",
            "client_secret": "secret",
            "grant_type": "password",
        }

        resp = await self._session.post(
            url=self._evn_area.get("evn_login_url"), data=payload, headers=headers
        )

        status, resp_json = await json_processing(resp)
        if status != CONF_SUCCESS:
            return status

        if ("error" in resp_json) and (resp_json["error"] == "invalid_grant"):
            return CONF_ERR_INVALID_AUTH

        elif "access_token" in resp_json:
            self._evn_area["access_token"] = resp_json["access_token"]
            if "expires_in" in resp_json:
                expires_in = resp_json["expires_in"]
                self._evn_area["token_expiry"] = time.time() + expires_in
            return CONF_SUCCESS

        _LOGGER.error(f"Error while logging in EVN Endpoints: {resp_json}")
        return CONF_ERR_UNKNOWN

    async def login_evnhcmc(self, username, password) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }

        payload = {"u": username, "p": password}

        ssl_context = await self.hass.async_add_executor_job(create_ssl_context)

        resp = await self._session.post(
            self._evn_area.get("evn_login_url"),
            data=payload,
            headers=headers,
            ssl=ssl_context,
        )

        status, resp_json = await json_processing(resp)
        if status != CONF_SUCCESS or not isinstance(resp_json, dict):
            return CONF_ERR_INVALID_AUTH

        if resp_json.get("state") not in ("success", "login"):
            return CONF_ERR_INVALID_AUTH

        jar = self._session.cookie_jar
        cookies = jar.filter_cookies("https://cskh.evnhcmc.vn")

        evn_cookie = cookies.get("evn_session")
        if not evn_cookie:
            _LOGGER.error("EVNHCMC login success but evn_session not found")
            return CONF_ERR_INVALID_AUTH

        self._evn_area["evn_session"] = evn_cookie.value

        if evn_cookie["expires"]:
            self._evn_area["expires"] = parser.parse(
                evn_cookie["expires"]
            ).astimezone(timezone.utc)

        _LOGGER.info("EVNHCMC login OK, session=%s", evn_cookie.value)
        return CONF_SUCCESS

    async def login_evnnpc(self, username, password, customer_id) -> str:
        payload = {
            "username": username,
            "password": password,
            "deviceInfo": {
                "deviceId": f"ha-{customer_id}",
                "deviceType": "Android/HomeAssistant",
            },
        }

        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "user-agent": "okhttp/4.12.0",
			"connection": "Keep-Alive",
        }

        resp = await self._session.post(
            self._evn_area["evn_login_url"],
            json=payload,
            headers=headers,
        )

        status, resp_json = await json_processing(resp)
        if status != CONF_SUCCESS:
            return status

        if "data" not in resp_json or "accessToken" not in resp_json["data"]:
            return CONF_ERR_INVALID_AUTH
            
        data = resp_json.get("data", {})
        access_token = data.get("accessToken")
        user_data = data.get("data", {})

        ma_kh_login = user_data.get("maKhang")
        self._evn_area["access_token"] = access_token

        if ma_kh_login != customer_id:
            switch_url = (
                f"https://cskh.evn.com.vn/cskh/v1/user/switch/{customer_id}"
            )

            switch_headers = {
                "accept": "application/json, text/plain, */*",
                "accept-encoding": "gzip",
                "connection": "Keep-Alive",
                "user-agent": "okhttp/4.12.0",
                "authorization": f"Bearer {access_token}",
            }

            resp = await self._session.get(switch_url, headers=switch_headers)
            status, switch_json = await json_processing(resp)

            if status != CONF_SUCCESS:
                return CONF_ERR_INVALID_ID

            switch_data = switch_json.get("data", {})
            new_token = switch_data.get("accessToken")

            if not new_token:
                return CONF_ERR_INVALID_ID

            self._evn_area["access_token"] = new_token

        return CONF_SUCCESS

    async def login_evncpc(self, username, password) -> str:
        """Create EVN login session corresponding with EVNCPC Endpoint"""

        payload = {
            "username": username,
            "password": password,
            "scope": "CSKH offline_access",
            "grant_type": "password",
        }

        basic_auth = "CSKH_Mobile_Notification:Evncpc@CC2023!Annv1609#"
        auth_header = base64.b64encode(basic_auth.encode()).decode()

        headers = {
            "Authorization": f"Basic {auth_header}",            
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "okhttp/4.9.2",
        }

        resp = await self._session.post(
            self._evn_area["evn_login_url"], data=payload, headers=headers
        )

        try:
            resp_json = await resp.json()
        except Exception:
            return CONF_ERR_INVALID_AUTH

        if resp_json.get("access_token"):
            self._evn_area["access_token"] = resp_json["access_token"]
            return CONF_SUCCESS

        _LOGGER.error(f"Error while logging in EVN Endpoints: {resp_json}")
        return CONF_ERR_UNKNOWN

    async def login_evnspc(self, username, password, customer_id) -> str:
        """Create EVN login session corresponding with EVNSPC Endpoint"""

        payload = {
            "strUsername": username,
            "strPassword": password,
            "strDeviceID": customer_id,
        }

        headers = {
            "User-Agent": "evnapp/59 CFNetwork/1240.0.4 Darwin/20.6.0",
            "Accept-Language": "vi-vn",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Content-Type": "application/json; charset=utf-8",
        }

        resp = await self._session.post(
            url=self._evn_area.get("evn_login_url"),
            data=json.dumps(payload),
            headers=headers,
            ssl=False,
        )

        status, resp_json = await json_processing(resp)

        if status != CONF_SUCCESS:
            return status

        if not ("maKH" in resp_json and "token" in resp_json):
            return CONF_ERR_UNKNOWN

        if resp_json["maKH"] == "":
            return CONF_ERR_INVALID_AUTH

        self._evn_area["access_token"] = resp_json["token"]
        return CONF_SUCCESS


    ##########################
    #       EVN HANOI          #
    ##########################
    async def request_update_evnhanoi(
        self, username, password, customer_id, from_date, to_date, last_index="001"
    ):
        """Request new update from EVNHANOI Server"""

        if self.is_token_expired():
            login_status = await self.login_evnhanoi(username, password)
            if login_status != CONF_SUCCESS:
                raise ConfigEntryNotReady("Token expired and failed to reauthenticate")
                    
        headers = {
            "Authorization": f"Bearer {self._evn_area.get('access_token')}",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        }

        data = {
            "maDiemDo": f"{customer_id}{last_index}",
            "maDonVi": f"{customer_id[0:6]}",
            "maXacThuc": "EVNHN",
            "ngayDau": from_date,
            "ngayCuoi": to_date,
        }

        ssl_context = await self.hass.async_add_executor_job(ssl.create_default_context)

        resp = await self._session.post(
            url=self._evn_area.get("evn_data_url"),
            data=json.dumps(data),
            headers=headers,
            ssl=ssl_context,
        )

        status, resp_json = await json_processing(resp)

        if status != CONF_SUCCESS:
            return resp_json

        if resp_json.get("isError"):

            if resp_json.get("code") == 400:

                if last_index == "001":
                    return await self.request_update_evnhanoi(
                        username, password, customer_id, from_date, to_date, last_index="1"
                    )

                return {"status": CONF_ERR_INVALID_ID, "data": resp_json}

            _LOGGER.error(f"Cannot request new data from EVN Server: {resp_json}")

            return {"status": resp_json.get("code"), "data": resp_json}

        sub_data = resp_json["data"]["chiSoNgay"]

        from_date = parser.parse(sub_data[0]["ngay"], dayfirst=True)
        to_date = parser.parse(
            sub_data[(-1 if len(sub_data) > 1 else 0)]["ngay"], dayfirst=True
        ) - timedelta(days=1)
        previous_date = parser.parse(
            sub_data[(-2 if len(sub_data) > 2 else 0)]["ngay"], dayfirst=True
        ) - timedelta(days=1)

        econ_total_new = round(
            float(str(sub_data[(-1 if len(sub_data) > 1 else 0)]["sg"])), 2
        )
        econ_total_old = round(float(str(sub_data[0]["sg"])), 2)

        econ_daily_new = round(
            float(sub_data[(-1 if len(sub_data) > 1 else 0)]["sg"])
            - float(sub_data[(-2 if len(sub_data) > 2 else 0)]["sg"]),
            2,
        )
        econ_daily_old = round(
            float(sub_data[(-2 if len(sub_data) > 2 else 0)]["sg"])
            - float(sub_data[(-3 if len(sub_data) > 3 else 0)]["sg"]),
            2,
        )

        fetched_data = {
            "status": CONF_SUCCESS,
            ID_ECON_TOTAL_OLD: econ_total_old,
            ID_ECON_TOTAL_NEW: econ_total_new,
            ID_ECON_DAILY_OLD: econ_daily_old,
            ID_ECON_DAILY_NEW: econ_daily_new,
            ID_ECON_MONTHLY_NEW: round(econ_total_new - econ_total_old, 2),
            "to_date": to_date.date(),
            "from_date": from_date.date(),
            "previous_date": previous_date.date(),
        }

        data = {
            "maKhachHang": customer_id,
            "maDonViQuanLy": f"{customer_id[0:6]}",
        }

        resp = await self._session.post(
            url=self._evn_area.get("evn_payment_url"),
            data=json.dumps(data),
            headers=headers,
            ssl=ssl_context,
        )
        status, resp_json = await json_processing(resp)

        payment_status = CONF_ERR_UNKNOWN
        m_payment_status = 0

        if status == CONF_SUCCESS and not resp_json["isError"]:
            if len(resp_json["data"]["listThongTinNoKhachHangVm"]):
                payment_status = STATUS_PAYMENT_NEEDED
                m_payment_status = int(
                    resp_json["data"]["listThongTinNoKhachHangVm"][0][
                        "tongTien"
                    ].replace(".", "")
                )
            else:
                payment_status = STATUS_N_PAYMENT_NEEDED

        fetched_data.update(
            {ID_PAYMENT_NEEDED: payment_status, ID_M_PAYMENT_NEEDED: m_payment_status}
        )

        return fetched_data

    def is_token_expired(self) -> bool:
        expiry_time = self._evn_area.get("token_expiry", 0)
        return time.time() > expiry_time

    async def fetch_evnhanoi_contract(self, customer_id: str):
        if hasattr(self, "_evnhanoi_contract") and self._evnhanoi_contract:
            return self._evnhanoi_contract

        resp = await self._session.get(
            "https://evnhanoi.vn/api/TraCuu/GetDanhSachHopDongByUserName",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._evn_area.get('access_token')}",
                "User-Agent": "Mozilla/5.0",
                "Accept-Encoding": "gzip, deflate, br",
            },
        )

        status, data = await json_processing(resp)
        if status != CONF_SUCCESS or not isinstance(data, dict):
            raise UpdateFailed("EVN HANOI: Không lấy được danh sách hợp đồng")

        contracts = data.get("data", {}).get("thongTinHopDongDtos", [])
        for c in contracts:
            if c.get("maKhachHang") == customer_id:
                self._evnhanoi_contract = c
                return c

        raise UpdateFailed(
            f"EVN HANOI: customer_id {customer_id} không khớp hợp đồng"
        )

    async def fetch_daily_range_evnhanoi(
        self,
        customer_id: str,
        start: date,
        end: date,
    ):
        contract = await self.fetch_evnhanoi_contract(customer_id)

        payload = {
            "maDonVi": contract["maDonViQuanLy"],
            "maDiemDo": f"{contract['maKhachHang']}001",
            "maXacThuc": "EVNHN",
            "ngayDau": start.strftime("%d/%m/%Y"),
            "ngayCuoi": end.strftime("%d/%m/%Y"),
        }

        resp = await self._session.post(
            "https://evnhanoi.vn/api/TraCuu/LayChiSoDoXaPharse2",
            json=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._evn_area.get('access_token')}",
                "Origin": "https://evnhanoi.vn",
                "Referer": "https://evnhanoi.vn/search/tracuu-chiso-congto",
                "User-Agent": "Mozilla/5.0",
                "Accept-Encoding": "gzip",
            },
        )
    
        status, data = await json_processing(resp)

        if status != CONF_SUCCESS or not isinstance(data, dict):
            return []

        result = data.get("data")
        if not isinstance(result, dict):
            return []

        return result.get("chiSoNgayFull", [])

    async def fetch_monthly_bills_evnhanoi(
        self,
        customer_id: str,
    ):
        today = date.today()
   
        contract = await self.fetch_evnhanoi_contract(customer_id)

        resp = await self._session.get(
            "https://evnhanoi.vn/api/TraCuu/GetLichSuThanhToan",
            params={
                "maDvQly": contract["maDonViQuanLy"],
                "maKh": customer_id,
                "thang": today.month,
                "nam": today.year,
            },
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._evn_area.get('access_token')}",
                "Accept-Encoding": "gzip, deflate, br",
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://evnhanoi.vn/dashboard/home/quan-ly-hoa-don/lich-su-thanh-toan",
            },
        )

        status, data = await json_processing(resp)
        if status != CONF_SUCCESS or not isinstance(data, dict):
            return []

        return data.get("data", {}).get("dmLichSuThanhToanList", [])

    ##########################
    #       EVN HCMC          #
    ##########################
    async def request_update_evnhcmc(self, username, password, customer_id, from_date, to_date):
        """Request new update from EVNHCMC Server"""

        evn_session_expires = self._evn_area.get("expires")
        if isinstance(evn_session_expires, datetime):
            evn_session_expires = evn_session_expires.astimezone(timezone.utc)
        else:    
            evn_session_expires = None

        if not evn_session_expires:
            login_status = await self.login_evnhcmc(username, password)
            if login_status != CONF_SUCCESS:
                raise ConfigEntryNotReady("Failed to reauthenticate due to invalid session expiration.")
        elif datetime.now(tz=timezone.utc) >= evn_session_expires:
            _LOGGER.info("Session expired. Attempting to login again...")
            login_status = await self.login_evnhcmc(username, password)
            if login_status != CONF_SUCCESS:
                raise ConfigEntryNotReady("Session expired and failed to reauthenticate")       

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cookie": f"evn_session={self._evn_area.get('evn_session')}",
        }

        ssl_context = await self.hass.async_add_executor_job(ssl.create_default_context)

        resp = await self._session.post(
            url=self._evn_area.get("evn_data_url"),
            data={
                "input_makh": customer_id,
                "input_tungay": from_date,
                "input_denngay": to_date,
            },
            ssl=ssl_context,
            headers=headers,
        )
        status, resp_json = await json_processing(resp)

        if status != CONF_SUCCESS:
            return resp_json

        state = resp_json["state"]

        if state != CONF_SUCCESS:
            if state == "error_login":
                return {"status": CONF_ERR_INVALID_AUTH, "data": resp.status}

            _LOGGER.error(
                f"Cannot request new data from EVN Server for customer ID: {customer_id}\n{resp_json}"
            )
            return {"status": state, "data": resp_json}

        resp_json = resp_json["data"]["sanluong_tungngay"]

        from_date = strip_date_range(resp_json[0]["ngayFull"])
        to_date = strip_date_range(
            resp_json[(-2 if len(resp_json) > 2 else 0)]["ngayFull"]
        )
        previous_date = strip_date_range(
            resp_json[(-3 if len(resp_json) > 3 else 0)]["ngayFull"]
        )

        econ_total_new = round(
            float(
                str(
                    resp_json[(-1 if len(resp_json) > 1 else 0)]["tong_p_giao"]
                ).replace(",", "")
            ),
            2,
        )
        econ_total_old = round(
            float(str(resp_json[0]["tong_p_giao"]).replace(",", "")), 2
        )

        fetched_data = {
            "status": CONF_SUCCESS,
            ID_ECON_TOTAL_OLD: econ_total_old,
            ID_ECON_TOTAL_NEW: econ_total_new,
            ID_ECON_DAILY_NEW: round(
                float(
                    str(resp_json[(-2 if len(resp_json) > 2 else 0)]["Tong"]).replace(
                        ",", ""
                    )
                ),
                2,
            ),
            ID_ECON_DAILY_OLD: round(
                float(
                    str(resp_json[(-3 if len(resp_json) > 3 else 0)]["Tong"]).replace(
                        ",", ""
                    )
                ),
                2,
            ),
            ID_ECON_MONTHLY_NEW: round(econ_total_new - econ_total_old, 2),
            "to_date": to_date.date(),
            "from_date": from_date.date(),
            "previous_date": previous_date.date(),
        }

        resp = await self._session.post(
            url=self._evn_area.get("evn_payment_url"),
            data={"input_makh": customer_id},
            ssl=ssl_context,
            headers=headers,
        )
        status, resp_json = await json_processing(resp)

        payment_status = CONF_ERR_UNKNOWN
        m_payment_status = 0

        if status == CONF_SUCCESS:
            if "isNo" in resp_json["data"]:
                if resp_json["data"].get("isNo") == 1:
                    payment_status = STATUS_PAYMENT_NEEDED

                    if "info_no" in resp_json["data"]:
                        m_payment_status = int(
                            resp_json["data"]["info_no"]
                            .get("TONG_TIEN")
                            .replace(".", "")
                        )

                elif resp_json["data"].get("isNo") == 0:
                    payment_status = STATUS_N_PAYMENT_NEEDED

        fetched_data.update(
            {ID_PAYMENT_NEEDED: payment_status, ID_M_PAYMENT_NEEDED: m_payment_status}
        )

        return fetched_data

    async def fetch_daily_range_evnhcmc(
        self,
        customer_id: str,
        start_date: str,
        end_date: str,
    ):
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Cookie": f"evn_session={self._evn_area.get('evn_session')}",
        }

        payload = {
            "input_makh": customer_id,
            "input_tungay": start_date,  # dd/mm/YYYY
            "input_denngay": end_date,
        }

        resp = await self._session.post(
            "https://cskh.evnhcmc.vn/Tracuu/ajax_dienNangTieuThuTheoNgay",
            headers=headers,
            data=payload,
        )

        status, resp_json = await json_processing(resp)
        if status != CONF_SUCCESS:
            return []

        return (
            resp_json
            .get("data", {})
            .get("sanluong_tungngay", [])
        )

    async def fetch_monthly_bills_evnhcmc(self, customer_id: str):
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Accept-Encoding": "gzip, deflate, br",
            "Cookie": f"evn_session={self._evn_area.get('evn_session')}",
        }

        payload = {
            "input_makh": customer_id
        }

        resp = await self._session.post(
            "https://www.evnhcmc.vn/Tracuu/ajax_dienNangTieuThuTheoKyHoaDon",
            headers=headers,
            data=payload,
        )

        status, resp_json = await json_processing(resp)
        if status != CONF_SUCCESS:
            return []

        return resp_json.get("data", {}).get("sanluong_hoadon", [])


    ##########################
    #       EVN NPC          #
    ##########################
    async def request_update_evnnpc(self, customer_id, from_date, to_date):
        """Request new update from EVNNPC Server"""

        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "user-agent": "okhttp/4.12.0",
            "authorization": f"Bearer {self._evn_area.get('access_token')}",
        }

        from_date_dt = parser.parse(from_date, dayfirst=True).date()
        to_date_dt = parser.parse(to_date, dayfirst=True).date() - timedelta(days=1)
        previous_date_dt = from_date_dt - timedelta(days=1)

        payload = {
            "MA_DVIQLY": customer_id[:6],
            "MA_DDO": f"{customer_id}001",
            "TU_NGAY": previous_date_dt.strftime("%d/%m/%Y"),
            "DEN_NGAY": to_date_dt.strftime("%d/%m/%Y"),
        }

        resp = await self._session.post(
            self._evn_area.get("evn_data_url"),
            json=payload,
            headers=headers,
        )

        status, resp_json = await json_processing(resp)
        if status != CONF_SUCCESS or not resp_json.get("data"):
            return {"status": CONF_ERR_NO_MONITOR}

        data = resp_json["data"]

        if len(data) < 2:
            return {"status": CONF_ERR_NO_MONITOR}

        record_last = data[0]
        record_first = data[-1]

        total_new = round(float(record_last["CHISO_MOI"]), 2)
        total_old = round(float(record_first["CHISO_MOI"]), 2)

        if len(data) >= 2:
            daily_new = round(
                float(data[0]["CHISO_MOI"]) - float(data[1]["CHISO_MOI"]), 2
            )
        else:
            daily_new = 0.0

        if len(data) >= 3:
            daily_old = round(
                float(data[1]["CHISO_MOI"]) - float(data[2]["CHISO_MOI"]), 2
            )
        else:
            daily_old = 0.0

        monthly_new = round(total_new - total_old, 2)

        fetched_data = {
            "status": CONF_SUCCESS,
            ID_ECON_TOTAL_OLD: total_old,
            ID_ECON_TOTAL_NEW: total_new,
            ID_ECON_DAILY_NEW: daily_new,
            ID_ECON_DAILY_OLD: daily_old,
            ID_ECON_MONTHLY_NEW: monthly_new,
            "from_date": from_date_dt,
            "to_date": to_date_dt,
            "previous_date": previous_date_dt,
        }

        resp = await self._session.post(
            self._evn_area.get("evn_payment_url"),
            headers=headers,
        )

        status, bill_json = await json_processing(resp)

        if status == CONF_SUCCESS and bill_json.get("data"):
            bill = bill_json["data"][0]
            if bill.get("TTRANG_TTOAN") == "CHUATT":
                fetched_data.update({
                    ID_PAYMENT_NEEDED: STATUS_PAYMENT_NEEDED,
                    ID_M_PAYMENT_NEEDED: int(bill.get("TONG_TIEN", 0)),
                })
            else:
                fetched_data.update({
                    ID_PAYMENT_NEEDED: STATUS_N_PAYMENT_NEEDED,
                    ID_M_PAYMENT_NEEDED: 0,
                })
        else:
            fetched_data.update({
                ID_PAYMENT_NEEDED: CONF_ERR_UNKNOWN,
                ID_M_PAYMENT_NEEDED: 0,
            })

        try:
            payload = {
                "TU_NGAY": from_date_dt.strftime("%d/%m/%Y"),
                "DEN_NGAY": to_date_dt.strftime("%d/%m/%Y"),
            }

            resp = await self._session.post(
                self._evn_area.get("evn_loadshedding_url"),
                json=payload,
                headers=headers,
            )

            status, shed_json = await json_processing(resp)

            if status == CONF_SUCCESS and shed_json.get("data"):
                shed = shed_json["data"][0]
                fetched_data[ID_LOADSHEDDING] = (
                    shed.get("THOI_GIAN")
                    or shed.get("NOI_DUNG")
                    or STATUS_LOADSHEDDING
                )
            else:
                fetched_data[ID_LOADSHEDDING] = (
                    STATUS_LOADSHEDDING if status == CONF_EMPTY else CONF_ERR_UNKNOWN
                )

        except Exception:
            fetched_data[ID_LOADSHEDDING] = CONF_ERR_UNKNOWN
            
        return fetched_data
 
    async def fetch_daily_range_evnnpc(
        self,
        customer_id: str,
        from_date: date,
        to_date: date,
    ):
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "user-agent": "okhttp/4.12.0",
            "authorization": f"Bearer {self._evn_area.get('access_token')}",
        }

        payload = {
            "MA_DVIQLY": customer_id[:6],
            "MA_DDO": f"{customer_id}001",
            "TU_NGAY": from_date.strftime("%d/%m/%Y"),
            "DEN_NGAY": to_date.strftime("%d/%m/%Y"),
        }

        resp = await self._session.post(
            "https://apicskhevn.npc.com.vn/api/evn/tracuu/diennangngay",
            json=payload,
            headers=headers,
        )

        status, resp_json = await json_processing(resp)
        if status != CONF_SUCCESS:
            return []

        return resp_json.get("data", [])

    async def fetch_monthly_bills_evnnpc(
        self,
        customer_id: str,
        from_month: int,
        from_year: int,
        to_month: int,
        to_year: int,
    ):
        headers = {
            "accept": "application/json, text/plain, */*",
            "user-agent": "okhttp/4.12.0",
            "authorization": f"Bearer {self._evn_area.get('access_token')}",
            "content-type": "application/json",
        }

        payload = {
            "MA_DVIQLY": customer_id[:6],
            "MA_DDO": f"{customer_id}001",
            "TU_THANG_NAM": f"{from_month:02d}/{from_year}",
            "DEN_THANG_NAM": f"{to_month:02d}/{to_year}",
        }

        resp = await self._session.post(
            "https://apicskhevn.npc.com.vn/api/evn/tracuu/diennangthang",
            json=payload,
            headers=headers,
        )

        status, resp_json = await json_processing(resp)
        if status != CONF_SUCCESS:
            return []

        return resp_json.get("data", [])

    ##########################
    #       EVN CPC          #
    ##########################
    async def request_update_evncpc(self, customer_id):
        """Request new update from EVNCPC Server"""

        headers = {
            "Authorization": f"Bearer {self._evn_area.get('access_token')}",
            "User-Agent": "okhttp/4.9.2",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

        resp = await self._session.get(
            url=f"{self._evn_area.get('evn_data_url')}{customer_id}",
            headers=headers,
        )

        _, resp_json = await json_processing(resp)

        electric = (
            resp_json.get("electricConsumption")
            if isinstance(resp_json, dict)
            else None
        )

        if not electric or not isinstance(electric, dict):
            return {
                "status": CONF_ERR_NO_MONITOR,
                "raw": resp_json,
            }
            
        fetched_data = {
            "status": CONF_SUCCESS,
            ID_ECON_DAILY_NEW: round(
                float(electric.get("electricConsumptionToday", 0)), 2
            ),
            ID_ECON_DAILY_OLD: round(
                float(electric.get("electricConsumptionYesterday", 0)), 2
            ),
            ID_ECON_MONTHLY_NEW: round(
                float(electric.get("electricConsumptionThisMonth", 0)), 2
            ),
        }

        resp = await self._session.get(
            url=f"{self._evn_area.get('evn_payment_url')}{customer_id}",
            headers=headers,
        )

        _, resp_json = await json_processing(resp)

        response = (
            resp_json.get("response")
            if isinstance(resp_json, dict)
            else None
        )

        if not response or not isinstance(response, dict):
            return {
                "status": CONF_ERR_NO_MONITOR,
                "raw": resp_json,
            }

        payment_status = STATUS_PAYMENT_NEEDED
        m_payment_status = 0

        if response.get("tinhTrangThanhToan") == "Đã thanh toán":
            payment_status = STATUS_N_PAYMENT_NEEDED
        else:
            try:
                m_payment_status = int(
                    response.get("tienHoaDon", "0")
                    .replace(".", "")
                    .replace("đ", "")
                )
            except Exception:
                m_payment_status = 0

        current_einfo = response.get("dienNangHienTai", {})

        try:
            to_date = datetime.strptime(
                current_einfo.get("thoiDiem"), "%Hh%M - %d/%m/%Y"
            )
        except Exception:
            to_date = datetime.now()

        fetched_data.update(
            {
                ID_PAYMENT_NEEDED: payment_status,
                ID_M_PAYMENT_NEEDED: m_payment_status,
                ID_ECON_TOTAL_NEW: round(float(current_einfo.get("chiSo", "0").replace(".", "").replace(",", ".")), 2),
                ID_ECON_TOTAL_OLD: round(float(response.get("chiSoCuoiKy", "0").replace(".", "").replace(",", ".")), 2),
                "to_date": to_date,
                "previous_date": to_date - timedelta(days=1),
            }
        )

        return fetched_data

    async def fetch_daily_range_evncpc(self, customer_id: str):
        headers = {
            "Authorization": f"Bearer {self._evn_area.get('access_token')}",
        }

        resp = await self._session.get(
            "https://cskh-api.cpc.vn/api/remote/meter/rf/sl-tieu-thu-view",
            params={
                "customerCode": customer_id,
                "orgCode": customer_id[:6],
            },
            headers=headers,
        )

        status, resp_json = await json_processing(resp)
        if status != CONF_SUCCESS:
            return []

        return resp_json

    async def fetch_monthly_bills_evncpc(self, customer_id: str):

        url = "https://cskh-api.cpc.vn/api/remote/thongTinHoaDonSpider"
        params = {
            "customerCode": customer_id,
            "maDonViQuanLy": customer_id[:6],
        }

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._evn_area.get('access_token')}",
            "Origin": "https://cskh.cpc.vn",
            "Referer": "https://cskh.cpc.vn/",
            "User-Agent": "Mozilla/5.0",
            "Accept-Encoding": "gzip, deflate, br",
        }

        resp = await self._session.get(url, params=params, headers=headers)

        # ---- HTTP status check ----
        if resp.status != 200:
            _LOGGER.error(
                "EVN CPC HTTP error %s while fetching monthly bills",
                resp.status,
            )
            return []

        # ---- read raw text first (CPC hay trả HTML khi token lỗi) ----
        try:
            raw_text = await resp.text()
        except Exception as e:
            _LOGGER.error("EVN CPC read response error: %s", e)
            return []

        # ---- parse JSON ----
        try:
            payload = json.loads(raw_text)
        except Exception:
            _LOGGER.error(
                "EVN CPC response is not JSON, first 500 chars:\n%s",
                raw_text[:500],
            )
            return []

        bills = payload.get("result")
        if not isinstance(bills, list):
            _LOGGER.error(
                "EVN CPC unexpected response format: %s",
                payload,
            )
            return []

        _LOGGER.info(
            "EVN CPC fetched %d monthly bills (raw)",
            len(bills),
        )

        return bills


    async def request_update_evnspc(
        self, customer_id, from_date, to_date, last_index="001"
    ):
        """Request new update from EVNSPC Server"""

        from_date_str = (parser.parse(from_date, dayfirst=True) - timedelta(days=1)).strftime("%Y%m%d")
        to_date_str = parser.parse(to_date, dayfirst=True).strftime("%Y%m%d")

        headers = {
            "User-Agent": "evnapp/59 CFNetwork/1240.0.4 Darwin/20.6.0",
            "Authorization": f"Bearer {self._evn_area.get('access_token')}",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "vi-vn",
            "Connection": "keep-alive",
        }

        status, resp_json = await fetch_with_retries(
            url=self._evn_area.get("evn_data_url"),
            headers=headers,
            params={
                "strMaDiemDo": f"{customer_id}{last_index}",
                "strFromDate": from_date_str,
                "strToDate": to_date_str,
            },
            session=self._session,
            api_name="Fetch EVN data"
        )

        if not resp_json:
            raise ValueError("Received empty response from EVN data API.")

        from_date = parser.parse(resp_json[0]["strTime"], dayfirst=True) + timedelta(days=1)
        to_date = parser.parse(
            resp_json[(-1 if len(resp_json) > 1 else 0)]["strTime"], dayfirst=True
        )
        previous_date = parser.parse(
            resp_json[(-2 if len(resp_json) > 2 else 0)]["strTime"], dayfirst=True
        )

        fetched_data = {
            "status": CONF_SUCCESS,
            ID_ECON_TOTAL_OLD: round(safe_float(resp_json[0].get("dGiaoBT")), 2),
            ID_ECON_TOTAL_NEW: round(safe_float(resp_json[-1].get("dGiaoBT")), 2),
            ID_ECON_DAILY_NEW: round(safe_float(resp_json[-1].get("dSanLuongBT")), 2),
            ID_ECON_DAILY_OLD: round(
                safe_float(resp_json[-2].get("dSanLuongBT")) if len(resp_json) > 1 else 0.0, 2
            ),
            ID_ECON_MONTHLY_NEW: round(
                safe_float(resp_json[-1].get("dGiaoBT")) - safe_float(resp_json[0].get("dGiaoBT"))
            ),
            "to_date": to_date.date(),
            "from_date": from_date.date(),
            "previous_date": previous_date.date(),
        }

        status, resp_json = await fetch_with_retries(
            url=self._evn_area.get("evn_payment_url"),
            headers=headers,
            params={
                "strMaKH": f"{customer_id}",
            },
            session=self._session,
            allow_empty=True,
            api_name="Payment data"
        )

        if status == CONF_SUCCESS and resp_json and isinstance(resp_json, list) and resp_json:
            m_payment_status = int(resp_json[0].get("lTongTien", 0))
            fetched_data.update({
                ID_PAYMENT_NEEDED: STATUS_PAYMENT_NEEDED,
                ID_M_PAYMENT_NEEDED: m_payment_status
            })
        else:
            fetched_data.update({
                ID_PAYMENT_NEEDED: STATUS_N_PAYMENT_NEEDED if status == CONF_EMPTY else CONF_ERR_UNKNOWN,
                ID_M_PAYMENT_NEEDED: 0
            })

        status, resp_json = await fetch_with_retries(
            url=self._evn_area.get("evn_loadshedding_url"),
            headers=headers,
            params={
                "strMaKH": f"{customer_id}",
            },
            session=self._session,
            api_name="EVN loadshedding data"
        )

        fetched_data[ID_LOADSHEDDING] = (
            resp_json[0].get("strThoiGianMatDien") if resp_json else STATUS_LOADSHEDDING if status == CONF_EMPTY else CONF_ERR_UNKNOWN
        )

        return fetched_data

    async def fetch_daily_range_evnspc(
        self,
        customer_id: str,
        from_date: str,
        to_date: str,
        last_index: str = "001",
    ):
        """
        Fetch raw daily data list from EVN SPC.
        from_date, to_date: string DD-MM-YYYY
        """
        from_date_str = parser.parse(from_date, dayfirst=True).strftime("%Y%m%d")
        to_date_str   = parser.parse(to_date, dayfirst=True).strftime("%Y%m%d")

        headers = {
            "User-Agent": "evnapp/59 CFNetwork/1240.0.4 Darwin/20.6.0",
            "Authorization": f"Bearer {self._evn_area.get('access_token')}",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Accept-Language": "vi-vn",
            "Connection": "keep-alive",
        }

        status, resp_json = await fetch_with_retries(
            url=self._evn_area.get("evn_data_url"),
            headers=headers,
            params={
                "strMaDiemDo": f"{customer_id}{last_index}",
                "strFromDate": from_date_str,
                "strToDate": to_date_str,
            },
            session=self._session,
            api_name="Fetch EVN daily raw data"
        )

        if status != CONF_SUCCESS:
            return []

        if not isinstance(resp_json, list):
            return []

        return resp_json

    async def fetch_monthly_bills_evnspc(
        self,
        customer_id: str,
        from_month: int,
        from_year: int,
        to_month: int,
        to_year: int,
    ):
        """
        Fetch monthly bill history for EVN SPC.
        """
        headers = {
            "User-Agent": "okhttp/3.12.12",
            "Authorization": f"Bearer {self._evn_area.get('access_token')}",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }

        status, resp_json = await fetch_with_retries(
            url="https://api.cskh.evnspc.vn/api/NghiepVu/TraCuuHoaDon",
            headers=headers,
            params={
                "strMaKH": customer_id,
                "iTuThang": from_month,
                "iTuNam": from_year,
                "iDenThang": to_month,
                "iDenNam": to_year,
            },
            session=self._session,
            api_name="Fetch EVN monthly bills"
        )

        if status != CONF_SUCCESS or not resp_json:
            return []

        return resp_json

async def json_processing(resp):
    if resp.status != 200:
        if resp.status in (400, 401):
            return CONF_ERR_INVALID_AUTH, {
                "status": CONF_ERR_INVALID_AUTH,
                "data": resp.status,
            }

        if resp.status == 405:
            return CONF_ERR_NOT_SUPPORTED, {
                "status": CONF_ERR_NOT_SUPPORTED,
                "data": resp.status,
            }

        _LOGGER.error(
            "Cannot connect to EVN Server while requesting new data: status code %s",
            resp.status,
        )
        return CONF_ERR_CANNOT_CONNECT, {
            "status": CONF_ERR_CANNOT_CONNECT,
            "data": resp.status,
        }

    # -----------------------------
    # SAFE JSON PARSING
    # -----------------------------
    try:
        # 1️⃣ ưu tiên aiohttp json() (tự handle gzip)
        resp_json = await resp.json(content_type=None)

        if not resp_json:
            return CONF_EMPTY, {
                "status": CONF_EMPTY,
                "data": {},
            }

        return CONF_SUCCESS, resp_json

    except Exception:
        # 2️⃣ fallback sang text
        try:
            text = (await resp.text()).strip()
            if not text:
                return CONF_EMPTY, {
                    "status": CONF_EMPTY,
                    "data": {},
                }

            resp_json = json.loads(text, strict=False)
            return CONF_SUCCESS, resp_json

        except Exception as error:
            _LOGGER.error(
                "Unable to fetch data from EVN Server while requesting new data: %s",
                error,
            )
            return CONF_ERR_UNKNOWN, {
                "status": CONF_ERR_UNKNOWN,
                "data": str(error),
            }

def formatted_result(raw_data: dict) -> dict:
    res = {}
    time_obj = datetime.now()

    res["status"] = CONF_SUCCESS

    res[ID_ECON_TOTAL_NEW] = {
        "value": raw_data[ID_ECON_TOTAL_NEW],
        "info": raw_data["to_date"],
    }

    res[ID_ECON_TOTAL_OLD] = {
        "value": raw_data[ID_ECON_TOTAL_OLD],
    }

    if raw_data[ID_ECON_MONTHLY_NEW] is not None:
        res[ID_ECON_MONTHLY_NEW] = {
            "value": raw_data[ID_ECON_MONTHLY_NEW],
        }
        res[ID_ECOST_MONTHLY_NEW] = {
            "value": calc_ecost(raw_data[ID_ECON_MONTHLY_NEW]),
        }

    if raw_data[ID_ECON_DAILY_NEW] is not None:
        if raw_data["to_date"] == time_obj.date():
            info = "hôm nay"
        elif raw_data["to_date"] == (time_obj - timedelta(days=1)).date():
            info = "hôm qua"
        else:
            info = f'ngày {raw_data["to_date"].strftime("%d/%m")}'

        res[ID_ECON_DAILY_NEW] = {"value": raw_data[ID_ECON_DAILY_NEW], "info": info}
        res[ID_ECOST_DAILY_NEW] = {
            "value": calc_ecost(raw_data[ID_ECON_DAILY_NEW]),
            "info": info,
        }

    if raw_data[ID_ECON_DAILY_OLD] is not None:
        if raw_data["previous_date"] == (time_obj - timedelta(days=2)).date():
            info = "hôm kia"
        elif raw_data["previous_date"] == (time_obj - timedelta(days=1)).date():
            info = "hôm qua"
        else:
            info = f'ngày {raw_data["previous_date"].strftime("%d/%m")}'

        res[ID_ECON_DAILY_OLD] = {"value": raw_data[ID_ECON_DAILY_OLD], "info": info}
        res[ID_ECOST_DAILY_OLD] = {
            "value": calc_ecost(raw_data[ID_ECON_DAILY_OLD]),
            "info": info,
        }

    res[ID_PAYMENT_NEEDED] = {
        "value": (
            None
            if (
                raw_data[ID_PAYMENT_NEEDED] != STATUS_N_PAYMENT_NEEDED
                and raw_data[ID_PAYMENT_NEEDED] != STATUS_PAYMENT_NEEDED
            )
            else raw_data[ID_PAYMENT_NEEDED]
        ),
        "info": (
            "mdi:comment-alert-outline"
            if raw_data[ID_PAYMENT_NEEDED] == STATUS_PAYMENT_NEEDED
            else (
                "mdi:comment-check-outline"
                if raw_data[ID_PAYMENT_NEEDED] == STATUS_N_PAYMENT_NEEDED
                else "mdi:comment-question-outline"
            )
        ),
    }

    res[ID_M_PAYMENT_NEEDED] = {
        "value": str(raw_data[ID_M_PAYMENT_NEEDED]),
        "info": (
            "mdi:alert-circle-outline"
            if raw_data[ID_M_PAYMENT_NEEDED] > 0
            else "mdi:checkbox-marked-circle-outline"
        ),
    }

    if raw_data.get(ID_LOADSHEDDING) is not None:
        original_content = raw_data.get(ID_LOADSHEDDING)
        formatted_content = (
            format_loadshedding(original_content)
            if original_content
            else STATUS_LOADSHEDDING
        )
    else:
        formatted_content = "Không hỗ trợ"

    res[ID_LOADSHEDDING] = {
        "value": formatted_content,
        "info": "mdi:transmission-tower-off",
    }

    if ID_FROM_DATE in raw_data:
        res[ID_FROM_DATE] = {"value": raw_data.get("from_date").strftime("%d/%m/%Y")}
    else:
        first_day_of_month = datetime.now().replace(day=1)
        res[ID_FROM_DATE] = {"value": first_day_of_month.strftime("%d/%m/%Y")}

    res[ID_TO_DATE] = {"value": raw_data.get("to_date").strftime("%d/%m/%Y")}

    res[ID_LATEST_UPDATE] = {"value": time_obj.astimezone()}

    return res

def get_evn_info(evn_customer_id: str):
    """Get EVN infomations based on Customer ID -> EVN Company, location, branches,..."""

    for index, each_area in enumerate(VIETNAM_EVN_AREA):
        for each_pattern in each_area.pattern:
            if each_pattern in evn_customer_id:

                evn_branch = "Unknown"

                file_path = os.path.join(os.path.dirname(__file__), "evn_branches.json")

                with open(file_path) as f:
                    evn_branches_list = json.load(f)

                    for evn_id in evn_branches_list:
                        if evn_id in evn_customer_id:
                            evn_branch = evn_branches_list[evn_id]

                return {
                    "status": CONF_SUCCESS,
                    "customer_id": evn_customer_id,
                    "evn_area": asdict(each_area),
                    "evn_name": each_area.name,
                    "evn_location": each_area.location,
                    "evn_branch": evn_branch,
                }

    return {"status": CONF_ERR_NOT_SUPPORTED}

def generate_datetime(monthly_start=1, offset=0):
    """Generate Datetime as string for requesting data purposes"""

    # Example:

    #   EVNSPC
    #   if offset == 1 means:
    #       When requesting to EVN endpoints for date 10/09/2022,
    #       the e-data returned from server would contain:
    #           - Total e-consumption data on 09/09/2022
    #           - E-monitor value at 23:59 09/09/2022

    #   if offset == 0 means:
    #       When requesting to EVN endpoints for date 10/09/2022,
    #       the e-data returned from server would contain:
    #           - Latest e-consumption data on 10/09/2022
    #           - E-monitor value at 23:59 10/09/2022

    from_date = ""
    time_obj = datetime.now()

    current_day = int(time_obj.strftime("%-d"))
    monthly_start_str = "{:0>2}".format(monthly_start - 1 + offset)

    to_date = (time_obj - timedelta(days=1 - offset)).strftime("%d/%m/%Y")

    # Example: billing start date is 08/09/2022
    #           and current date is 09/09/2022
    if current_day > monthly_start:
        from_date = f"{monthly_start_str}/{time_obj.strftime('%m/%Y')}"

    else:
        last_month = int(time_obj.strftime("%-m")) - 1

        # If current month >= 2
        if last_month:
            last_month_str = "{:0>2}".format(last_month)
            from_date = (
                f"{monthly_start_str}/{last_month_str}/{time_obj.strftime('%Y')}"
            )

        # If current month == 1
        #   last_month must be 12 and change Year to Last Year
        else:
            last_year = int(time_obj.strftime("%Y")) - 1
            from_date = f"{monthly_start_str}/12/{last_year}"

    return from_date, to_date

def safe_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "")) if value is not None else default
    except ValueError:
        return default

def format_loadshedding(raw_value: str) -> str:
    try:
        if not raw_value or 'đến' not in raw_value:
            return STATUS_LOADSHEDDING
            
        start, end = raw_value.replace('từ ', '').replace(' ngày', '').split('đến')
        start = start.strip().split()
        end = end.strip().split()
        if len(start) != 2 or len(end) != 2:
            return STATUS_LOADSHEDDING

        start_time, start_date = start
        end_time, end_date = end

        start_time = start_time[:-3]
        end_time = end_time[:-3]
        start_date = start_date[:-5]
        end_date = end_date[:-5]

        return f"{start_time} {start_date} - {end_time} {end_date}"
    
    except Exception as e:
        return STATUS_LOADSHEDDING

def strip_date_range(date_str):
    if "đến" in date_str:
        stripped_date = date_str.split("đến")[1].strip()
    else:
        stripped_date = date_str.strip()
    return parser.parse(stripped_date, dayfirst=True)

async def fetch_with_retries(
    url, headers, params, max_retries=3, session=None, allow_empty=False, api_name="API"
):
    """Fetch data with retry mechanism."""
    for attempt in range(max_retries):
        try:
            resp = await session.get(url=url, headers=headers, params=params, ssl=False)
            status, resp_json = await json_processing(resp)
            
            if status == CONF_EMPTY:
                return CONF_EMPTY, []

            if status == CONF_SUCCESS or (allow_empty and status == CONF_EMPTY):
                return status, resp_json
            
            _LOGGER.error(f"Attempt {attempt + 1}/{max_retries} failed for {api_name}: {resp_json}")
        
        except Exception as e:
            _LOGGER.error(f"Attempt {attempt + 1}/{max_retries} encountered an error: {str(e)}")

    raise Exception(f"Failed to fetch data of {api_name} after {max_retries} attempts.")

def get_evn_info_sync(customer_id: str, branches_data=None):
    """Synchronous helper to get EVN info"""
    for index, each_area in enumerate(VIETNAM_EVN_AREA):
        for each_pattern in each_area.pattern:
            if each_pattern in customer_id:
                evn_branch = "Unknown"

                if branches_data:
                    for evn_id in branches_data:
                        if evn_id in customer_id:
                            evn_branch = branches_data[evn_id]

                return {
                    "status": CONF_SUCCESS,
                    "customer_id": customer_id,
                    "evn_area": asdict(each_area),
                    "evn_name": each_area.name,
                    "evn_location": each_area.location,
                    "evn_branch": evn_branch,
                }

    return {"status": CONF_ERR_NOT_SUPPORTED}

async def get_evn_info(hass: HomeAssistant, customer_id: str):
    """Async wrapper for EVN info"""
    return await hass.async_add_executor_job(get_evn_info_sync, customer_id)
