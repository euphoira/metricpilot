"""Regression for Unicode output on Windows and always-offline CLI commands."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("command", ["demo", "eval"])
def test_unicode_output_with_legacy_console_encoding(tmp_path, command):
    output = tmp_path / ("分析报告.md" if command == "demo" else "评测结果.json")
    environment = {
        **os.environ,
        "PYTHONIOENCODING": "cp1252",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "METRICPILOT_DATA_DIR": str(tmp_path / "data"),
        "METRICPILOT_MODE": "live",
        "METRICPILOT_MODEL": "",
        "METRICPILOT_API_KEY": "",
    }
    result = subprocess.run(
        [sys.executable, "-m", "metricpilot.cli", command, "--output", str(output)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    value = json.loads(result.stdout.decode("utf-8"))
    assert value["mode"] == "demo"
    if command == "eval":
        assert value["passed"] == value["total"] == 8
    else:
        assert value["status"] == "completed"
    assert output.read_text("utf-8")
