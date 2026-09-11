"""Unit tests for aircraft operational classification and enrichment."""

from __future__ import annotations

from services.classification import (
    aircraft_types,
    airlines,
    classify,
    emitter_class,
    operator_info,
    type_info,
)
from services.enrichment import enrich_position, summarise_airspace


def test_reference_data_is_loaded() -> None:
    assert len(aircraft_types()) > 1000
    assert len(airlines()) > 1000


def test_type_lookup_uses_icao_8643() -> None:
    a320 = type_info("A320")
    assert a320 is not None
    assert a320["airframe"] == "landplane"
    assert a320["engines"] == 2
    assert a320["engine_type"] == "jet"
    assert type_info("EC45")["airframe"] == "helicopter"  # type: ignore[index]
    assert type_info("NOPE") is None


def test_operator_lookup() -> None:
    assert operator_info("DLH400")["operator_name"] == "Lufthansa"  # type: ignore[index]
    assert operator_info("DLH400")["operator_category"] == "passenger"  # type: ignore[index]
    assert operator_info("FDX9")["operator_category"] == "cargo"  # type: ignore[index]
    assert operator_info("RRR1")["operator_category"] == "military"  # type: ignore[index]
    assert operator_info("") is None


def test_emitter_category_mapping() -> None:
    assert emitter_class("A7") == "helicopter"
    assert emitter_class("B1") == "glider"
    assert emitter_class("B6") == "drone"
    assert emitter_class("B2") == "balloon"
    assert emitter_class("A5") == "heavy"
    assert emitter_class(None) == "unknown"
    assert emitter_class("Z9") == "unknown"


def test_helicopter_by_emitter_and_type() -> None:
    assert classify("HELI1", "EC45", "A7")["aircraft_class"] == "helicopter"
    assert classify("", "EC45", None)["aircraft_class"] == "helicopter"
    assert classify("", "H145", None)["aircraft_class"] == "helicopter"


def test_cargo_by_callsign_and_type() -> None:
    assert classify("FDX123", "B77F", None)["aircraft_class"] == "cargo"
    assert classify("", "B77F", None)["aircraft_class"] == "cargo"
    assert classify("GTI88", "", None)["is_cargo"] is True
    assert classify("CLX42", "B744", None)["aircraft_class"] == "cargo"
    assert classify("", "B763F", None)["aircraft_class"] == "cargo"


def test_military_by_callsign_and_type() -> None:
    assert classify("RCH451", "C17", None)["aircraft_class"] == "military"
    assert classify("RRR123", "", None)["is_military"] is True
    assert classify("", "C130", None)["aircraft_class"] == "military"


def test_military_by_icao24_block() -> None:
    assert classify(None, None, None, icao24="AE1234")["aircraft_class"] == "military"
    assert classify(None, None, None, icao24="43C123")["aircraft_class"] == "military"
    assert classify(None, "A320", None, icao24="400123")["aircraft_class"] == "passenger"


def test_passenger_and_private() -> None:
    assert classify("DLH400", "A320", "A3")["aircraft_class"] == "passenger"
    assert classify("BAW123", "A320", None)["aircraft_class"] == "passenger"
    assert classify("", "A320", "A5")["aircraft_class"] == "passenger"
    assert classify("", "C172", "A1")["aircraft_class"] == "private"


def test_registration_is_not_mistaken_for_an_operator() -> None:
    # US / German registrations must not resolve to an airline by coincidence.
    assert classify("N123AB", "C172", "A1")["operator_name"] is None
    assert classify("N123AB", "C172", "A1")["aircraft_class"] == "private"
    # A registration with no flight number must not match an operator.
    assert classify("GBTVX", "C152", "A1")["operator_name"] is None
    # A light piston is never airline traffic, even behind an airline-looking code.
    assert classify("DLH1", "C172", None)["aircraft_class"] == "private"


def test_business_and_unmanned() -> None:
    assert classify("", "G650", None)["aircraft_class"] == "business"
    assert classify("", "", "B6")["aircraft_class"] == "drone"
    assert classify("", "", "B2")["aircraft_class"] == "balloon"


def test_ground_vehicles() -> None:
    assert classify("", "", "C1")["aircraft_class"] == "ground"
    assert classify("", "", "C2")["aircraft_class"] == "ground"


def test_military_takes_priority_over_cargo() -> None:
    # A C-130 is both freighter-shaped and military; military must win.
    assert classify("", "C130", None)["aircraft_class"] == "military"


def test_operator_metadata_is_returned() -> None:
    row = classify("DLH400", "A320", None)
    assert row["operator_name"] == "Lufthansa"
    assert row["operator_country"] == "Germany"
    assert row["type_name"] is not None
    assert row["manufacturer"] is not None
    assert row["airframe"] == "landplane"
    assert row["wake_category"] == "M"


def test_enrich_position_adds_class_and_emissions() -> None:
    row = enrich_position(
        {
            "icao24": "ABC123",
            "callsign": "FDX99",
            "aircraft_type": "B77F",
            "category": "A5",
        }
    )
    assert row["aircraft_class"] == "cargo"
    assert row["is_cargo"] is True
    assert row["co2_kg_per_hour"] > 0
    assert row["fuel_burn_kg_per_hour"] > 0
    assert row["operator_name"] == "Federal Express"


def test_airspace_summary() -> None:
    rows = [
        enrich_position({"icao24": "1", "callsign": "DLH1", "aircraft_type": "A320"}),
        enrich_position({"icao24": "2", "callsign": "FDX9", "aircraft_type": "B77F"}),
        enrich_position({"icao24": "3", "callsign": "RCH1", "aircraft_type": "C17"}),
        enrich_position({"icao24": "4", "aircraft_type": "EC45", "category": "A7"}),
    ]
    summary = summarise_airspace(rows)
    assert summary["total"] == 4
    assert summary["cargo"] == 1
    assert summary["military"] == 1
    assert summary["helicopter"] == 1
    assert summary["by_class"]["passenger"] == 1
    assert summary["total_co2_kg_per_hour"] > 0
