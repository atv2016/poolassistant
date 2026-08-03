"""Constants for the Pool Assistant integration."""
from homeassistant.components.sensor import SensorDeviceClass

DOMAIN = "poolassistant"

OPTION_SURFACE_TOOLTIP_IDS = "surface_tooltip_ids"
OPTION_IDEAL_RANGES = "ideal_ranges"
OPTION_TEMPERATURE_ENTITY = "temperature_entity"
OPTION_POLL_INTERVAL_MINUTES = "poll_interval_minutes"
OPTION_DISABLE_AUTO_DISCOVERY = "disable_auto_discovery"

DEFAULT_POLL_INTERVAL_MINUTES = 15

# Master list of every chemistry parameter the app supports - confirmed
# against the app's own manual-entry parameter list and the Scuba3s's
# physical test menu. A sensor is created for each of these even if the
# pool has no reading for it (yet) - it will just show 'unknown' instead
# of not existing at all.
#
# Units are best guesses based on typical pool-chemistry conventions (mg/L ~
# ppm), confirmed against real captured Firestore data where possible.
PARAMETERS = {
    "fcl":  {"name": "Free Chlorine",     "unit": "mg/L", "device_class": None,               "icon": "mdi:test-tube"},
    "tcl":  {"name": "Total Chlorine",    "unit": "mg/L", "device_class": None,               "icon": "mdi:test-tube"},
    "ccl":  {"name": "Combined Chlorine", "unit": "mg/L", "device_class": None,               "icon": "mdi:test-tube"},
    "ph":   {"name": "pH",                "unit": None,   "device_class": SensorDeviceClass.PH, "icon": None},
    "ta":   {"name": "Total Alkalinity",  "unit": "mg/L", "device_class": None,               "icon": "mdi:water-percent"},
    "cya":  {"name": "Cyanuric Acid",     "unit": "mg/L", "device_class": None,               "icon": "mdi:water-opacity"},
    "ch":   {"name": "Calcium Hardness",  "unit": "mg/L", "device_class": None,               "icon": "mdi:water-opacity"},
    "cu":   {"name": "Copper",            "unit": "mg/L", "device_class": None,               "icon": "mdi:water-alert"},
    "salt": {"name": "Salt",              "unit": "mg/L", "device_class": None,               "icon": "mdi:shaker-outline"},
    "po4":  {"name": "Phosphates",        "unit": "mg/L", "device_class": None,               "icon": "mdi:water-alert-outline"},
    "ao2":  {"name": "Active Oxygen",     "unit": "mg/L", "device_class": None,               "icon": "mdi:water-outline"},
    "br2":  {"name": "Bromine",           "unit": "mg/L", "device_class": None,               "icon": "mdi:test-tube"},
}

# Generic pool-chemistry guideline ranges, used only as a fallback when a
# reading has no lowRange/highRange of its own, and no per-pool override
# has been set via the integration's options. Treat these as a starting
# point, not gospel - actual targets vary by regional guidance, pool type,
# and interactions between parameters (e.g. free chlorine's effective
# target shifts with cyanuric acid level) - which is exactly what the
# per-pool override option exists for.
IDEAL_RANGES = {
    "fcl": (1.0, 3.0),
    "tcl": (1.0, 3.0),
    "ccl": (0.0, 0.2),
    "ph": (7.2, 7.6),
    "ta": (80.0, 120.0),
    "cya": (30.0, 50.0),
    "ch": (200.0, 400.0),
    "cu": (0.0, 0.3),
    "salt": (2700.0, 3400.0),
    "ao2": (0.0, 6.0),
    "br2": (3.0, 5.0),
    # po4 omitted - no reliable mg/l guideline confirmed; add your own via
    # the integration's options if you have a target in mind.
}

# Firestore method codes, confirmed only from real captured Scuba3s payloads
# (device-sourced readings; manual entries never carry these fields at all,
# regardless of who logs them). Deliberately not guessing codes we haven't
# actually seen - a wrong methodNumber/methodName pair risks confusing the
# app's own UI. Still missing: po4, ao2 (real Scuba3s tests, just not
# captured yet). ccl and salt are expected to never get a code - ccl looks
# to be a calculated/manual-only value (TCL - FCL), and salt isn't a
# reagent test on this device at all.
METHOD_INFO = {
    "fcl": {"methodNumber": "M100F", "methodName": "Cl F"},
    "tcl": {"methodNumber": "M100T", "methodName": "Cl T"},
    "ph":  {"methodNumber": "M330",  "methodName": "pH"},
    "ta":  {"methodNumber": "M030",  "methodName": "TA"},
    "cya": {"methodNumber": "M160",  "methodName": "CyA"},
    "ch":  {"methodNumber": "M191",  "methodName": "CaH"},
    "br2": {"methodNumber": "M80",   "methodName": "Br"},
    "cu":  {"methodNumber": "M150",  "methodName": "Cu"},
}
