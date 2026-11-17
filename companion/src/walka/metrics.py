"""Calories and cadence from belt speed.

The same formulas live in the addon (``Walka/Core/Metrics.lua``) so that the
in-game live estimate and the companion's numbers agree;
``tests/shared/metrics_vectors.json`` pins both implementations together.
"""

from __future__ import annotations

from dataclasses import dataclass

KMH_PER_MPH = 1.609344
METERS_PER_MILE = 1609.344
METERS_PER_YARD = 0.9144

# Speed (km/h) above which the running equations apply.
RUN_THRESHOLD_KMH = 8.0

# Resting oxygen uptake, ml O2 per kg per minute (1 MET).
VO2_REST = 3.5
# kcal released per litre of oxygen.
KCAL_PER_LITRE_O2 = 5.0


@dataclass
class Profile:
    """The walker's body, for the estimates."""

    weight_kg: float = 75.0
    height_cm: float = 172.0
    age: int | None = None
    sex: str | None = None  # "male" | "female" | None

    @property
    def height_m(self) -> float:
        return self.height_cm / 100.0


def vo2(kmh: float, height_m: float) -> float:
    """Gross oxygen uptake on a level belt, ml O2 per kg per minute."""
    if kmh <= 0:
        return VO2_REST
    if kmh >= RUN_THRESHOLD_KMH:
        # ACSM running equation (level): 0.2 ml/kg/min per m/min + rest.
        return VO2_REST + 0.2 * (kmh * 1000.0 / 60.0)
    # Ludlow & Weyand (2016) height-weight-speed walking model: rest + a
    # minimum walking cost + a term in speed squared over height. Unlike the
    # ACSM walking equation it stays accurate at slow desk-walking speeds.
    v = kmh / 3.6
    return VO2_REST + 3.85 + 5.97 * v * v / height_m


def kcal_per_minute(kmh: float, profile: Profile, *, net: bool = False) -> float:
    """Calories burned per minute at a belt speed.

    With ``net=True`` resting metabolism is subtracted ("active calories").
    """
    if kmh <= 0:
        return 0.0
    uptake = vo2(kmh, profile.height_m)
    if net:
        uptake -= VO2_REST
    return uptake * profile.weight_kg / 1000.0 * KCAL_PER_LITRE_O2


def steps_per_minute(kmh: float, height_cm: float) -> float:
    """Estimated cadence (steps per minute) at a belt speed."""
    if kmh <= 0:
        return 0.0
    height_m = height_cm / 100.0
    if kmh >= RUN_THRESHOLD_KMH:
        return (150.0 + 2.2 * (kmh - RUN_THRESHOLD_KMH)) * (1.70 / height_m) ** 0.5
    # Walking: cadence grows with speed^0.427 and shrinks with height^0.71
    # (dynamic similarity), anchored at 93 steps/min at 2 mph and 125 at
    # 4 mph for a 1.70 m adult.
    return 56.5 * kmh**0.427 * (1.70 / height_m) ** 0.71


def kcal_per_minute_from_hr(bpm: float, profile: Profile) -> float | None:
    """Heart-rate based energy expenditure (Keytel et al. 2005), kcal/min.

    Needs age and sex; returns ``None`` without them. Only meaningful during
    exercise: at near-resting heart rates it over-reads, so callers should
    use it only above a floor (see ``HR_FLOOR_BPM``).
    """
    if profile.age is None or profile.sex not in ("male", "female") or bpm <= 0:
        return None
    if profile.sex == "male":
        kj = -55.0969 + 0.6309 * bpm + 0.1988 * profile.weight_kg + 0.2017 * profile.age
    else:
        kj = -20.4022 + 0.4472 * bpm - 0.1263 * profile.weight_kg + 0.074 * profile.age
    return max(0.0, kj / 4.184)


# Below this heart rate the HR equation is not trusted.
HR_FLOOR_BPM = 90
