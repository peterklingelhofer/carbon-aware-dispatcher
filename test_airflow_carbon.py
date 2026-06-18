"""Tests for the carbon-aware Airflow sensor."""

from unittest import mock

from integrations import airflow_carbon as ac


class TestPoke:
    def test_clean_pokes_true(self):
        with mock.patch.object(ac, "grid_is_clean", return_value=True) as g:
            sensor = ac.CarbonAwareSensor(zones="GB", max_carbon=150)
            assert sensor.poke() is True
            assert g.call_args.args[0] == "GB" and g.call_args.args[1] == 150

    def test_dirty_pokes_false(self):
        with mock.patch.object(ac, "grid_is_clean", return_value=False):
            assert ac.CarbonAwareSensor().poke({}) is False

    def test_tokens_threaded(self):
        with mock.patch.object(ac, "grid_is_clean", return_value=True) as g:
            ac.CarbonAwareSensor(tokens={"eia": "k"}).poke()
            assert g.call_args.kwargs == {"eia": "k"}

    def test_subclasses_object_without_airflow(self):
        # without Airflow installed the base is object, so construction is bare
        assert ac.CarbonAwareSensor().zones == "auto:green"
