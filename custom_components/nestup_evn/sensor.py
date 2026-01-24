"""Setup and manage HomeAssistant Entities."""

import logging
from typing import Any
import os
from datetime import datetime

from .data_storage import EVNDataStorage

from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.components.sensor import (
    DOMAIN as ENTITY_DOMAIN,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from . import nestup_evn
from .const import (
    CONF_AREA,
    CONF_CUSTOMER_ID,
    CONF_DEVICE_MANUFACTURER,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DEVICE_SW_VERSION,
    CONF_ERR_INVALID_AUTH,
    CONF_ERR_UNKNOWN,
    CONF_MONTHLY_START,
    CONF_PASSWORD,
    CONF_SUCCESS,
    CONF_USERNAME,
    CONF_HISTORY_START_DATE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ID_ECON_DAILY_NEW,
    ID_ECON_DAILY_OLD,
    ID_ECOST_DAILY_NEW,
    ID_ECOST_DAILY_OLD,
)

from .types import EVN_SENSORS, EVNSensorEntityDescription

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    entry_config = hass.data[DOMAIN][entry.entry_id]
    evn_api = nestup_evn.EVNAPI(hass, True)
    evn_device = EVNDevice(entry_config, evn_api)
    await evn_device.async_create_coordinator(hass)

    entities = [
        EVNSensor(evn_device, description, hass)
        for description in EVN_SENSORS
    ]
    async_add_entities(entities)


class EVNDevice:
    def __init__(self, dataset, api: nestup_evn.EVNAPI) -> None:
        self._name = f"{CONF_DEVICE_NAME}: {dataset[CONF_CUSTOMER_ID]}"
        self.hass = api.hass
        self._username = dataset.get(CONF_USERNAME)
        self._password = dataset.get(CONF_PASSWORD)
        self._area_name = dataset.get(CONF_AREA)
        self._customer_id = dataset.get(CONF_CUSTOMER_ID)
        self._monthly_start = dataset.get(CONF_MONTHLY_START)
        self._api = api
        self._data = {}
        self._branches_data = None
        self._coordinator = None

        history_start_iso = dataset.get(CONF_HISTORY_START_DATE)
        history_start_date = None
        if history_start_iso:
            history_start_date = datetime.strptime(
                history_start_iso, "%Y-%m-%d"
            ).date()

        self._storage = EVNDataStorage(
            self.hass,
            self._customer_id,
            history_start_date=history_start_date,
        )

    async def async_load_branches(self):
        try:
            file_path = os.path.join(
                os.path.dirname(nestup_evn.__file__),
                "evn_branches.json",
            )
            self._branches_data = await self.hass.async_add_executor_job(
                nestup_evn.read_evn_branches_file, file_path
            )
        except Exception as ex:
            _LOGGER.error("Load branch data failed: %s", ex)

    async def update(self) -> dict[str, Any]:
        data = await self._api.request_update(
            self._area_name,
            self._username,
            self._password,
            self._customer_id,
            self._monthly_start,
        )

        if data.get("status") != CONF_SUCCESS:
            raise UpdateFailed(f"EVN update failed: {self._customer_id}")

        self._data = data
        await self._storage.async_update_from_sensor_data(data)
        return self._data

    async def _async_update(self):
        return await self.update()

    async def async_create_coordinator(self, hass: HomeAssistant) -> None:
        if self._coordinator:
            return

        await self.async_load_branches()
        await self._storage.async_load()

        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self._customer_id}",
            update_method=self._async_update,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self._coordinator = coordinator
        await coordinator.async_config_entry_first_refresh()
        if not self._storage.data.get("meta", {}).get("backfill_done"):
            self._storage.start_background_backfill(self._api)

    @property
    def coordinator(self):
        return self._coordinator

    @property
    def branch_info(self):
        if self._branches_data is None:
            return {"status": CONF_ERR_UNKNOWN}
        return nestup_evn.get_evn_info_sync(
            self._customer_id, self._branches_data
        )

class EVNSensor(CoordinatorEntity, SensorEntity):
    """EVN Sensor Instance."""

    def __init__(
        self, device: EVNDevice, description: EVNSensorEntityDescription, hass
    ):
        """Construct EVN sensor wrapper."""
        super().__init__(device.coordinator)

        self._device = device
        self._attr_name = f"{device._name} {description.name}"
        self._unique_id = str(f"{device._customer_id}_{description.key}").lower()
        self._default_name = description.name
        name = description.name
        if device._area_name.get("name") == "EVNCPC":
            if description.key in (ID_ECON_DAILY_NEW, ID_ECOST_DAILY_NEW):
                name = name.replace("hôm qua", "hôm nay")
            elif description.key in (ID_ECON_DAILY_OLD, ID_ECOST_DAILY_OLD):
                name = name.replace("hôm kia", "hôm qua")
        self._attr_name = f"{device._name} {name}"
        self._unique_id = f"{device._customer_id}_{description.key}".lower()
        self.entity_id = (
            f"{ENTITY_DOMAIN}.{device._customer_id}_{description.key}".lower()
        )
        self.entity_description = description

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self.entity_description.value_fn(self._device._data) or {}

        if self.entity_description.dynamic_name:
            self._attr_name = f"{self._default_name} {data.get('info')}"

        if self.entity_description.dynamic_icon:
            self._attr_icon = data.get("info")

        return data.get("value")

    @property
    def device_info(self):
        """Return a device description for device registry."""
        hw_version = f"by {self._device._area_name['name']}"

        evn_area = self._device.branch_info
        if (evn_area["status"] == CONF_SUCCESS) and (
            evn_area["evn_branch"] != "Unknown"
        ):
            hw_version = f"by {evn_area['evn_branch']}"

        return DeviceInfo(
            name=self._device._name,
            identifiers={(DOMAIN, self._device._customer_id)},
            manufacturer=CONF_DEVICE_MANUFACTURER,
            sw_version=CONF_DEVICE_SW_VERSION,
            hw_version=hw_version,
            model=CONF_DEVICE_MODEL,
        )

    @property
    def available(self) -> bool:
        """Return the availability of the sensor."""
        return (
            self._device._data.get("status") == CONF_SUCCESS
            and self.native_value is not None
        )

    @property
    def last_reset(self):
        if self.entity_description.state_class == SensorStateClass.TOTAL:
            data = self.entity_description.value_fn(self._device._data)

            return data.get("info")

        return None
