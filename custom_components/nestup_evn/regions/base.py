import logging
import json
import ssl
from abc import ABC, abstractmethod
from typing import Any, List, Dict, Optional
from homeassistant.core import HomeAssistant
from ..types import Area, EVNUpdateResponse, DailyHistoryRecord, MonthlyBillRecord
from .utils import create_ssl_context
from ..const import CONF_SUCCESS, CONF_ERR_UNKNOWN

_LOGGER = logging.getLogger(__name__)

class EVNRegion(ABC):
    def __init__(self, hass: HomeAssistant, session, evn_area: Area):
        self.hass = hass
        self._session = session
        self._evn_area = evn_area

    @abstractmethod
    async def login(self, username, password, customer_id) -> str:
        """Authenticate with the regional API."""
        pass

    @abstractmethod
    async def request_update(self, username, password, customer_id, from_date, to_date) -> EVNUpdateResponse:
        """Request the latest sensor data."""
        pass

    async def fetch_daily_history(self, username, password, customer_id: str, start_date, end_date) -> List[DailyHistoryRecord]:
        """Fetch daily consumption records in standardized format."""
        return []

    async def fetch_monthly_history(self, username, password, customer_id: str, history_start_date) -> List[MonthlyBillRecord]:
        """Fetch monthly bill records in standardized format."""
        return []

    async def _request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        json_data: Optional[Any] = None,
        use_ssl: bool = True,
        api_name: str = "API"
    ) -> tuple[str, Any]:
        """Standardized request handler for all regions."""
        if not url:
            _LOGGER.debug("%s: URL is None, skipping request", api_name)
            return CONF_ERR_UNKNOWN, None
        try:
            ssl_context = False
            if use_ssl:
                ssl_context = await self.hass.async_add_executor_job(create_ssl_context)
            
            async with self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                data=data,
                json=json_data,
                ssl=ssl_context,
                timeout=30
            ) as resp:
                text = await resp.text()
                try:
                    resp_json = json.loads(text)
                    return CONF_SUCCESS, resp_json
                except json.JSONDecodeError:
                    if resp.status == 200 and not text:
                        return CONF_SUCCESS, {}
                    _LOGGER.error("%s: Non-JSON response from %s (Status: %s): %s", api_name, url, resp.status, text[:255])
                    return f"http_{resp.status}", {"raw_text": text}
        except Exception as ex:
            _LOGGER.error("%s: Request to %s failed: %s", api_name, url, ex)
            return CONF_ERR_UNKNOWN, {}
