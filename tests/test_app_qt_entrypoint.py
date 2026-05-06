"""Tests for the thin Qt entrypoint import behavior."""

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_app_qt_import_does_not_eagerly_import_application_controller():
    code = """
import sys
import app_qt
assert hasattr(app_qt, 'main')
assert 'services.application_controller' not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
