"""Langelier Saturation Index calculation.

LSI = measured pH - theoretical saturation pH (pHs), where pHs is derived
from water temperature, calcium hardness, and total alkalinity (all as
CaCO3-equivalent ppm, matching how Pool Assistant already reports ta/ch).
"""
import math


def calculate_lsi(
    ph: float,
    temperature_c: float,
    total_alkalinity_ppm: float,
    calcium_hardness_ppm: float,
    tds_ppm: float,
) -> dict:
    temp_k = temperature_c + 273.0

    a = (math.log10(tds_ppm) - 1) / 10 if tds_ppm > 0 else 0
    b = -13.12 * math.log10(temp_k) + 34.55
    c = math.log10(calcium_hardness_ppm) - 0.4 if calcium_hardness_ppm > 0 else 0
    d = math.log10(total_alkalinity_ppm) if total_alkalinity_ppm > 0 else 0

    ph_s = (9.3 + a + b) - (c + d)
    lsi = round(ph - ph_s, 2)

    if lsi > 0.3:
        status = "scaling"
    elif lsi < -0.3:
        status = "corrosive"
    else:
        status = "balanced"

    return {"lsi": lsi, "saturation_ph": round(ph_s, 2), "status": status}
