"""Config flow for EVN Data integration."""

from __future__ import annotations

import logging
from typing import Any
from datetime import datetime
import os

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from . import nestup_evn
from .data_storage import EVNDataStorage
from .const import (
    CONF_AREA,
    CONF_CUSTOMER_ID,
    CONF_ERR_UNKNOWN,
    CONF_MONTHLY_START,
    CONF_PASSWORD,
    CONF_SUCCESS,
    CONF_USERNAME,
    DOMAIN,
    CONF_HISTORY_START_DATE,
)

_LOGGER = logging.getLogger(__name__)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Config Flow for setting up EVN integration."""

    VERSION = 1

    def __init__(self):
        self._user_data: dict[str, Any] = {}
        self._api: nestup_evn.EVNAPI | None = None
        self._errors: dict[str, str] = {}
        self._branches_data = None

    async def _load_branches_data(self):
        """Load EVN branches data asynchronously."""
        try:
            file_path = os.path.join(
                os.path.dirname(nestup_evn.__file__),
                "evn_branches.json",
            )
            self._branches_data = await self.hass.async_add_executor_job(
                nestup_evn.read_evn_branches_file,
                file_path,
            )
        except Exception as ex:
            _LOGGER.error("Error loading branches data: %s", ex)
            return None

        return self._branches_data

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user step."""

        self._errors = {}

        if user_input is not None:
            history_start = user_input.get(CONF_HISTORY_START_DATE)
            if history_start:
                try:
                    dt = datetime.strptime(history_start, "%d-%m-%Y")
                    if dt.year < 2024:
                        raise ValueError
                    user_input[CONF_HISTORY_START_DATE] = dt.strftime(
                        "%Y-%m-%d"
                    )
                except ValueError:
                    self._errors[CONF_HISTORY_START_DATE] = "invalid_date"

            if not self._errors:
                self._user_data.update(user_input)

                if self._branches_data is None:
                    await self._load_branches_data()

                evn_info = nestup_evn.get_evn_info_sync(
                    self._user_data[CONF_CUSTOMER_ID],
                    self._branches_data,
                )

                if evn_info.get("status") is not CONF_SUCCESS:
                    self._errors["base"] = evn_info.get(
                        "status", CONF_ERR_UNKNOWN
                    )
                else:
                    self._user_data[CONF_AREA] = evn_info["evn_area"]

                    self._api = nestup_evn.EVNAPI(self.hass, True)

                    login_state = await self._api.login(
                        self._user_data[CONF_AREA],
                        self._user_data[CONF_USERNAME],
                        self._user_data[CONF_PASSWORD],
                        self._user_data[CONF_CUSTOMER_ID],
                    )

                    if login_state is not CONF_SUCCESS:
                        self._errors["base"] = login_state
                    else:
                        verify = await self._verify_id()
                        if verify is not CONF_SUCCESS:
                            self._errors["base"] = verify
                        else:
                            await self.async_set_unique_id(
                                self._user_data[CONF_CUSTOMER_ID]
                            )
                            self._abort_if_unique_id_configured()

                            entry = self.async_create_entry(
                                title=self._user_data[
                                    CONF_CUSTOMER_ID
                                ],
                                data=self._user_data,
                            )

                            return entry

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_CUSTOMER_ID): vol.All(
                    str,
                    vol.Length(min=11, max=13),
                ),
                vol.Optional(
                    CONF_MONTHLY_START,
                    default=14,
                ): vol.All(int, vol.Range(min=1, max=28)),
                vol.Optional(
                    CONF_HISTORY_START_DATE,
                    default="01-01-2025",
                ): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=self._errors,
        )

    async def _verify_id(self) -> str:
        """Verify customer ID by requesting initial data."""

        try:
            res = await self._api.request_update(
                self._user_data[CONF_AREA],
                self._user_data[CONF_USERNAME],
                self._user_data[CONF_PASSWORD],
                self._user_data[CONF_CUSTOMER_ID],
                self._user_data.get(CONF_MONTHLY_START),
            )

            status = res.get("status")
            if status == CONF_SUCCESS:
                return CONF_SUCCESS

            return (
                status
                if isinstance(status, str)
                else CONF_ERR_UNKNOWN
            )

        except Exception as ex:
            _LOGGER.exception(
                "Unexpected exception while verifying ID: %s",
                ex,
            )
            return CONF_ERR_UNKNOWN
