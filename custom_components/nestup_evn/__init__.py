from __future__ import annotations

import json
import logging
import os
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady


from .const import (
    DOMAIN,
    CONF_AREA,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_CUSTOMER_ID,
    CONF_MONTHLY_START,
)
from .data_storage import EVNDataStorage
from .nestup_evn import EVNAPI
from .views import (
    EVNPingView,
    EVNStaticView,
    EVNOptionsView,
    EVNMonthlyDataView,
    EVNDailyDataView,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the EVN component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up nestup_evn from a config entry."""

    api = EVNAPI(hass, True)

    try:
        await api.request_update(
            entry.data.get(CONF_AREA),
            entry.data.get(CONF_USERNAME),
            entry.data.get(CONF_PASSWORD),
            entry.data.get(CONF_CUSTOMER_ID),
            entry.data.get(CONF_MONTHLY_START),
        )
    except Exception as err:
        raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data

    # Register API views (only once)
    if "api_registered" not in hass.data[DOMAIN]:
        webui_path = hass.config.path("custom_components/nestup_evn/webui")

        hass.http.register_view(EVNStaticView(webui_path))
        hass.http.register_view(EVNPingView(hass))
        hass.http.register_view(EVNOptionsView(hass))
        hass.http.register_view(EVNMonthlyDataView(hass))
        hass.http.register_view(EVNDailyDataView(hass))

        hass.data[DOMAIN]["api_registered"] = True
        _LOGGER.info("Registered EVN API endpoints and WebUI at %s", webui_path)

    # Register WebUI panel (only once)
    if "panel_registered" not in hass.data[DOMAIN]:
        try:
            from homeassistant.components import frontend
            # Note: This is NOT an async function, don't use await
            frontend.async_register_built_in_panel(
                hass,
                "iframe",
                "EVN Monitor",
                "mdi:lightning-bolt",
                "evn_monitor",
                {"url": "/evn-monitor/index.html"},
                require_admin=False,
            )
            hass.data[DOMAIN]["panel_registered"] = True
            _LOGGER.info("Registered EVN Monitor panel")
        except Exception as ex:
            _LOGGER.warning("Could not register panel: %s", str(ex))

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle removal of an EVN config entry."""

    customer_id = entry.data.get(CONF_CUSTOMER_ID)
    if not customer_id:
        return

    storage_dir = hass.config.path("nestup_evn")
    file_path = os.path.join(storage_dir, f"{customer_id}.json")

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            _LOGGER.info(
                "[EVN] Removed history data for customer %s",
                customer_id,
            )
    except Exception as ex:
        _LOGGER.error(
            "[EVN] Failed to remove history data for %s: %s",
            customer_id,
            ex,
        )
        return 

    try:
        if os.path.isdir(storage_dir) and not os.listdir(storage_dir):
            os.rmdir(storage_dir)
            _LOGGER.info(
                "[EVN] Removed empty storage directory %s",
                storage_dir,
            )
    except Exception as ex:
        _LOGGER.debug(
            "[EVN] Storage directory not removed: %s",
            ex,
        )
