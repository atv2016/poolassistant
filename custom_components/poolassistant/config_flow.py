from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT, SOURCE_DISCOVERY
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    PARAMETERS,
    OPTION_SURFACE_TOOLTIP_IDS,
    OPTION_IDEAL_RANGES,
    OPTION_TEMPERATURE_ENTITY,
    OPTION_POLL_INTERVAL_MINUTES,
    OPTION_DISABLE_AUTO_DISCOVERY,
    DEFAULT_POLL_INTERVAL_MINUTES,
)
from .firebase import FirebaseAuth
from .firestore_write import async_create_pool_document, async_list_pools

_LOGGER = logging.getLogger(__name__)

# These identify the Pool Assistant app's own Firebase backend - not secrets
# (a Firebase web API key only names the project; it doesn't grant
# privileged access by itself), and the same for every user of the app.
# Confirmed from repeated network captures of the live app.
DEFAULT_API_KEY = "AIzaSyCy0s4Mc3SDvTYuz8KuvJ2g6AiJWb-V-58"
DEFAULT_PROJECT_ID = "poolassistant-b95b1"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
        vol.Required("api_key", default=DEFAULT_API_KEY): str,
        vol.Required("project_id", default=DEFAULT_PROJECT_ID): str,
    }
)

NEW_POOL_OPTION = "__create_new__"


class PoolAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._data: dict = {}
        self._pools: list[dict] = []
        self._reauth_entry: ConfigEntry | None = None
        self._discovery_data: dict = {}

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            firebase = FirebaseAuth(
                session=session,
                api_key=user_input["api_key"],
                email=user_input["email"],
                password=user_input["password"],
            )
            try:
                await firebase.login()
            except Exception:
                errors["base"] = "auth_failed"
            else:
                try:
                    pools = await async_list_pools(session, user_input["project_id"], firebase)
                except Exception:
                    errors["base"] = "cannot_connect"
                else:
                    self._data = user_input
                    self._pools = pools
                    # No pools on this account yet - skip straight to
                    # creating one instead of dead-ending on an error.
                    if not pools:
                        return await self.async_step_new_pool()
                    return await self.async_step_pool()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_pool(self, user_input=None):
        if user_input is not None:
            selected = user_input["pool_id"]
            create_new = NEW_POOL_OPTION in selected
            existing_selected = [p for p in selected if p != NEW_POOL_OPTION]

            if not existing_selected and not create_new:
                return self._show_pool_form(errors={"base": "no_selection"})

            configured_ids = {
                entry.data.get("pool_id")
                for entry in self.hass.config_entries.async_entries(DOMAIN)
            }
            # Filter out already-configured pools before deciding who's
            # "foreground" vs "background" - so the outcome is consistent
            # regardless of which pool happened to be ticked/listed first.
            new_pool_ids = [p for p in existing_selected if p not in configured_ids]

            if not new_pool_ids and not create_new:
                return self._show_pool_form(errors={"base": "already_configured_selection"})

            # Every new pool beyond the first (and all of them, if
            # "create new" was also ticked) gets its own entry created
            # directly in the background - a single flow run can only
            # finish with one entry via async_create_entry.
            tail_start = 0 if create_new else 1
            for extra_pool_id in new_pool_ids[tail_start:]:
                pool_name = next(
                    (p["name"] for p in self._pools if p["pool_id"] == extra_pool_id),
                    "Pool Assistant",
                )
                self.hass.async_create_task(
                    self.hass.config_entries.flow.async_init(
                        DOMAIN,
                        context={"source": SOURCE_IMPORT},
                        data={**self._data, "pool_id": extra_pool_id, "name": pool_name},
                    )
                )

            if create_new:
                return await self.async_step_new_pool()

            pool_id = new_pool_ids[0]
            pool_name = next(
                (p["name"] for p in self._pools if p["pool_id"] == pool_id), "Pool Assistant"
            )
            await self.async_set_unique_id(pool_id, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=pool_name,
                data={**self._data, "pool_id": pool_id},
            )

        return self._show_pool_form()

    def _show_pool_form(self, errors=None):
        configured_ids = {
            entry.data.get("pool_id")
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        }
        options = {}
        for p in self._pools:
            label = f"✅ {p['name']} (already added)" if p["pool_id"] in configured_ids else p["name"]
            options[p["pool_id"]] = label
        options[NEW_POOL_OPTION] = "+ Create a new pool"
        schema = vol.Schema({vol.Required("pool_id"): cv.multi_select(options)})
        return self.async_show_form(step_id="pool", data_schema=schema, errors=errors or {})

    async def async_step_new_pool(self, user_input=None):
        errors = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            firebase = FirebaseAuth(
                session=session,
                api_key=self._data["api_key"],
                email=self._data["email"],
                password=self._data["password"],
            )
            fields = {
                "name": user_input["name"],
                "volumeGallons": user_input["volume_gallons"],
                "basinType": "pool",
                "surfaceMaterial": "",
                "heater": False,
                "cityName": "",
                "image": "",
                "datapoints": [],
                "profile": {
                    "symbolOrder": [],
                    "waterProfileId": "americas",
                    "waterProfileName": "",
                },
            }
            try:
                pool_id = await async_create_pool_document(
                    session, firebase, self._data["project_id"], fields
                )
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(pool_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input["name"],
                    data={**self._data, "pool_id": pool_id},
                )

        return self.async_show_form(
            step_id="new_pool",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required("volume_gallons"): vol.Coerce(float),
                }
            ),
            errors=errors,
        )

    async def async_step_import(self, import_data: dict):
        """Create a config entry directly, no confirmation card - used by
        the pool-picker step when multiple existing pools are ticked at
        once, and by create_pool for the pool it just created. In both
        cases the user already gave explicit, in-the-moment consent, so
        there's nothing left to confirm - and this can override a stale
        pending Discovered card for the same pool."""
        await self.async_set_unique_id(import_data["pool_id"], raise_on_progress=False)
        self._abort_if_unique_id_configured()
        name = import_data.pop("name")
        return self.async_create_entry(title=name, data=import_data)

    async def async_step_discovery(self, discovery_info: dict):
        """A pool the coordinator noticed was created directly in the app -
        something HA genuinely discovered passively, unlike create_pool or
        the multi-select picker where the user already told us directly.
        Shows up as its own 'Discovered' card rather than appearing silently.
        """
        await self.async_set_unique_id(discovery_info["pool_id"])
        self._abort_if_unique_id_configured()
        self._discovery_data = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info["name"]}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input=None):
        if user_input is not None:
            name = self._discovery_data.pop("name")
            return self.async_create_entry(title=name, data=self._discovery_data)
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"name": self._discovery_data["name"]},
        )

    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            firebase = FirebaseAuth(
                session=session,
                api_key=self._reauth_entry.data["api_key"],
                email=self._reauth_entry.data["email"],
                password=user_input["password"],
            )
            try:
                await firebase.login()
            except Exception:
                errors["base"] = "auth_failed"
            else:
                new_data = {**self._reauth_entry.data, "password": user_input["password"]}
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=new_data)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required("password"): str}),
            errors=errors,
            description_placeholders={"email": self._reauth_entry.data["email"]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "PoolAssistantOptionsFlow":
        return PoolAssistantOptionsFlow()


def _build_range_schema_fields(current_overrides: dict) -> dict:
    """One optional low/high number field per parameter. Only pre-filled
    when a real override already exists for that parameter - fields with
    no override start genuinely blank (the built-in default is shown in
    the field's own label instead), so 'leave blank to use the default'
    is actually true, and just opening this form and hitting Submit
    doesn't silently turn every default into a stored override.
    """
    fields = {}
    for key in PARAMETERS:
        override = current_overrides.get(key)
        low_suggested = override[0] if override else None
        high_suggested = override[1] if override else None
        fields[
            vol.Optional(f"{key}_low", description={"suggested_value": low_suggested})
        ] = vol.Coerce(float)
        fields[
            vol.Optional(f"{key}_high", description={"suggested_value": high_suggested})
        ] = vol.Coerce(float)
    return fields


def _extract_range_overrides(user_input: dict) -> dict:
    overrides = {}
    for key in PARAMETERS:
        low = user_input.get(f"{key}_low")
        high = user_input.get(f"{key}_high")
        if low is not None and high is not None:
            overrides[key] = [low, high]
    return overrides


class PoolAssistantOptionsFlow(config_entries.OptionsFlow):
    """Integration-level settings, reached via the entry's 'Configure' button.

    Grouped into collapsible sections (each with its own short heading and
    one-line description) instead of one long paragraph above a flat list
    of fields - the Ideal Ranges section starts collapsed since it's 24
    fields, the rest start open since they're each just one field.

    Deliberately no self.config_entry assignment - modern Home Assistant
    core injects that itself, and setting it manually raises an
    AttributeError (which previously surfaced as a 500 error).
    """

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            tooltip = user_input.get("tooltip_section", {})
            polling = user_input.get("polling_section", {})
            discovery = user_input.get("discovery_section", {})
            temperature = user_input.get("temperature_section", {})
            ranges = user_input.get("ideal_ranges", {})

            data = {
                OPTION_SURFACE_TOOLTIP_IDS: tooltip.get(OPTION_SURFACE_TOOLTIP_IDS, False),
                OPTION_POLL_INTERVAL_MINUTES: polling.get(
                    OPTION_POLL_INTERVAL_MINUTES, DEFAULT_POLL_INTERVAL_MINUTES
                ),
                OPTION_DISABLE_AUTO_DISCOVERY: discovery.get(OPTION_DISABLE_AUTO_DISCOVERY, False),
            }
            if temperature.get(OPTION_TEMPERATURE_ENTITY):
                data[OPTION_TEMPERATURE_ENTITY] = temperature[OPTION_TEMPERATURE_ENTITY]

            overrides = _extract_range_overrides(ranges)
            if overrides:
                data[OPTION_IDEAL_RANGES] = overrides

            return self.async_create_entry(title="", data=data)

        current = self.config_entry.options
        current_overrides = current.get(OPTION_IDEAL_RANGES, {})

        schema = vol.Schema(
            {
                vol.Required("tooltip_section"): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                OPTION_SURFACE_TOOLTIP_IDS,
                                default=current.get(OPTION_SURFACE_TOOLTIP_IDS, False),
                            ): bool,
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required("polling_section"): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                OPTION_POLL_INTERVAL_MINUTES,
                                default=current.get(
                                    OPTION_POLL_INTERVAL_MINUTES, DEFAULT_POLL_INTERVAL_MINUTES
                                ),
                            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required("discovery_section"): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                OPTION_DISABLE_AUTO_DISCOVERY,
                                default=current.get(OPTION_DISABLE_AUTO_DISCOVERY, False),
                            ): bool,
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required("temperature_section"): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                OPTION_TEMPERATURE_ENTITY,
                                description={"suggested_value": current.get(OPTION_TEMPERATURE_ENTITY)},
                            ): selector.EntitySelector(
                                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                            ),
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required("ideal_ranges"): section(
                    vol.Schema(_build_range_schema_fields(current_overrides)),
                    {"collapsed": True},
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
