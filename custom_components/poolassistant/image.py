"""Image platform for Pool Assistant - shows the pool photo set in the app."""
import base64
import binascii
import logging

from homeassistant.components.image import ImageEntity
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import build_device_info

_LOGGER = logging.getLogger(__name__)


def _decode_data_uri(data_uri: str):
    """Split 'data:<mime>;base64,<data>' into (mime, decoded bytes)."""
    try:
        header, encoded = data_uri.split(",", 1)
        mime = header.split(";")[0].removeprefix("data:") or "image/jpeg"
        return mime, base64.b64decode(encoded)
    except (ValueError, binascii.Error) as err:
        _LOGGER.debug("Could not decode pool photo: %s", err)
        return None, None


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    device_info = build_device_info(coordinator, entry)
    async_add_entities([PoolPhotoImage(hass, coordinator, entry, device_info)])


class PoolPhotoImage(CoordinatorEntity, ImageEntity):
    """The pool photo from the app (e.g. after using 'Update photo')."""

    _attr_name = "Pool Photo"

    def __init__(self, hass, coordinator, entry, device_info):
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{entry.entry_id}_photo"
        self._attr_device_info = device_info
        self._image_bytes: bytes | None = None
        self._refresh_from_data()

    def _refresh_from_data(self) -> None:
        raw = self.coordinator.data["pool"].get("image")
        if not raw:
            self._image_bytes = None
            return
        mime, decoded = _decode_data_uri(raw)
        if decoded is None:
            return
        self._image_bytes = decoded
        self._attr_content_type = mime
        self._attr_image_last_updated = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_from_data()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        return self._image_bytes
