"""Tests for the env-var parsing helpers in check_grid."""

import pytest

from check_grid import _env_float, _env_int


def test_env_float_valid(monkeypatch):
    monkeypatch.setenv("MAX_CARBON", "123.5")
    assert _env_float("MAX_CARBON", 250) == 123.5


def test_env_float_default_when_unset(monkeypatch):
    monkeypatch.delenv("MAX_CARBON", raising=False)
    assert _env_float("MAX_CARBON", 250) == 250.0


def test_env_float_uses_raw_override(monkeypatch):
    monkeypatch.delenv("MAX_CARBON", raising=False)
    assert _env_float("MAX_CARBON", 250, "300") == 300.0


def test_env_float_invalid_exits(monkeypatch):
    monkeypatch.setenv("MAX_CARBON", "abc")
    with pytest.raises(SystemExit):
        _env_float("MAX_CARBON", 250)


def test_env_int_valid(monkeypatch):
    monkeypatch.setenv("MAX_WAIT", "45")
    assert _env_int("MAX_WAIT", 0) == 45


def test_env_int_invalid_exits(monkeypatch):
    monkeypatch.setenv("MAX_WAIT", "notanint")
    with pytest.raises(SystemExit):
        _env_int("MAX_WAIT", 0)
