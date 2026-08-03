import logging
from datetime import datetime, timedelta, timezone

import homeassistant.helpers.issue_registry as ir
from homeassistant.config_entries import SOURCE_DISCOVERY, SOURCE_REAUTH
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    DOMAIN,
    PARAMETERS,
    OPTION_POLL_INTERVAL_MINUTES,
    OPTION_DISABLE_AUTO_DISCOVERY,
    DEFAULT_POLL_INTERVAL_MINUTES,
)
from .firestore import parse_fields

_LOGGER = logging.getLogger(__name__)


def ms_to_datetime(ms):
    """Convert a Firestore epoch-millisecond timestamp into a UTC datetime."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def most_recent_device_reading(coordinator) -> dict | None:
    """The most recently logged device-sourced (non-manual) reading across
    every tracked parameter, by its own 'time' field - not dict iteration
    order, which follows PARAMETERS's fixed key order rather than recency.
    Returns None if the pool has no device-sourced readings at all.

    Used both for the manual-entry tooltip feature (borrowing serial/
    firmware so a manual entry looks like it came from the same device)
    and for populating the HA device page's model/firmware.
    """
    best = None
    for reading in coordinator.data["parameters"].values():
        if not reading or reading.get("manual") or not reading.get("serialNumber"):
            continue
        if best is None or (reading.get("time") or 0) > (best.get("time") or 0):
            best = reading
    return best


def build_device_info(coordinator, entry) -> dict:
    """Device info shared by every platform (sensor/image/button/binary_sensor).
    Model and firmware come from the most recently logged device-sourced
    reading, when one exists, so the HA device page reflects the real
    Scuba3s rather than a generic placeholder.
    """
    reading = most_recent_device_reading(coordinator)
    info = {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": coordinator.data["pool"]["name"],
        "manufacturer": "Lovibond",
        "model": "Scuba3s" if reading else "Pool Assistant",
    }
    if reading:
        if reading.get("firmwareVersion"):
            info["sw_version"] = reading["firmwareVersion"]
        if reading.get("serialNumber"):
            info["serial_number"] = reading["serialNumber"]
    return info


class PoolAssistantCoordinator(DataUpdateCoordinator):
    """Fetches the pool document from Firestore and builds a clean data model."""

    def __init__(self, hass, firebase, session, entry):
        self.firebase = firebase
        self.session = session
        self.entry = entry
        poll_minutes = entry.options.get(OPTION_POLL_INTERVAL_MINUTES, DEFAULT_POLL_INTERVAL_MINUTES)
        super().__init__(
            hass,
            _LOGGER,
            name="Pool Assistant",
            update_interval=timedelta(minutes=poll_minutes),
        )

    def _pool_deleted_issue_id(self) -> str:
        return f"pool_deleted_{self.entry.entry_id}"

    def _create_pool_deleted_issue(self) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._pool_deleted_issue_id(),
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="pool_deleted",
            translation_placeholders={"name": self.entry.title},
            data={"entry_id": self.entry.entry_id, "name": self.entry.title},
        )

    def _clear_pool_deleted_issue(self) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, self._pool_deleted_issue_id())

    async def _get_token_or_reauth(self) -> str:
        """Get a valid Firebase token for a write operation. Unlike the
        poll cycle (where DataUpdateCoordinator's own ConfigEntryAuthFailed
        handling triggers reauth automatically), a service-call write sits
        outside that mechanism, so a dead token here has to kick off the
        reauth flow explicitly instead of just failing quietly - this
        mirrors what the poll cycle's own auth-failure handling does
        internally, just triggered manually.
        """
        try:
            return await self.firebase.get_token()
        except Exception as err:
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": SOURCE_REAUTH, "entry_id": self.entry.entry_id},
                    data=self.entry.data,
                )
            )
            raise UpdateFailed("Pool Assistant re-authentication required") from err

    async def _async_update_data(self):
        try:
            try:
                token = await self.firebase.get_token()
            except Exception as err:
                raise ConfigEntryAuthFailed("Pool Assistant re-authentication required") from err

            headers = {"Authorization": f"Bearer {token}"}
            url = (
                "https://firestore.googleapis.com/v1/"
                "projects/"
                f"{self.entry.data['project_id']}/"
                "databases/(default)/documents/"
                "users/"
                f"{self.firebase.local_id}/"
                "allPools/"
                f"{self.entry.data['pool_id']}"
            )
            async with self.session.get(url, headers=headers) as resp:
                if resp.status == 404:
                    self._create_pool_deleted_issue()
                    raise UpdateFailed(
                        "This pool no longer exists in your Pool Assistant account "
                        "(see Settings > Repairs for how to remove it)."
                    )
                if resp.status != 200:
                    text = await resp.text()
                    raise UpdateFailed(f"Firestore returned HTTP {resp.status}: {text}")
                raw = await resp.json()
        except (UpdateFailed, ConfigEntryAuthFailed):
            raise
        except Exception as err:
            raise UpdateFailed(f"Pool Assistant update failed: {err}") from err

        self._clear_pool_deleted_issue()
        if not self.entry.options.get(OPTION_DISABLE_AUTO_DISCOVERY, False):
            self.hass.async_create_task(self.async_discover_new_pools())
        return self._build_model(raw)

    def _build_model(self, raw: dict) -> dict:
        """Turn the raw Firestore document into the shape every entity reads from:

        {
            "pool": {...},
            "parameters": {key: {result, timestamp, source, ...} or {}},
            "chemicals": [...],
            "notes": [...],
            "water_quality": [...],
            "raw": {...},  # full parsed document, for debugging/future phases
        }
        """
        doc = parse_fields(raw.get("fields", {}))

        pool = {
            "name": doc.get("name", "Pool"),
            "volume_gallons": doc.get("volumeGallons"),
            "surface": doc.get("surfaceMaterial"),
            "heater": doc.get("heater"),
            "city": doc.get("cityName"),
            "basin_type": doc.get("basinType"),
            "water_profile": (doc.get("profile") or {}).get("waterProfileId"),
            "image": doc.get("image"),  # raw "data:image/...;base64,..." string, or ""
        }

        datapoints = doc.get("datapoints") or []

        # Each "datapoint" is a loose event bundle - it may contain any mix of
        # parameters / chemicals / waterQuality / notes, not neat separate
        # collections. So we flatten across all of them rather than assuming
        # a fixed per-datapoint shape. We also compare 'time' across every
        # reading found (not just walk order), since a single synced
        # datapoint (e.g. from an NFC device sync) can bundle several
        # readings that were actually measured on different days.
        latest_parameters: dict[str, dict] = {}
        chemicals: list[dict] = []
        water_quality: list[dict] = []
        notes: list[str] = []

        for datapoint in datapoints:
            if not isinstance(datapoint, dict):
                continue

            for key, reading in (datapoint.get("parameters") or {}).items():
                if not isinstance(reading, dict):
                    continue
                existing = latest_parameters.get(key)
                if existing is None or (reading.get("time") or 0) > (existing.get("time") or 0):
                    latest_parameters[key] = reading

            chemicals.extend(c for c in (datapoint.get("chemicals") or []) if isinstance(c, dict))
            water_quality.extend(w for w in (datapoint.get("waterQuality") or []) if isinstance(w, dict))

            note = datapoint.get("notes")
            if note:
                notes.append(note)

        chemicals.sort(key=lambda c: c.get("time") or 0, reverse=True)
        water_quality.sort(key=lambda w: w.get("time") or 0, reverse=True)

        parameters = {key: latest_parameters.get(key, {}) for key in PARAMETERS}

        return {
            "pool": pool,
            "parameters": parameters,
            "chemicals": chemicals,
            "water_quality": water_quality,
            "notes": notes,
            "raw": doc,
        }

    # ---------------------------------------------------------------
    # Discovering pools created directly in the app
    # ---------------------------------------------------------------

    async def async_discover_new_pools(self) -> None:
        """Check this Firebase account for pools that exist in Firestore
        but have no Home Assistant entry yet - e.g. created directly in
        the app - and offer each one up as a 'Discovered' card, the same
        way create_pool's own new pool does. Runs on every poll (unless
        disabled via options), so a pool added in the app eventually gets
        noticed without anyone touching HA.
        """
        from .firestore_write import async_list_pools  # local import avoids a cycle

        try:
            pools = await async_list_pools(
                self.session, self.entry.data["project_id"], self.firebase
            )
        except Exception:
            return  # not worth failing the whole poll over - just skip this round

        configured_pool_ids = {
            entry.data.get("pool_id") for entry in self.hass.config_entries.async_entries(DOMAIN)
        }

        for pool in pools:
            if pool["pool_id"] in configured_pool_ids:
                continue
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": SOURCE_DISCOVERY},
                    data={
                        "email": self.entry.data["email"],
                        "password": self.entry.data["password"],
                        "api_key": self.entry.data["api_key"],
                        "project_id": self.entry.data["project_id"],
                        "pool_id": pool["pool_id"],
                        "name": pool["name"],
                    },
                )
            )

    # ---------------------------------------------------------------
    # Writes: appends (array-union transform)
    # ---------------------------------------------------------------

    async def async_append_datapoint(self, datapoint: dict) -> None:
        """Append a single datapoint to this pool's history via an array-union transform.

        This mirrors what the app does when you log a manual reading - it
        just appends one small object to the 'datapoints' array - but does
        it as an atomic server-side transform instead of rewriting the whole
        document, so it won't race with the app if it's open on your phone
        at the same time.
        """
        from .firestore_write import to_firestore_value  # local import avoids a cycle

        token = await self._get_token_or_reauth()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        document = (
            f"projects/{self.entry.data['project_id']}/databases/(default)/documents/"
            f"users/{self.firebase.local_id}/allPools/{self.entry.data['pool_id']}"
        )
        url = (
            f"https://firestore.googleapis.com/v1/projects/{self.entry.data['project_id']}"
            "/databases/(default)/documents:commit"
        )
        body = {
            "writes": [
                {
                    "transform": {
                        "document": document,
                        "fieldTransforms": [
                            {
                                "fieldPath": "datapoints",
                                "appendMissingElements": {
                                    "values": [to_firestore_value(datapoint)]
                                },
                            }
                        ],
                    }
                }
            ]
        }
        async with self.session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise UpdateFailed(f"Pool Assistant write failed: HTTP {resp.status}: {text}")

        # Pull the change back down immediately so entities update without
        # waiting for the next poll.
        await self.async_request_refresh()

    # ---------------------------------------------------------------
    # Writes: masked patch of top-level fields (e.g. volume)
    # ---------------------------------------------------------------

    async def async_update_document_fields(self, fields: dict) -> None:
        """Patch specific top-level document fields (e.g. volume, name, city).

        Unlike async_append_datapoint (an array-union transform), this uses a
        masked field update - only the given field paths are touched, leaving
        the rest of the document (datapoints, etc.) untouched. This mirrors
        what the app sends when you edit a pool's settings directly, e.g.
        recalculating volume.
        """
        from .firestore_write import to_firestore_value

        token = await self._get_token_or_reauth()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        document = (
            f"projects/{self.entry.data['project_id']}/databases/(default)/documents/"
            f"users/{self.firebase.local_id}/allPools/{self.entry.data['pool_id']}"
        )
        url = (
            f"https://firestore.googleapis.com/v1/projects/{self.entry.data['project_id']}"
            "/databases/(default)/documents:commit"
        )
        body = {
            "writes": [
                {
                    "update": {
                        "name": document,
                        "fields": {k: to_firestore_value(v) for k, v in fields.items()},
                    },
                    "updateMask": {"fieldPaths": list(fields.keys())},
                }
            ]
        }
        async with self.session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise UpdateFailed(f"Pool Assistant write failed: HTTP {resp.status}: {text}")

        await self.async_request_refresh()

    # ---------------------------------------------------------------
    # Writes: full-document overwrite (delete/edit a reading, attach a chemical)
    # ---------------------------------------------------------------

    async def _fetch_raw_doc_fields(self) -> tuple[dict, str | None]:
        """Fetch the pool document. Returns (fields, update_time) - the
        parsed field dict, and Firestore's own 'updateTime' for the
        document as it stood at the moment of this read. update_time lets
        a subsequent write assert nothing has touched the document since,
        instead of blindly overwriting a concurrent edit from the app.
        """
        token = await self._get_token_or_reauth()
        headers = {"Authorization": f"Bearer {token}"}
        url = (
            f"https://firestore.googleapis.com/v1/projects/{self.entry.data['project_id']}"
            "/databases/(default)/documents/users/"
            f"{self.firebase.local_id}/allPools/{self.entry.data['pool_id']}"
        )
        async with self.session.get(url, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise UpdateFailed(f"Firestore returned HTTP {resp.status}: {text}")
            raw = await resp.json()
        return parse_fields(raw.get("fields", {})), raw.get("updateTime")

    async def _write_full_document(self, doc_fields: dict, update_time: str | None = None) -> None:
        """Overwrite the entire document - the same mechanism the app itself
        uses for deleting a reading or creating a pool (a bare 'update' with
        no updateMask). If update_time is given, the write is conditioned on
        the document not having changed since that time - if the app (or
        another HA write) touched it in between, Firestore rejects the write
        outright instead of silently clobbering whatever changed.
        """
        from .firestore_write import to_firestore_value

        token = await self._get_token_or_reauth()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        document = (
            f"projects/{self.entry.data['project_id']}/databases/(default)/documents/"
            f"users/{self.firebase.local_id}/allPools/{self.entry.data['pool_id']}"
        )
        url = (
            f"https://firestore.googleapis.com/v1/projects/{self.entry.data['project_id']}"
            "/databases/(default)/documents:commit"
        )
        write = {
            "update": {
                "name": document,
                "fields": {k: to_firestore_value(v) for k, v in doc_fields.items()},
            }
        }
        if update_time:
            write["currentDocument"] = {"updateTime": update_time}
        body = {"writes": [write]}
        async with self.session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                text = await resp.text()
                if "FAILED_PRECONDITION" in text:
                    raise UpdateFailed(
                        "This reading changed elsewhere (likely in the app) while "
                        "this edit was in progress. Please try again."
                    )
                raise UpdateFailed(f"Pool Assistant write failed: HTTP {resp.status}: {text}")

        await self.async_request_refresh()

    @staticmethod
    def _find_datapoint_index(datapoints: list, result_id: str):
        """Find which datapoint (test session) contains a reading with this resultId."""
        for i, dp in enumerate(datapoints):
            if not isinstance(dp, dict):
                continue
            for reading in (dp.get("parameters") or {}).values():
                if isinstance(reading, dict) and reading.get("resultId") == result_id:
                    return i
        return None

    async def async_delete_reading(self, result_id: str) -> None:
        """Delete an entire test session containing this reading.

        Matches the app's own 'delete reading' button exactly: a 'reading' is
        really a whole test session (datapoint), which may bundle several
        parameters together - deleting it removes the whole session, not
        just one value.
        """
        doc, update_time = await self._fetch_raw_doc_fields()
        datapoints = doc.get("datapoints") or []
        index = self._find_datapoint_index(datapoints, result_id)
        if index is None:
            raise UpdateFailed(f"No reading found with resultId {result_id}")
        del datapoints[index]
        doc["datapoints"] = datapoints
        await self._write_full_document(doc, update_time)

    async def async_edit_reading(self, result_id: str, new_result: float) -> None:
        """Change a single reading's result value in place, leaving the rest
        of its test session untouched. The app itself can't do this - only
        delete-and-recreate - so this goes beyond stock app behaviour.
        """
        doc, update_time = await self._fetch_raw_doc_fields()
        datapoints = doc.get("datapoints") or []
        index = self._find_datapoint_index(datapoints, result_id)
        if index is None:
            raise UpdateFailed(f"No reading found with resultId {result_id}")
        parameters = datapoints[index].get("parameters") or {}
        for reading in parameters.values():
            if isinstance(reading, dict) and reading.get("resultId") == result_id:
                reading["result"] = new_result
                break
        doc["datapoints"] = datapoints
        await self._write_full_document(doc, update_time)

    async def async_add_chemical_to_reading(self, result_id: str, chemical: dict) -> None:
        """Attach a chemical dose to the same test session (datapoint) as an
        existing reading, instead of creating a standalone chemicals-only
        datapoint. The app's own chemical-logging screen requires a dose to
        be attached to a session with real readings in it - a freestanding
        chemicals-only datapoint is what was making the app misbehave when
        tapping the reading line above it.
        """
        doc, update_time = await self._fetch_raw_doc_fields()
        datapoints = doc.get("datapoints") or []
        index = self._find_datapoint_index(datapoints, result_id)
        if index is None:
            raise UpdateFailed(f"No reading found with resultId {result_id}")
        chemicals = datapoints[index].get("chemicals")
        if not isinstance(chemicals, list):
            chemicals = []
        chemicals.append(chemical)
        datapoints[index]["chemicals"] = chemicals
        doc["datapoints"] = datapoints
        await self._write_full_document(doc, update_time)
