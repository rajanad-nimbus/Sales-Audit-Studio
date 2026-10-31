"""Verify the commit gate stops immediately when a required check fails."""

import os
import subprocess
from pathlib import Path

import pytest


HOOK = Path(__file__).resolve().parents[4] / ".husky" / "pre-commit"


@pytest.mark.parametrize("failing_step", ["lint-staged", "tsc", "design-system", "ruff", "pytest"])
def test_pre_commit_propagates_failures(tmp_path, failing_step):
    tools = {
        "frontend/node_modules/.bin/lint-staged": "lint-staged",
        "frontend/node_modules/.bin/tsc": "tsc",
        "bin/node": "design-system",
        "backend/.venv/bin/ruff": "ruff",
        "backend/.venv/bin/python": "pytest",
    }
    for path, name in tools.items():
        executable = tmp_path / path
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text(
            f'#!/bin/sh\necho {name} >> "$CHECK_LOG"\n'
            f'[ "$FAILING_STEP" != {name} ]\n'
        )
        executable.chmod(0o755)

    test_file = tmp_path / "backend/apps/example/tests/test_example.py"
    test_file.parent.mkdir(parents=True)
    test_file.touch()
    (tmp_path / "backend/integrations").mkdir()
    log = tmp_path / "checks.log"
    env = {**os.environ, "FAILING_STEP": failing_step, "CHECK_LOG": str(log),
           "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}

    result = subprocess.run(["sh", str(HOOK)], cwd=tmp_path, env=env, check=False)

    assert result.returncode != 0
    assert log.read_text().splitlines() == list(tools.values())[: list(tools.values()).index(failing_step) + 1]
