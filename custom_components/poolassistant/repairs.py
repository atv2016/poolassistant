"""Repair flows for Pool Assistant."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant


class PoolDeletedRepairFlow(RepairsFlow):
    """Guides removing an HA entry for a pool that no longer exists in the account."""

    def __init__(self, entry_id: str, name: str) -> None:
        self._entry_id = entry_id
        self._name = name

    async def async_step_init(self, user_input=None):
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            await self.hass.config_entries.async_remove(self._entry_id)
            return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"name": self._name},
        )


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict | None):
    data = data or {}
    return PoolDeletedRepairFlow(data.get("entry_id"), data.get("name", "This pool"))
