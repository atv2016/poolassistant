"""Binary sensor platform for Pool Assistant."""
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, OPTION_IDEAL_RANGES, PARAMETERS
from .coordinator import build_device_info
from .status import effective_range, status_for


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    device_info = build_device_info(coordinator, entry)
    async_add_entities([PoolNeedsAttentionBinarySensor(coordinator, entry, device_info)])


class PoolNeedsAttentionBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """On when any tracked parameter is outside its ideal range - the same
    underlying logic as the Pool Status sensor, exposed as a plain on/off
    entity since that's the more idiomatic building block for automation
    triggers than matching an enum sensor's state text.
    """

    _attr_name = "Needs Attention"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_needs_attention"
        self._attr_device_info = device_info

    def _problems(self) -> dict:
        overrides = self.coordinator.entry.options.get(OPTION_IDEAL_RANGES, {})
        problems = {}
        for key in PARAMETERS:
            reading = self.coordinator.data["parameters"].get(key) or {}
            result = reading.get("result")
            low = reading.get("lowRange")
            high = reading.get("highRange")
            status = status_for(key, result, low, high, overrides)
            if status in ("low", "high"):
                eff_low, eff_high, source = effective_range(key, low, high, overrides)
                problems[key] = {
                    "status": status,
                    "result": result,
                    "range": f"{eff_low}\u2013{eff_high}" if eff_low is not None and eff_high is not None else None,
                    "range_source": source,
                }
        return problems

    @property
    def is_on(self) -> bool:
        return bool(self._problems())

    @property
    def extra_state_attributes(self):
        return {"out_of_range_parameters": self._problems()}
