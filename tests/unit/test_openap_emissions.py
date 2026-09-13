"""OpenAP-backed emissions: kinematic lookup with the static table as fallback."""

from __future__ import annotations


def test_committed_table_loads() -> None:
    from services.emissions import _openap

    data = _openap()
    assert data, "services/data/emission_openap.json is missing or unreadable"
    assert "A320" in data["types"]
    assert data["_meta"]["flow_order"] == "flow[vs_index][alt_index][speed_index]"


def test_known_type_scales_with_flight_phase() -> None:
    from services.emissions import emission_estimate

    cruise = emission_estimate("A320", altitude=35_000, speed_kt=450, vertical_rate=0)
    climb = emission_estimate("A320", altitude=15_000, speed_kt=250, vertical_rate=1500)
    ground = emission_estimate("A320", on_ground=True)
    assert climb[0] > cruise[0] > ground[0] > 0
    assert cruise[2] is False and ground[2] is False


def test_lookup_matches_the_committed_grid() -> None:
    from services.emissions import _openap, emission_estimate

    table = _openap()["types"]["B738"]
    expected = table["flow"][1][7][3]  # level · 35,000 ft · 450 kt
    fuel, co2, estimated = emission_estimate("B738", altitude=35_000, speed_kt=450, vertical_rate=0)
    assert fuel == expected
    assert co2 == round(expected * 2.52, 1)
    assert estimated is False


def test_without_telemetry_uses_the_static_table() -> None:
    from services.emissions import emission_estimate

    fuel, co2, estimated = emission_estimate("A320")
    assert fuel == 2000.0
    assert co2 == 5040.0
    assert estimated is False


def test_unknown_type_is_flagged_estimated() -> None:
    from services.emissions import emission_estimate

    fuel, co2, estimated = emission_estimate("ZZZ9", altitude=35_000, speed_kt=450)
    assert fuel == 2000.0  # A320 fallback
    assert co2 == 5040.0
    assert estimated is True


def test_ground_vehicles_stay_zero_with_telemetry() -> None:
    from services.emissions import emission_estimate

    assert emission_estimate("GND", altitude=0, speed_kt=5, on_ground=True) == (0.0, 0.0, False)


def test_enrich_emissions_uses_telemetry() -> None:
    from services.emissions import enrich_emissions

    row = {
        "aircraft_type": "A320",
        "altitude": 35_000,
        "velocity": 450,
        "vertical_rate": 0,
        "on_ground": False,
    }
    enriched = enrich_emissions(row)
    assert enriched["co2_kg_per_hour"] > 5040.0  # cruise burn is higher than the flat rate
    assert enriched["co2_estimated"] is False
