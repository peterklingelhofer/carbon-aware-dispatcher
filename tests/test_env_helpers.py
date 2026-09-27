"""Tests for the env-var parsing helpers in check_grid."""

import pytest

from check_grid import _env_float, _env_int


@pytest.mark.parametrize(
    ("helper", "name", "raw", "default", "expected"),
    [
        (_env_float, "MAX_CARBON", "123.5", 250, 123.5),
        (_env_int, "MAX_WAIT", "45", 0, 45),
    ],
)
def test_valid_value(monkeypatch, helper, name, raw, default, expected):
    monkeypatch.setenv(name, raw)
    assert helper(name, default) == expected


@pytest.mark.parametrize(
    ("helper", "name", "raw", "default"),
    [
        (_env_float, "MAX_CARBON", "abc", 250),
        (_env_int, "MAX_WAIT", "notanint", 0),
    ],
)
def test_invalid_value_exits(monkeypatch, helper, name, raw, default):
    monkeypatch.setenv(name, raw)
    with pytest.raises(SystemExit):
        helper(name, default)


def test_env_float_default_when_unset(monkeypatch):
    monkeypatch.delenv("MAX_CARBON", raising=False)
    assert _env_float("MAX_CARBON", 250) == 250.0


def test_env_float_uses_raw_override(monkeypatch):
    monkeypatch.delenv("MAX_CARBON", raising=False)
    assert _env_float("MAX_CARBON", 250, "300") == 300.0
