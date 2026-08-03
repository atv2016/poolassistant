"""Pool volume estimation, matching the Pool Assistant app's own volume
calculator. Formulas below were reverse-engineered from real numbers the
app produced for known input combinations - not textbook pool-industry
formulas, which turned out to disagree with the app in places.

Confirmed against real app output:
- The app's gallons-per-cubic-foot conversion factor is 7.48 (not the more
  commonly published 7.5).
- Rectangular: Length x Width x Average Depth x 7.48
- Round: pi x (Diameter / 2)^2 x Average Depth x 7.48
- Oval: Length x Width x (pi / 4) x Average Depth x 7.48
- Kidney: NOT a continuous sloped floor like the others - modeled as two
  separate circular "lobes" (diameter and diameter_2), each carrying its
  OWN depth in variable-depth mode rather than a single shared average.
  In constant-depth mode, both lobes simply use the same depth value.
    pi x (diameter / 2)^2 x depth + pi x (diameter_2 / 2)^2 x depth_2

Variable-depth handling (simple mean of shallow_depth and deep_depth) is
confirmed against real app output for all four shapes.
"""

import math

GALLONS_PER_CUBIC_FOOT = 7.48
FEET_PER_METER = 3.28084
LITERS_PER_GALLON = 3.78541


def _to_feet(value: float, unit: str) -> float:
    return value * FEET_PER_METER if unit == "meters" else value


def _average_depth_ft(average_depth, shallow_depth, deep_depth, unit) -> float:
    if average_depth is not None:
        return _to_feet(average_depth, unit)
    if shallow_depth is not None and deep_depth is not None:
        return _to_feet((shallow_depth + deep_depth) / 2, unit)
    raise ValueError("Provide either average_depth, or both shallow_depth and deep_depth.")


def calculate_pool_volume(
    shape: str,
    unit: str = "feet",
    length: float | None = None,
    width: float | None = None,
    diameter: float | None = None,
    diameter_2: float | None = None,
    average_depth: float | None = None,
    shallow_depth: float | None = None,
    deep_depth: float | None = None,
) -> dict:
    if shape == "rectangular":
        if length is None or width is None:
            raise ValueError("Rectangular pools need both length and width.")
        depth_ft = _average_depth_ft(average_depth, shallow_depth, deep_depth, unit)
        cubic_feet = _to_feet(length, unit) * _to_feet(width, unit) * depth_ft

    elif shape == "round":
        if diameter is None:
            raise ValueError("Round pools need diameter.")
        depth_ft = _average_depth_ft(average_depth, shallow_depth, deep_depth, unit)
        radius_ft = _to_feet(diameter, unit) / 2
        cubic_feet = math.pi * (radius_ft ** 2) * depth_ft

    elif shape == "oval":
        if length is None or width is None:
            raise ValueError("Oval pools need both length and width.")
        depth_ft = _average_depth_ft(average_depth, shallow_depth, deep_depth, unit)
        cubic_feet = _to_feet(length, unit) * _to_feet(width, unit) * (math.pi / 4) * depth_ft

    elif shape == "kidney":
        if diameter is None or diameter_2 is None:
            raise ValueError("Kidney pools need both diameter and diameter_2.")
        # Two separate circular lobes, each with its own depth - not a
        # single shared average depth like the other shapes.
        if average_depth is not None:
            depth_1_ft = depth_2_ft = _to_feet(average_depth, unit)
        elif shallow_depth is not None and deep_depth is not None:
            depth_1_ft = _to_feet(shallow_depth, unit)
            depth_2_ft = _to_feet(deep_depth, unit)
        else:
            raise ValueError(
                "Provide either average_depth (constant-depth kidney pool), "
                "or both shallow_depth and deep_depth (variable-depth - "
                "shallow_depth applies to the diameter lobe, deep_depth to "
                "the diameter_2 lobe)."
            )
        radius_1_ft = _to_feet(diameter, unit) / 2
        radius_2_ft = _to_feet(diameter_2, unit) / 2
        cubic_feet = (
            math.pi * (radius_1_ft ** 2) * depth_1_ft
            + math.pi * (radius_2_ft ** 2) * depth_2_ft
        )

    else:
        raise ValueError(f"Unsupported shape: {shape}")

    gallons = cubic_feet * GALLONS_PER_CUBIC_FOOT
    return {
        "volume_gallons": round(gallons, 1),
        "volume_liters": round(gallons * LITERS_PER_GALLON, 1),
    }
