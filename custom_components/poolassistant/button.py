from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import build_device_info


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    device_info = build_device_info(coordinator, entry)
    async_add_entities([PoolRefreshButton(coordinator, entry, device_info)])


class PoolRefreshButton(CoordinatorEntity, ButtonEntity):
    """Manually pull the latest data from Firestore right now."""

    _attr_name = "Refresh Now"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
