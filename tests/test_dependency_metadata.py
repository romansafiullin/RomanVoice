from __future__ import annotations

import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_requirements_match_pyproject_direct_dependencies():
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    expected = {
        dependency.strip().lower()
        for dependency in pyproject["project"]["dependencies"]
    }
    actual = {
        line.strip().lower()
        for line in (PROJECT_ROOT / "requirements.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert actual == expected
