from __future__ import annotations

import os

import pytest

import config as config_module


def test_generated_service_token_is_atomically_persisted(tmp_path, monkeypatch):
    token_file = tmp_path / "RomanVoice" / "service_token.txt"
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN", "")
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN_FILE", str(token_file))

    token = config_module.ensure_service_token()

    assert token
    assert token_file.read_text(encoding="utf-8").strip() == token
    assert config_module.config.SERVICE_TOKEN == token
    assert list(token_file.parent.glob("*.tmp")) == []


def test_generated_service_token_does_not_survive_only_in_memory(
    tmp_path,
    monkeypatch,
):
    token_file = tmp_path / "RomanVoice" / "service_token.txt"
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN", "")
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN_FILE", str(token_file))

    def fail_replace(_source, _destination):
        raise OSError("disk unavailable")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(RuntimeError, match="Unable to persist"):
        config_module.ensure_service_token()

    assert config_module.config.SERVICE_TOKEN == ""
    assert not token_file.exists()
    assert list(token_file.parent.glob("*.tmp")) == []


def test_service_token_diagnostics_report_drift_without_secret_values(
    tmp_path,
    monkeypatch,
):
    token_file = tmp_path / "service_token.txt"
    token_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("ROMANVOICE_SERVICE_TOKEN", "environment-secret")
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN", "environment-secret")
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN_FILE", str(token_file))

    status = config_module.service_token_configuration()

    assert status == {
        "source": "environment",
        "file_present": True,
        "environment_present": True,
        "environment_file_mismatch": True,
        "active_file_mismatch": True,
    }
    assert "file-secret" not in repr(status)
    assert "environment-secret" not in repr(status)
