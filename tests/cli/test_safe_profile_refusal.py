"""Safe exports with changed null rates are unusable diff inputs (exit 2)."""

import json

import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.privacy.safe_profile import to_safe_profile


@pytest.mark.parametrize("safe_side", ["baseline", "current", "both"])
@pytest.mark.parametrize("flags", [[], ["--fail-on-drift"], ["--json", "-", "--fail-on-drift"]])
def test_diff_refuses_safe_profile(tmp_path, capsys, safe_side, flags):
    inputs = []
    safe_paths = []
    for side, nulls in (("baseline", 0), ("current", 12)):
        profile = shape.profile(pa.table({"value": [None] * nulls + [1] * (100 - nulls)}))
        if safe_side in (side, "both"):
            # The marker, not the suffix, identifies a safe profile.
            path = tmp_path / f"{side}.json"
            path.write_text(to_safe_profile(profile).to_json(), encoding="utf-8")
            safe_paths.append(path)
        else:
            path = tmp_path / f"{side}.shape"
            shape.save(profile, path)
        inputs.append(str(path))
    capsys.readouterr()
    assert main(["diff", "--no-project", *inputs, *flags]) == 2
    output = capsys.readouterr()
    if "--json" in flags:
        report = json.loads(output.out)
        assert report["exit_code"] == 2 and "payload" not in report
        assert str(safe_paths[0]) in report["error"]
    else:
        assert not output.out
    assert str(safe_paths[0]) in output.err
    assert "safe profile" in output.err and "diff the .shape captures instead" in output.err
    assert "Traceback" not in output.err
