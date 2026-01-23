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
from .views import EVNPingView, EVNStaticView

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
        # WebUI Static View
        webui_path = hass.config.path("custom_components/nestup_evn/webui")
        hass.http.register_view(EVNStaticView(webui_path))
        
        # API Views
        hass.http.register_view(EVNPingView(hass))
        hass.http.register_view(EVNOptionsView(hass))
        hass.http.register_view(EVNMonthlyDataView(hass))
        hass.http.register_view(EVNDailyDataView(hass))
        
        hass.data[DOMAIN]["api_registered"] = True
        _LOGGER.info("Registered EVN API endpoints and WebUI at %s", webui_path)

    # Register WebUI panel (only once)
    if "panel_registered" not in hass.data[DOMAIN]:
        try:
            # Use the async method directly from hass.components.frontend
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
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


class EVNOptionsView(HomeAssistantView):
    """View to return available accounts."""

    url = "/api/nestup_evn/options"
    name = "api:nestup_evn:options"
    requires_auth = False

    def __init__(self, hass):
        """Initialize the view."""
        self.hass = hass

    async def get(self, request):
        """Get list of configured accounts."""
        try:
            hass = request.app["hass"]
            
            accounts = []
            # Track added customer_ids to prevent duplicates
            added_ids = set()

            # 1. Get from Config Entries
            entries = hass.config_entries.async_entries(DOMAIN)
            for entry in entries:
                customer_id = entry.data.get(CONF_CUSTOMER_ID)
                if customer_id and customer_id not in added_ids:
                    accounts.append({
                        "id": customer_id,
                        "userevn": customer_id,  # Required by WebUI data.js
                        "name": f"EVN {customer_id}",
                        "customer_id": customer_id
                    })
                    added_ids.add(customer_id)
            
            # 2. Scan for JSON files in nestup_evn dir
            storage_dir = hass.config.path("nestup_evn")
            if os.path.exists(storage_dir):
                for filename in os.listdir(storage_dir):
                    if filename.endswith(".json") and not filename.startswith("evn_branches"):
                        # Customer ID is the filename without extension
                        customer_id = filename[:-5]
                        
                        if customer_id and customer_id not in added_ids:
                            accounts.append({
                                "id": customer_id,
                                "userevn": customer_id,
                                "name": f"EVN {customer_id} (File)",
                                "customer_id": customer_id
                            })
                            added_ids.add(customer_id)
            
            _LOGGER.info("EVNOptionsView returning accounts: %s", accounts)
            
            # Return in format expected by WebUI
            return web.json_response({
                "accounts_json": json.dumps(accounts)
            })
        except Exception as ex:
            _LOGGER.error("Error in EVNOptionsView: %s", str(ex), exc_info=True)
            return web.json_response({"error": str(ex)}, status=500)


class EVNMonthlyDataView(HomeAssistantView):
    """View to return monthly data for an account."""

    url = "/api/nestup_evn/monthly/{account}"
    name = "api:nestup_evn:monthly"
    requires_auth = False

    def __init__(self, hass):
        """Initialize the view."""
        self.hass = hass

    async def get(self, request, account):
        """Get monthly data for account."""
        hass = request.app["hass"]
        
        try:
            # Load data from storage
            storage = EVNDataStorage(hass, account)
            await storage.async_load()
            
            # Get formatted data for WebUI
            data = storage.get_data_for_webui()
            
            _LOGGER.info("EVNMonthlyDataView returning data for %s: %d months", account, len(data.get("monthly", {}).get("SanLuong", [])))
            return web.json_response(data["monthly"])
            
        except Exception as ex:
            _LOGGER.error("Error getting monthly data for %s: %s", account, str(ex), exc_info=True)
            return web.json_response(
                {"error": str(ex)},
                status=500
            )


class EVNDailyDataView(HomeAssistantView):
    """View to return daily data for an account."""

    url = "/api/nestup_evn/daily/{account}"
    name = "api:nestup_evn:daily"
    requires_auth = False

    def __init__(self, hass):
        """Initialize the view."""
        self.hass = hass

    async def get(self, request, account):
        """Get daily data for account."""
        hass = request.app["hass"]
        
        try:
            # Load data from storage
            storage = EVNDataStorage(hass, account)
            await storage.async_load()
            
            # Get formatted data for WebUI
            data = storage.get_data_for_webui()
            
            _LOGGER.info("EVNDailyDataView returning data for %s: %d days", account, len(data.get("daily", [])))
            return web.json_response(data["daily"])
            
        except Exception as ex:
            _LOGGER.error("Error getting daily data for %s: %s", account, str(ex), exc_info=True)
            return web.json_response(
                {"error": str(ex)},
                status=500
            )
