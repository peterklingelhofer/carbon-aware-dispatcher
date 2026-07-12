"""Tests for carbon-aware inference routing."""

from unittest import mock

from integrations import inference_router as ir

ENDPOINTS = [
    {"name": "us-west", "zone": "CISO", "url": "u1"},
    {"name": "france", "zone": "FR", "url": "u2"},
    {"name": "norway", "zone": "NO-NO1", "url": "u3"},
]


def _measure(pairs):
    def fake(zones, ceiling, *a, collect=None, **k):
        collect.extend(pairs)
        return (None, None, None, [])

    return fake


class TestRankEndpoints:
    @mock.patch("integrations.inference_router.check_grid.check_multiple_zones")
    def test_sorts_cleanest_first(self, cmz):
        cmz.side_effect = _measure([("CISO", 300), ("FR", 60), ("NO-NO1", 20)])
        ranked = ir.rank_endpoints(ENDPOINTS)
        assert [e["name"] for e in ranked] == ["norway", "france", "us-west"]
        assert ranked[0]["intensity"] == 20

    @mock.patch("integrations.inference_router.check_grid.check_multiple_zones")
    def test_unreadable_endpoints_sort_last(self, cmz):
        cmz.side_effect = _measure([("FR", 60)])  # only FR has a reading
        ranked = ir.rank_endpoints(ENDPOINTS)
        assert ranked[0]["name"] == "france"
        assert ranked[-1]["intensity"] is None


class TestCleanestEndpoint:
    @mock.patch("integrations.inference_router.check_grid.check_multiple_zones")
    def test_picks_lowest(self, cmz):
        cmz.side_effect = _measure([("CISO", 300), ("FR", 60), ("NO-NO1", 20)])
        assert ir.cleanest_endpoint(ENDPOINTS)["name"] == "norway"

    @mock.patch("integrations.inference_router.check_grid.check_multiple_zones")
    def test_none_when_no_readings(self, cmz):
        cmz.side_effect = _measure([])
        assert ir.cleanest_endpoint(ENDPOINTS) is None
