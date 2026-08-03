import logging
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PARAMETERS, OPTION_IDEAL_RANGES, OPTION_TEMPERATURE_ENTITY
from .coordinator import build_device_info, ms_to_datetime
from .lsi import calculate_lsi
from .status import effective_range, status_for

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    device_info = build_device_info(coordinator, entry)

    entities = [
        PoolInfoSensor(coordinator, entry, device_info, "name", "Pool Name", "mdi:pool"),
        PoolInfoSensor(coordinator, entry, device_info, "volume_gallons", "Pool Volume", "mdi:waves", unit="gal"),
        PoolInfoSensor(coordinator, entry, device_info, "surface", "Pool Surface", "mdi:layers-outline"),
        PoolInfoSensor(coordinator, entry, device_info, "city", "Pool City", "mdi:map-marker"),
        LastChemicalSensor(coordinator, entry, device_info),
        PoolStatusSensor(coordinator, entry, device_info),
        DaysSinceLastTestSensor(coordinator, entry, device_info),
    ]
    entities += [
        PoolParameterSensor(coordinator, entry, device_info, key, meta)
        for key, meta in PARAMETERS.items()
    ]

    temperature_entity_id = entry.options.get(OPTION_TEMPERATURE_ENTITY)
    if temperature_entity_id:
        entities.append(PoolLsiSensor(coordinator, entry, device_info, temperature_entity_id))

    async_add_entities(entities)

class DaysSinceLastTestSensor(CoordinatorEntity, SensorEntity):
    """Days since any water-chemistry parameter was last tested."""

    _attr_name = "Days Since Last Test"
    _attr_icon = "mdi:calendar-clock"
    _attr_native_unit_of_measurement = "d"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_days_since_last_test"
        self._attr_device_info = device_info

    def _latest_time_ms(self):
        times = [
            reading.get("time")
            for reading in self.coordinator.data["parameters"].values()
            if reading and reading.get("time") is not None
        ]
        return max(times) if times else None

    @property
    def native_value(self):
        latest_ms = self._latest_time_ms()
        timestamp = ms_to_datetime(latest_ms)
        if timestamp is None:
            return None
        return (datetime.now(timezone.utc) - timestamp).days

    @property
    def extra_state_attributes(self):
        timestamp = ms_to_datetime(self._latest_time_ms())
        return {"last_test_timestamp": timestamp.isoformat() if timestamp else None}
        
class PoolInfoSensor(CoordinatorEntity, SensorEntity):
    """Static-ish pool info: name, volume, surface, city..."""

    def __init__(self, coordinator, entry, device_info, key, name, icon, unit=None):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._attr_device_info = device_info

    @property
    def native_value(self):
        return self.coordinator.data["pool"].get(self._key)


class PoolParameterSensor(CoordinatorEntity, SensorEntity):
    """A single water-chemistry parameter (Free Chlorine, pH, ...).

    Always exists - reports 'unknown' rather than disappearing if the pool
    has never been tested for it.
    """

    def __init__(self, coordinator, entry, device_info, key, meta):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = meta["name"]
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_icon = meta.get("icon")
        self._attr_native_unit_of_measurement = meta.get("unit")
        self._attr_device_class = meta.get("device_class")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_suggested_display_precision = 2
        self._attr_device_info = device_info

    @property
    def _reading(self) -> dict:
        return self.coordinator.data["parameters"].get(self._key, {})

    @property
    def native_value(self):
        result = self._reading.get("result")
        return float(result) if result is not None else None

    @property
    def extra_state_attributes(self):
        reading = self._reading
        if not reading:
            return {}

        timestamp = ms_to_datetime(reading.get("time"))
        age_seconds = None
        if timestamp is not None:
            age_seconds = int((datetime.now(timezone.utc) - timestamp).total_seconds())

        low = reading.get("lowRange")
        high = reading.get("highRange")
        result = reading.get("result")

        overrides = self.coordinator.entry.options.get(OPTION_IDEAL_RANGES, {})
        effective_low, effective_high, range_source = effective_range(self._key, low, high, overrides)

        return {
            "timestamp": timestamp.isoformat() if timestamp else None,
            "age_seconds": age_seconds,
            "manual": reading.get("manual"),
            "source": "manual" if reading.get("manual") else "device",
            "device_serial": reading.get("serialNumber"),
            "method": reading.get("methodName"),
            "method_number": reading.get("methodNumber"),
            "method_version": reading.get("methodVersion"),
            "firmware": reading.get("firmwareVersion"),
            "result_id": reading.get("resultId"),
            "unique_id": reading.get("uniqueID"),
            "unit_raw": reading.get("unit"),
            "range": f"{effective_low}\u2013{effective_high}" if effective_low is not None and effective_high is not None else None,
            "range_source": range_source,
            "status_code": reading.get("resultStatusCode"),
            "flag": reading.get("flag"),
            "status": status_for(self._key, result, low, high, overrides),
        }


class LastChemicalSensor(CoordinatorEntity, SensorEntity):
    """The most recent chemical dosing logged in the app."""

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator)
        self._attr_name = "Last Chemical Added"
        self._attr_unique_id = f"{entry.entry_id}_last_chemical"
        self._attr_icon = "mdi:cup-water"
        self._attr_device_info = device_info

    @property
    def _latest(self) -> dict:
        chemicals = self.coordinator.data.get("chemicals") or []
        return chemicals[0] if chemicals else {}

    @property
    def native_value(self):
        return self._latest.get("name")

    @property
    def extra_state_attributes(self):
        chem = self._latest
        if not chem:
            return {}
        timestamp = ms_to_datetime(chem.get("time"))
        return {
            "amount": chem.get("amount"),
            "unit": chem.get("unit"),
            "timestamp": timestamp.isoformat() if timestamp else None,
        }


class PoolStatusSensor(CoordinatorEntity, SensorEntity):
    """Overall pool status, based on how many tracked parameters are out of range."""

    _attr_name = "Pool Status"
    _attr_icon = "mdi:pool-thermometer"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["ideal", "needs_attention"]
    _attr_translation_key = "pool_status"

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_device_info = device_info

    def _details(self) -> dict[str, dict]:
        overrides = self.coordinator.entry.options.get(OPTION_IDEAL_RANGES, {})
        out = {}
        for key in PARAMETERS:
            reading = self.coordinator.data["parameters"].get(key) or {}
            result = reading.get("result")
            low = reading.get("lowRange")
            high = reading.get("highRange")
            status = status_for(key, result, low, high, overrides)
            if status in ("low", "ideal", "high"):
                eff_low, eff_high, source = effective_range(key, low, high, overrides)
                out[key] = {
                    "status": status,
                    "result": result,
                    "range": f"{eff_low}\u2013{eff_high}" if eff_low is not None and eff_high is not None else None,
                    "range_source": source,
                }
        return out

    @property
    def native_value(self):
        details = self._details()
        if not details:
            return None  # -> HA's own "Unknown" state, properly localized
        if any(d["status"] in ("low", "high") for d in details.values()):
            return "needs_attention"
        return "ideal"

    @property
    def extra_state_attributes(self):
        details = self._details()
        return {
            "out_of_range_parameters": {k: v for k, v in details.items() if v["status"] in ("low", "high")},
            "tracked_parameters": len(details),
        }


class PoolLsiSensor(CoordinatorEntity, SensorEntity):
    """Automatic Langelier Saturation Index, recalculated whenever pH/TA/CH
    change or the linked temperature sensor updates. Only created when a
    temperature entity has been chosen via the integration's options.
    """

    _attr_name = "LSI"
    _attr_icon = "mdi:water-check"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, entry, device_info, temperature_entity_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_lsi"
        self._attr_device_info = device_info
        self._temperature_entity_id = temperature_entity_id
        self._unsub_temperature = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_temperature = async_track_state_change_event(
            self.hass, [self._temperature_entity_id], self._handle_temperature_change
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_temperature:
            self._unsub_temperature()
        await super().async_will_remove_from_hass()

    @callback
    def _handle_temperature_change(self, event) -> None:
        self.async_write_ha_state()

    def _temperature_c(self) -> float | None:
        state = self.hass.states.get(self._temperature_entity_id)
        if state is None or state.state in (None, "unknown", "unavailable"):
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        if state.attributes.get("unit_of_measurement") == "°F":
            value = (value - 32) * 5 / 9
        return value

    def _result(self) -> dict | None:
        params = self.coordinator.data["parameters"]
        ph = (params.get("ph") or {}).get("result")
        ta = (params.get("ta") or {}).get("result")
        ch = (params.get("ch") or {}).get("result")
        # Salt/TDS not required here, unlike the on-demand calculate_lsi
        # service - a missing salt reading just contributes 0 to the
        # formula rather than blocking the sensor from working at all.
        tds = (params.get("salt") or {}).get("result") or 0
        temp_c = self._temperature_c()
        if None in (ph, ta, ch, temp_c):
            return None
        return calculate_lsi(ph, temp_c, ta, ch, tds)

    @property
    def native_value(self):
        result = self._result()
        return result["lsi"] if result else None

    @property
    def extra_state_attributes(self):
        result = self._result()
        if not result:
            return {}
        return {
            "saturation_ph": result["saturation_ph"],
            "status": result["status"],
            "water_temperature_c": self._temperature_c(),
        }
