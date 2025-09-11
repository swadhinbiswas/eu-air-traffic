"""Unit tests for geometric route estimation and the learned route memory."""

from __future__ import annotations

from services.route_estimate import estimate_leg
from services.route_memory import RouteMemory


def _row(**overrides):
    base = {"latitude": 50.0, "longitude": 8.0, "altitude": 30000.0, "vertical_rate": 0.0}
    base.update(overrides)
    return base


def test_cruise_yields_nothing():
    leg = estimate_leg(_row())
    assert leg == {"origin": None, "destination": None, "phase": "enroute"}


def test_missing_coords_yields_nothing():
    leg = estimate_leg({"latitude": None, "longitude": None})
    assert leg["phase"] == "enroute"


def test_climbing_near_frankfurt_is_departure():
    # EDDF ≈ (50.03, 8.56); low and climbing right next to it.
    leg = estimate_leg(_row(latitude=50.05, longitude=8.60, altitude=5000.0, vertical_rate=1500.0))
    assert leg["origin"] == "EDDF"
    assert leg["destination"] is None
    assert leg["phase"] == "departing"


def test_descending_near_frankfurt_is_arrival():
    leg = estimate_leg(_row(latitude=50.00, longitude=8.50, altitude=4000.0, vertical_rate=-1200.0))
    assert leg["destination"] == "EDDF"
    assert leg["origin"] is None
    assert leg["phase"] == "arriving"


def test_ground_near_airport():
    leg = estimate_leg(_row(latitude=50.033, longitude=8.571, on_ground=True))
    assert leg["origin"] == "EDDF"
    assert leg["phase"] == "ground"


def test_ocean_yields_nothing():
    leg = estimate_leg(
        {"latitude": 30.0, "longitude": -40.0, "altitude": 2000.0, "vertical_rate": 800.0}
    )
    assert leg["origin"] is None and leg["destination"] is None


def test_memory_learns_pair_and_resolves():
    mem = RouteMemory()
    mem.observe("THY5TK", "EDDF", None, now=1000.0)
    mem.observe("THY5TK", None, "LTFM", now=2000.0)
    resolved = mem.resolve("THY5TK", "EDDF", None)
    assert resolved["route"] == "EDDF → LTFM"
    assert resolved["route_source"] == "typical"


def test_memory_resolves_cruise_from_callsign():
    mem = RouteMemory()
    mem.observe("DLH400", "EDDF", None, now=1000.0)
    mem.observe("DLH400", None, "EGLL", now=2000.0)
    resolved = mem.resolve("DLH400", None, None)
    assert resolved["route"] == "EDDF → EGLL"
    assert resolved["route_source"] == "typical"


def test_memory_unknown_callsign():
    mem = RouteMemory()
    resolved = mem.resolve("NOPE123", "EDDF", None)
    assert resolved["route"] == "EDDF → ?"
    assert resolved["route_source"] == "estimated"
    resolved = mem.resolve("NOPE123", None, None)
    assert resolved["route"] is None


def test_memory_persists(tmp_path):
    path = tmp_path / "route_memory.json"
    mem = RouteMemory(path)
    mem.observe("BAW1", "EGLL", None, now=1000.0)
    mem.observe("BAW1", None, "KJFK", now=2000.0)
    mem.save(force=True)
    assert path.exists()
    mem2 = RouteMemory(path)
    assert mem2.resolve("BAW1", None, None)["route"] == "EGLL → KJFK"
