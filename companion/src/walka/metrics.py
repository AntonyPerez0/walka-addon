"""Calories and cadence from belt speed.

The same formulas live in the addon (``Walka/Core/Metrics.lua``) so that the
in-game live estimate and the companion's numbers agree;
``tests/shared/metrics_vectors.json`` pins both implementations together.

Why these formulas: the textbook treadmill equation (ACSM walking) is only
meant for 1.9-3.7 mph and, against measured oxygen uptake, reads 11-35% low
at desk-walking speeds. The ones below were chosen because they were
validated at the slow speeds people actually use under a desk.
"""

from __future__ import annotations

from dataclasses import dataclass

KMH_PER_MPH = 1.609344
METERS_PER_MILE = 1609.344
METERS_PER_YARD = 0.9144

JOULES_PER_KCAL = 4184.0

# Walking and running formulas are blended linearly across these belt
# speeds (m/s). About half of adults still walk at 2.0 m/s (4.5 mph).
ENERGY_BLEND_MS = (2.0, 2.2)
CADENCE_BLEND_MS = (1.9, 2.2)

# What sitting at a desk costs (Compendium of Physical Activities: 1.3 MET),
# in watts per kg - subtracted for "extra calories versus sitting".
SITTING_W_PER_KG = 1.3 * JOULES_PER_KCAL / 3600.0

# Per-user correction of the step estimate (counted / estimated).
STEP_CALIBRATION_RANGE = (0.7, 1.5)


@dataclass
class Profile:
    """The walker's body, for the estimates."""

    weight_kg: float = 75.0
    height_cm: float = 172.0
    age: int | None = None
    sex: str | None = None  # "male" | "female" | None
    # Multiplier for the step estimate: count your steps for a minute, divide
    # by what Walka counted in that minute.
    step_calibration: float = 1.0

    @property
    def height_m(self) -> float:
        return self.height_cm / 100.0


def _blend(v: float, window: tuple[float, float], low: float, high: float) -> float:
    start, end = window
    if v <= start:
        return low
    if v >= end:
        return high
    share = (v - start) / (end - start)
    return low + (high - low) * share


def watts_per_kg(kmh: float) -> float:
    """Gross metabolic rate on a moving level belt, in watts per kg of body mass.

    Walking: the LCDA equation (Looney et al. 2019), fitted to 48 studies and
    valid from a standstill to 2 m/s. Running: the ACSM running equation.
    """
    if kmh <= 0:
        return 0.0
    v = kmh / 3.6
    walking = 1.44 + 1.94 * v**0.43 + 0.24 * v**4
    # ACSM: VO2 [ml/kg/min] = 0.2 * speed [m/min] + 3.5; 1 litre O2 = 5 kcal.
    running = (0.2 * (v * 60.0) + 3.5) * 5.0 * JOULES_PER_KCAL / 60000.0
    return _blend(v, ENERGY_BLEND_MS, walking, running)


def kcal_per_minute(kmh: float, profile: Profile, *, net: bool = False) -> float:
    """Calories burned per minute at a belt speed.

    With ``net=True`` the cost of sitting is subtracted: what the walk burned
    on top of sitting at the desk.
    """
    if kmh <= 0:
        return 0.0
    rate = watts_per_kg(kmh)
    if net:
        rate = max(0.0, rate - SITTING_W_PER_KG)
    return rate * profile.weight_kg * 60.0 / JOULES_PER_KCAL


def steps_per_minute(kmh: float, height_cm: float, calibration: float = 1.0) -> float:
    """Estimated cadence (steps per minute) at a belt speed.

    Walking: 128 * (speed / height)^0.465 with speed in m/s and height in m -
    a fit to hand-counted treadmill cadence of 156 adults from 0.5 to 4.5 mph
    (CADENCE-Adults), within 3 steps/min of the group means. Individuals
    differ by 6-20%, most at slow speeds, hence ``calibration``.
    Running: Hoeger et al. 2008.
    """
    if kmh <= 0:
        return 0.0
    v = kmh / 3.6
    h = height_cm / 100.0
    walking = 128.0 * (v / h) ** 0.465
    running = 143.6 + (1084.0 - 531.5 * h) * v / 26.82
    low, high = STEP_CALIBRATION_RANGE
    return _blend(v, CADENCE_BLEND_MS, walking, running) * min(high, max(low, calibration))


def kcal_per_minute_from_hr(bpm: float, profile: Profile) -> float | None:
    """Heart-rate based energy expenditure (Keytel et al. 2005), kcal/min.

    Needs age and sex; returns ``None`` without them. The equation was
    fitted at 57-90% of maximum heart rate and badly over-reads below that,
    so callers must only use it above ``hr_threshold_bpm``.
    """
    if profile.age is None or profile.sex not in ("male", "female") or bpm <= 0:
        return None
    if profile.sex == "male":
        kj = -55.0969 + 0.6309 * bpm + 0.1988 * profile.weight_kg + 0.2017 * profile.age
    else:
        kj = -20.4022 + 0.4472 * bpm - 0.1263 * profile.weight_kg + 0.074 * profile.age
    return max(0.0, kj / 4.184)


def hr_threshold_bpm(profile: Profile) -> float | None:
    """Heart rate from which the heart-rate formula may be used: 60% of the
    age-predicted maximum (220 - age). ``None`` without an age."""
    if profile.age is None:
        return None
    return 0.6 * (220 - profile.age)


# The heart rate must stay above the threshold this long before it is used
# (a jump scare in a dungeon is not exercise).
HR_SUSTAIN_S = 60.0
