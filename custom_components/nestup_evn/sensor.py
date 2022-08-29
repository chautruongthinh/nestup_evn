"""Setup and manage HomeAssistant Entities."""

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
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
    CONF_MONTHLY_START,
    CONF_PASSWORD,
    CONF_SUCCESS,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .types import EVN_SENSORS, EVNSensorEntityDescription

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Setup the sensor platform."""

    entry_config = hass.data[DOMAIN][entry.entry_id]

    evn_api = nestup_evn.EVNAPI(hass, True)
    evn_device = EVNDevice(entry_config, evn_api)

    await evn_device.async_create_coordinator(hass)

    entities = []
    entities.extend([EVNSensor(evn_device, description) for description in EVN_SENSORS])

    async_add_entities(entities)


class EVNDevice:
    """EVN Device Instance"""

    def __init__(self, dataset, api: nestup_evn.EVNAPI) -> None:
        """Construct Device wrapper."""

        self._name = f"{CONF_DEVICE_NAME}: {dataset[CONF_CUSTOMER_ID]}"
        self._coordinator: DataUpdateCoordinator = None

        if CONF_USERNAME in dataset:
            self._username = dataset[CONF_USERNAME]
            self._password = dataset[CONF_PASSWORD]
        else:
            self._username = None
            self._password = None

        self._area_name = dataset[CONF_AREA]
        self._customer_id = dataset[CONF_CUSTOMER_ID]
        self._monthly_start = dataset[CONF_MONTHLY_START]

        self._api = api
        self._data = {}

    async def update(self) -> dict[str, Any]:
        """Update device data from EVN Endpoints."""

        if self._area_name.get("auth_needed"):
            login_state = await self._api.login(
                self._area_name, self._username, self._password
            )

            if login_state != CONF_SUCCESS:
                return

        self._data = await self._api.request_update(
            self._area_name, self._customer_id, self._monthly_start
        )

        if self._data["status"] == CONF_SUCCESS:
            _LOGGER.info(
                "Successfully fetched new data for EVN Customer ID: %s",
                self._customer_id,
            )
        else:
            _LOGGER.warn(
                "Could not fetch new data for EVN Customer ID: %s", self._customer_id
            )

        return self._data

    async def _async_update(self):
        """Fetch the latest data from EVN."""
        await self.update()

    async def async_create_coordinator(self, hass: HomeAssistant) -> None:
        """Create the coordinator for this specific device."""
        if self._coordinator:
            return

        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self._customer_id}",
            update_method=self._async_update,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        await coordinator.async_config_entry_first_refresh()
        self._coordinator = coordinator

    @property
    def info(self) -> DeviceInfo:
        """Return device description for device registry."""
        evn_area = nestup_evn.get_evn_info(self._customer_id)
        hw_version = f"by {self._area_name['name']}"

        if (evn_area["status"] == CONF_SUCCESS) and (
            evn_area["evn_branch"] != "Unknown"
        ):
            hw_version = f"by {evn_area['evn_branch']}"

        return DeviceInfo(
            name=self._name,
            identifiers={(DOMAIN, self._customer_id)},
            manufacturer=CONF_DEVICE_MANUFACTURER,
            sw_version=CONF_DEVICE_SW_VERSION,
            hw_version=hw_version,
            model=CONF_DEVICE_MODEL,
        )

    @property
    def coordinator(self) -> DataUpdateCoordinator or None:
        """Return coordinator associated."""
        return self._coordinator


class EVNSensor(CoordinatorEntity, SensorEntity):
    """EVN Sensor Instance."""

    def __init__(self, device: EVNDevice, description: EVNSensorEntityDescription):
        """Construct EVN sensor wrapper."""
        super().__init__(device.coordinator)

        self._device = device
        self._attr_unique_id = f"{device._customer_id}_{description.key}"
        self._attr_name = f"{device._name} {description.name}"

        self.entity_description = description

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self._device._data)

    @property
    def device_info(self):
        """Return a device description for device registry."""
        return self._device.info

    @property
    def available(self) -> bool:
        """Return the availability of the sensor."""
        return self._device._data["status"] == CONF_SUCCESS
