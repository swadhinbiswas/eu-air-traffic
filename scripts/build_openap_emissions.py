"""Generate the OpenAP fuel-flow lookup table used by ``services.emissions``.

The runtime never imports OpenAP — the VPS only needs the committed JSON. Run
this when OpenAP adds aircraft or the model updates:

    uv run --extra openap python -m scripts.build_openap_emissions

OpenAP (TU Delft, open source) models fuel flow from mass, true airspeed,
altitude and vertical rate. We precompute it on a small grid per type so the
collector's hot path stays a dict lookup.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Grid the collector snaps live telemetry onto.
ALT_BANDS_FT = [0, 5_000, 10_000, 15_000, 20_000, 25_000, 30_000, 35_000, 40_000]
SPEED_BANDS_KT = [150, 250, 350, 450]
VS_BANDS_FPM = [-1500, 0, 1500]

# Assumed mid-payload mass for the lookup: oew + fraction × (mtow − oew).
PAYLOAD_FRACTION = 0.5

# Taxi/idle burn as a fraction of takeoff fuel flow. OpenAP's FuelFlow.idle()
# is not implemented for the default backend, and airborne models are invalid on
# the ground; 4% of takeoff matches published A320/B738 taxi figures.
TAXI_FRACTION_OF_TAKEOFF = 0.04

DEFAULT_OUT = Path("services/data/emission_openap.json")


def build() -> dict[str, Any]:
    from openap import FuelFlow, prop

    sources = sorted(prop.available_aircraft())
    types: dict[str, Any] = {}
    for name in sources:
        code = name.upper()
        try:
            spec = prop.aircraft(name)
            flow_model = FuelFlow(ac=name)
        except Exception:  # noqa: BLE001 - fall back to OpenAP's synonym airframe
            try:
                spec = prop.aircraft(name, use_synonym=True)
                flow_model = FuelFlow(ac=name, use_synonym=True)
            except Exception as exc:  # noqa: BLE001 - skip unsupported airframes
                print(f"  skip {code}: {exc}", file=sys.stderr)
                continue
        mass = float(spec["oew"]) + PAYLOAD_FRACTION * (float(spec["mtow"]) - float(spec["oew"]))
        grid: list[list[list[float]]] = []
        try:
            for vs in VS_BANDS_FPM:
                by_alt = []
                for alt in ALT_BANDS_FT:
                    by_alt.append(
                        [
                            round(
                                float(flow_model.enroute(mass=mass, tas=tas, alt=alt, vs=vs))
                                * 3600,
                                1,
                            )
                            for tas in SPEED_BANDS_KT
                        ]
                    )
                grid.append(by_alt)
            takeoff = float(flow_model.takeoff(tas=160, alt=0)) * 3600
        except Exception as exc:  # noqa: BLE001 - outside the model envelope
            print(f"  skip {code}: {exc}", file=sys.stderr)
            continue
        types[code] = {
            "ground": round(takeoff * TAXI_FRACTION_OF_TAKEOFF, 1),
            "flow": grid,
        }

    return {
        "_meta": {
            "source": "openap",
            "units": "kg/h",
            "flow_order": "flow[vs_index][alt_index][speed_index]",
            "alt_bands_ft": ALT_BANDS_FT,
            "speed_bands_kt": SPEED_BANDS_KT,
            "vs_bands_fpm": VS_BANDS_FPM,
            "payload_fraction": PAYLOAD_FRACTION,
            "taxi_fraction_of_takeoff": TAXI_FRACTION_OF_TAKEOFF,
        },
        "types": types,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the OpenAP emissions lookup")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    data = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(data['types'])} aircraft types to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
