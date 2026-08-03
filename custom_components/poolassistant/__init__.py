import base64
import logging
import mimetypes
from pathlib import Path

import voluptuous as vol

from homeassistant.components import media_source
import homeassistant.helpers.issue_registry as ir
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url
from homeassistant.helpers.update_coordinator import UpdateFailed

from .firebase import FirebaseAuth
from .coordinator import PoolAssistantCoordinator, most_recent_device_reading
from .firestore_write import now_ms, new_id, async_create_pool_document
from .lsi import calculate_lsi
from .volume import calculate_pool_volume
from .const import DOMAIN, PARAMETERS, METHOD_INFO, OPTION_SURFACE_TOOLTIP_IDS

_LOGGER = logging.getLogger(__name__)

SERVICE_ADD_CHEMICAL = "add_chemical"
SERVICE_LOG_READING = "log_reading"

ADD_CHEMICAL_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Required("result_id"): cv.string,
    vol.Required("chemical"): cv.string,
    vol.Required("amount"): vol.Coerce(float),
    vol.Required("unit"): cv.string,
})

LOG_READING_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Required("parameter"): vol.In(PARAMETERS.keys()),
    vol.Required("result"): vol.Coerce(float),
    vol.Optional("note"): cv.string,
})

SET_VOLUME_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Required("volume_gallons"): vol.Coerce(float),
})

CREATE_POOL_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Required("name"): cv.string,
    vol.Optional("basin_type", default="pool"): cv.string,
    vol.Required("volume_gallons"): vol.Coerce(float),
    vol.Optional("surface_material", default=""): cv.string,
    vol.Optional("city", default=""): cv.string,
    vol.Optional("heater", default=False): cv.boolean,
    vol.Optional("water_profile", default="americas"): cv.string,
    vol.Optional("image_file_path"): cv.string,
    vol.Optional("image_base64"): cv.string,
    vol.Optional("image"): dict,  # selector: media -> {"media_content_id": ..., "media_content_type": ...}
})

CALCULATE_LSI_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Required("water_temperature_c"): vol.Coerce(float),
    vol.Optional("ph"): vol.Coerce(float),
    vol.Optional("total_alkalinity"): vol.Coerce(float),
    vol.Optional("calcium_hardness"): vol.Coerce(float),
    vol.Optional("tds"): vol.Coerce(float),
})

CALCULATE_VOLUME_SCHEMA = vol.Schema({
    vol.Required("shape"): vol.In(["rectangular", "round", "oval", "kidney"]),
    vol.Optional("unit", default="feet"): vol.In(["feet", "meters"]),
    vol.Optional("length"): vol.Coerce(float),
    vol.Optional("width"): vol.Coerce(float),
    vol.Optional("diameter"): vol.Coerce(float),
    vol.Optional("diameter_2"): vol.Coerce(float),
    vol.Optional("average_depth"): vol.Coerce(float),
    vol.Optional("shallow_depth"): vol.Coerce(float),
    vol.Optional("deep_depth"): vol.Coerce(float),
})

DELETE_READING_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Required("result_id"): cv.string,
})

EDIT_READING_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Required("result_id"): cv.string,
    vol.Required("new_result"): vol.Coerce(float),
})

SET_POOL_IMAGE_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Optional("image_file_path"): cv.string,
    vol.Optional("image_base64"): cv.string,
    vol.Optional("image"): dict,
})

CLEAR_POOL_IMAGE_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
})


def _get_coordinator(hass: HomeAssistant, config_entry_id: str) -> PoolAssistantCoordinator:
    entry_data = hass.data[DOMAIN].get(config_entry_id)
    if entry_data is None:
        raise ValueError(f"Unknown Pool Assistant config entry: {config_entry_id}")
    return entry_data["coordinator"]


async def _async_resolve_image(
    hass: HomeAssistant,
    session,
    file_path: str | None,
    image_base64: str | None,
    media: dict | None,
) -> str:
    """Build a 'data:<mime>;base64,...' string from a Media Browser pick, a
    file path, or raw base64 data - checked in that priority order."""
    if media and media.get("media_content_id"):
        played = await media_source.async_resolve_media(hass, media["media_content_id"], None)
        url = played.url
        if url.startswith("/"):
            url = get_url(hass) + url
        async with session.get(url) as resp:
            if resp.status != 200:
                raise HomeAssistantError(f"Could not fetch selected image: HTTP {resp.status}")
            data = await resp.read()
        mime = played.mime_type or "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    if file_path:
        path = Path(file_path)
        if not path.is_file():
            raise HomeAssistantError(f"Image file not found: {file_path}")
        mime, _ = mimetypes.guess_type(path.name)
        mime = mime or "image/jpeg"
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{data}"

    if image_base64:
        # Accept either a bare base64 string or an already-formed data URI.
        return image_base64 if image_base64.startswith("data:") else f"data:image/jpeg;base64,{image_base64}"

    return ""


def _device_info_from_existing_readings(coordinator) -> dict:
    """Borrow serialNumber/firmwareVersion from the most recently logged
    device-sourced reading on this pool, so a manually logged entry
    carries the current device's identity - not a stale one."""
    reading = most_recent_device_reading(coordinator)
    if reading is None:
        return {}
    return {
        "serialNumber": reading["serialNumber"],
        "firmwareVersion": reading.get("firmwareVersion", ""),
    }

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

async def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_ADD_CHEMICAL):
        return  # already registered by a previous config entry

    async def handle_add_chemical(call):
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        chemical = {
            "name": call.data["chemical"],
            "amount": int(call.data["amount"]),
            "unit": call.data["unit"],
            "time": now_ms(),
        }
        try:
            await coordinator.async_add_chemical_to_reading(call.data["result_id"], chemical)
        except UpdateFailed as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_log_reading(call):
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        key = call.data["parameter"]
        result_id = new_id()
        unit = (PARAMETERS[key].get("unit") or "").lower()
        reading = {
            "appSymbol": key,
            "result": call.data["result"],
            "resultId": result_id,
            "uniqueID": result_id,
            "manual": True,
            "time": now_ms(),
            "unit": unit,
        }
        # Optional/experimental: fill in the method fields the app uses to
        # render its clickable test-ID tooltip, for parameters we've
        # confirmed real codes for. Off by default - see the integration's
        # options (Configure) to enable it.
        if coordinator.entry.options.get(OPTION_SURFACE_TOOLTIP_IDS):
            method = METHOD_INFO.get(key)
            if method:
                reading.update(method)
                reading.update(_device_info_from_existing_readings(coordinator))

        datapoint = {"parameters": {key: reading}}
        if call.data.get("note"):
            datapoint["notes"] = call.data["note"]

        await coordinator.async_append_datapoint(datapoint)
        return {"result_id": result_id}

    async def handle_set_volume(call):
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        await coordinator.async_update_document_fields(
            {"volumeGallons": call.data["volume_gallons"]}
        )

    async def handle_create_pool(call):
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        firebase = coordinator.firebase

        image_data_uri = await _async_resolve_image(
            hass, coordinator.session,
            call.data.get("image_file_path"), call.data.get("image_base64"), call.data.get("image"),
        )

        fields = {
            "name": call.data["name"],
            "volumeGallons": call.data["volume_gallons"],
            "basinType": call.data["basin_type"],
            "surfaceMaterial": call.data["surface_material"],
            "heater": call.data["heater"],
            "cityName": call.data["city"],
            "image": image_data_uri,
            "datapoints": [],
            "profile": {
                "symbolOrder": [],
                "waterProfileId": call.data["water_profile"],
                "waterProfileName": "",
            },
        }
        try:
            pool_id = await async_create_pool_document(
                coordinator.session, firebase, coordinator.entry.data["project_id"], fields
            )
        except RuntimeError as err:
            raise HomeAssistantError(str(err)) from err

        # This is a deliberate, user-initiated action, not something HA
        # noticed passively - so it goes straight into a real entry, no
        # "Discovered" card. That's reserved for the coordinator's own
        # background check for pools created directly in the app.
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data={
                    "email": coordinator.entry.data["email"],
                    "password": coordinator.entry.data["password"],
                    "api_key": coordinator.entry.data["api_key"],
                    "project_id": coordinator.entry.data["project_id"],
                    "pool_id": pool_id,
                    "name": call.data["name"],
                },
            )
        )

        return {"pool_id": pool_id}

    async def handle_calculate_lsi(call):
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        params = coordinator.data["parameters"]

        def latest(key):
            return (params.get(key) or {}).get("result")

        ph = call.data.get("ph", latest("ph"))
        ta = call.data.get("total_alkalinity", latest("ta"))
        ch = call.data.get("calcium_hardness", latest("ch"))
        tds = call.data.get("tds", latest("salt"))

        missing = [n for n, v in (("ph", ph), ("total_alkalinity", ta), ("calcium_hardness", ch), ("tds", tds)) if v is None]
        if missing:
            raise HomeAssistantError(
                f"Missing values for LSI calculation and no tracked reading available: {', '.join(missing)}"
            )

        return calculate_lsi(ph, call.data["water_temperature_c"], ta, ch, tds)

    async def handle_calculate_volume(call):
        try:
            return calculate_pool_volume(
                shape=call.data["shape"],
                unit=call.data["unit"],
                length=call.data.get("length"),
                width=call.data.get("width"),
                diameter=call.data.get("diameter"),
                diameter_2=call.data.get("diameter_2"),
                average_depth=call.data.get("average_depth"),
                shallow_depth=call.data.get("shallow_depth"),
                deep_depth=call.data.get("deep_depth"),
            )
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_delete_reading(call):
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        try:
            await coordinator.async_delete_reading(call.data["result_id"])
        except UpdateFailed as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_edit_reading(call):
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        try:
            await coordinator.async_edit_reading(call.data["result_id"], call.data["new_result"])
        except UpdateFailed as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_set_pool_image(call):
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        image_data_uri = await _async_resolve_image(
            hass, coordinator.session,
            call.data.get("image_file_path"), call.data.get("image_base64"), call.data.get("image"),
        )
        if not image_data_uri:
            raise HomeAssistantError("Provide an image via the media picker, image_file_path, or image_base64")
        await coordinator.async_update_document_fields({"image": image_data_uri})

    async def handle_clear_pool_image(call):
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        await coordinator.async_update_document_fields({"image": ""})

    hass.services.async_register(DOMAIN, "add_chemical", handle_add_chemical, schema=ADD_CHEMICAL_SCHEMA)
    hass.services.async_register(
        DOMAIN, "log_reading", handle_log_reading,
        schema=LOG_READING_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, "set_volume", handle_set_volume, schema=SET_VOLUME_SCHEMA)
    hass.services.async_register(
        DOMAIN, "create_pool", handle_create_pool,
        schema=CREATE_POOL_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, "calculate_lsi", handle_calculate_lsi,
        schema=CALCULATE_LSI_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, "calculate_volume", handle_calculate_volume,
        schema=CALCULATE_VOLUME_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, "delete_reading", handle_delete_reading, schema=DELETE_READING_SCHEMA)
    hass.services.async_register(DOMAIN, "edit_reading", handle_edit_reading, schema=EDIT_READING_SCHEMA)
    hass.services.async_register(DOMAIN, "set_pool_image", handle_set_pool_image, schema=SET_POOL_IMAGE_SCHEMA)
    hass.services.async_register(DOMAIN, "clear_pool_image", handle_clear_pool_image, schema=CLEAR_POOL_IMAGE_SCHEMA)

PLATFORMS = ["sensor", "image", "button", "binary_sensor"]

async def async_setup(hass: HomeAssistant, config: dict):
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    session = async_get_clientsession(hass)
    firebase = FirebaseAuth(
        session=session,
        api_key=entry.data["api_key"],
        email=entry.data["email"],
        password=entry.data["password"],
    )
    try:
        await firebase.get_token()
    except Exception as err:
        raise ConfigEntryAuthFailed("Pool Assistant login failed") from err
    _LOGGER.debug("Pool Assistant login OK (local_id=%s)", firebase.local_id)

    coordinator = PoolAssistantCoordinator(hass, firebase, session, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "firebase": firebase,
    }

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry whenever its options change, so poll interval,
    auto-discovery toggle, ideal-range overrides, etc. all take effect
    immediately instead of needing a manual restart."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        ir.async_delete_issue(hass, DOMAIN, f"pool_deleted_{entry.entry_id}")
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
