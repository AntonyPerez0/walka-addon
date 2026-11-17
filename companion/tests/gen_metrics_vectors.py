"""Generate ``tests/shared/metrics_vectors.json``.

The file pins the calorie and cadence formulas: both implementations - the
companion's ``walka/metrics.py`` and the addon's ``Walka/Core/Metrics.lua`` -
must reproduce every vector (see ``test_metrics.py``). The expected values
are produced by the Python implementation.

Run it after a deliberate change to the formulas::

    python companion/tests/gen_metrics_vectors.py

and change ``Metrics.lua`` to match in the same commit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VECTORS_PATH = HERE.parent.parent / "tests" / "shared" / "metrics_vectors.json"

# Belt speeds (km/h): stopped, the pad's grid ends and typical speeds in mph
# and km/h, both sides of the walk/run switch at 8 km/h, and a few runs.
SPEEDS_KMH = [
    0.0, 0.5, 0.965606, 1.0, 1.609344, 2.0, 2.4, 3.218688, 4.0, 4.828032,
    5.5, 6.437376, 7.0, 7.99, 8.0, 8.01, 9.0, 9.977933, 12.0, 16.0,
]  # fmt: skip
WEIGHTS_KG = [50.0, 75.0, 110.5]
HEIGHTS_CM = [155.0, 172.0, 190.5]
TOLERANCE_REL = 1e-9


def build() -> dict:
    src = HERE.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from walka import metrics

    vectors = []
    for kmh in SPEEDS_KMH:
        for weight in WEIGHTS_KG:
            for height in HEIGHTS_CM:
                profile = metrics.Profile(weight_kg=weight, height_cm=height)
                vectors.append(
                    {
                        "kmh": kmh,
                        "weight_kg": weight,
                        "height_cm": height,
                        "vo2": metrics.vo2(kmh, height / 100.0),
                        "kcal_per_min": metrics.kcal_per_minute(kmh, profile),
                        "kcal_per_min_net": metrics.kcal_per_minute(kmh, profile, net=True),
                        "steps_per_min": metrics.steps_per_minute(kmh, height),
                    }
                )
    return {
        "description": (
            "Calories and cadence from belt speed: inputs and expected outputs. "
            "walka/metrics.py and Walka/Core/Metrics.lua must both reproduce every vector."
        ),
        "generator": "companion/tests/gen_metrics_vectors.py",
        "tolerance_rel": TOLERANCE_REL,
        "units": {
            "kmh": "belt speed, km/h",
            "weight_kg": "body weight, kg",
            "height_cm": "body height, cm",
            "vo2": "gross oxygen uptake, ml/kg/min",
            "kcal_per_min": "gross kcal per minute",
            "kcal_per_min_net": "kcal per minute above resting",
            "steps_per_min": "cadence, steps per minute",
        },
        "vectors": vectors,
    }


def render(data: dict) -> str:
    """Stable text form: one vector per line, keys in insertion order."""
    head = {k: v for k, v in data.items() if k != "vectors"}
    lines = [json.dumps(head, indent=2)[:-2] + ","]
    lines.append('  "vectors": [')
    rows = [json.dumps(vector, separators=(", ", ": ")) for vector in data["vectors"]]
    lines.append(",\n".join("    " + row for row in rows))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = render(build())
    json.loads(text)  # must be valid JSON
    VECTORS_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {VECTORS_PATH} ({len(build()['vectors'])} vectors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
