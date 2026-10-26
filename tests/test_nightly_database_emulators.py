"""The selected database acceptance suite must have its real-server prerequisites."""

import shlex
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("prerequisite", ["service", "driver", "connection"])
def test_oracle_emulator_is_provisioned_for_selected_database_suite(prerequisite):
    job = yaml.safe_load((ROOT / ".github/workflows/nightly.yml").read_text())["jobs"][
        "databases-e2e"
    ]
    commands = [shlex.split(step["run"]) for step in job["steps"] if "run" in step]
    assert any("plugins/shape-databases/tests" in cmd and "emulator" in cmd for cmd in commands)
    if prerequisite == "service":
        assert any("up" in cmd and "--wait" in cmd and "oracle" in cmd for cmd in commands)
    elif prerequisite == "driver":
        assert any(
            cmd[:2] == ["uv", "sync"]
            and "--locked" in cmd
            and "--all-packages" in cmd
            and any(cmd[i : i + 2] == ["--extra", "oracle"] for i in range(len(cmd)))
            for cmd in commands
        )
    else:
        assert job["env"]["SHAPE_TEST_ORACLE"] == "oracle://shape@localhost:1521/FREEPDB1"
        compose = yaml.safe_load((ROOT / "ci/emulators/docker-compose.yml").read_text())
        assert (
            job["env"]["ORACLE_PASSWORD"]
            == compose["services"]["oracle"]["environment"]["APP_USER_PASSWORD"]
        )
