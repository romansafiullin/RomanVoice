from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import config as config_module
from services.dictation_service import RomanVoiceDictationService


def test_generated_service_token_is_atomically_persisted(tmp_path, monkeypatch):
    token_file = tmp_path / "RomanVoice" / "service_token.txt"
    protected_paths = []
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN", "")
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(
        config_module,
        "_protect_service_token_file",
        protected_paths.append,
    )

    token = config_module.ensure_service_token()

    assert token
    assert token_file.read_text(encoding="utf-8").strip() == token
    assert config_module.config.SERVICE_TOKEN == token
    assert list(token_file.parent.glob("*.tmp")) == []
    assert len(protected_paths) == 2
    assert protected_paths[0].endswith(".tmp")
    assert protected_paths[1] == str(token_file)


def test_generated_service_token_does_not_survive_only_in_memory(
    tmp_path,
    monkeypatch,
):
    token_file = tmp_path / "RomanVoice" / "service_token.txt"
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN", "")
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(
        config_module,
        "_protect_service_token_file",
        lambda _path: None,
    )

    def fail_replace(_source, _destination):
        raise OSError("disk unavailable")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(RuntimeError, match="Unable to persist"):
        config_module.ensure_service_token()

    assert config_module.config.SERVICE_TOKEN == ""
    assert not token_file.exists()
    assert list(token_file.parent.glob("*.tmp")) == []


def test_service_refuses_mismatched_environment_and_file_tokens(
    tmp_path,
    monkeypatch,
):
    token_file = tmp_path / "service_token.txt"
    token_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("ROMANVOICE_SERVICE_TOKEN", "environment-secret")
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN", "environment-secret")
    monkeypatch.setattr(config_module.config, "SERVICE_TOKEN_FILE", str(token_file))

    with pytest.raises(RuntimeError) as caught:
        RomanVoiceDictationService(SimpleNamespace())

    message = str(caught.value)
    assert "differs from the durable service token file" in message
    assert "file-secret" not in message
    assert "environment-secret" not in message


def test_windows_token_acl_is_exact_and_does_not_forward_service_token(
    tmp_path,
    monkeypatch,
):
    token_file = tmp_path / "service_token.txt"
    calls = []
    monkeypatch.setattr(config_module, "_is_windows", lambda: True)
    monkeypatch.setenv("ROMANVOICE_SERVICE_TOKEN", "must-not-reach-child")

    def fake_run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(config_module.subprocess, "run", fake_run)

    config_module._protect_service_token_file(str(token_file))

    assert len(calls) == 1
    arguments, kwargs = calls[0]
    assert arguments[0] == "powershell.exe"
    script = arguments[-1]
    assert "S-1-5-18" in script
    assert "S-1-5-32-544" in script
    assert "WindowsIdentity]::GetCurrent().User" in script
    assert "/inheritance:r /grant:r" in script
    assert "AreAccessRulesProtected" in script
    assert kwargs["check"] is False
    assert kwargs["env"]["ROMANVOICE_TOKEN_ACL_TARGET"] == str(token_file)
    assert "ROMANVOICE_SERVICE_TOKEN" not in kwargs["env"]


def test_windows_token_acl_failure_has_safe_diagnostics(tmp_path, monkeypatch):
    token_file = tmp_path / "service_token.txt"
    monkeypatch.setattr(config_module, "_is_windows", lambda: True)
    monkeypatch.setattr(
        config_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=5,
            stdout="file-secret",
            stderr="environment-secret",
        ),
    )

    with pytest.raises(OSError) as caught:
        config_module._protect_service_token_file(str(token_file))

    message = str(caught.value)
    assert "required Windows ACL (exit 5)" in message
    assert "file-secret" not in message
    assert "environment-secret" not in message


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
