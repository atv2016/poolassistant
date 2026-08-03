"""Shared 'is this reading in range' logic, used by the sensor and
binary_sensor platforms so they can never disagree with each other."""
from .const import IDEAL_RANGES


def effective_range(key: str, low, high, overrides: dict | None = None) -> tuple[float | None, float | None, str]:
    """The (low, high, source) actually used to judge a reading - source is
    'custom' (a per-pool override), 'device' (the reading's own reported
    range), or 'default' (the built-in IDEAL_RANGES fallback). A custom
    override always wins, even over a device-reported range - a reagent
    test's own lowRange/highRange reflects what the instrument can
    measure, not necessarily what's healthy for this specific pool.
    """
    if overrides and key in overrides:
        o_low, o_high = overrides[key]
        return o_low, o_high, "custom"
    if low is not None and high is not None:
        return low, high, "device"
    d_low, d_high = IDEAL_RANGES.get(key, (None, None))
    return d_low, d_high, "default"


def status_for(key: str, result, low, high, overrides: dict | None = None) -> str | None:
    """Classify a reading as 'low', 'ideal', 'high', or 'unknown' (no range
    available at all), or None if there's no result yet."""
    if result is None:
        return None
    eff_low, eff_high, _ = effective_range(key, low, high, overrides)
    if eff_low is None or eff_high is None:
        return "unknown"
    if result < eff_low:
        return "low"
    if result > eff_high:
        return "high"
    return "ideal"
