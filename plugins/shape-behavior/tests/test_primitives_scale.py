"""``telemetry_series`` at 10,000 devices, hourly, for a year, in bounded memory.

The run goes through ``shape behave run --window-years`` in a fresh process; the peak resident
memory of that process is read at exit and recorded in the test output (``-s`` shows it). The
bound asserted is far below what holding the year in memory would need (about 87.6 million
events), and is not compared with any other tool.
"""

import json
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.heavy

WINDOW_YEARS = 1 / 52  # one week of events (about 1.7 million) per output file
PEAK_BOUND_MB = 1500  # measured about 770 MB on the build machine

CHILD = textwrap.dedent(
    """
    import argparse, sys
    from shape_behavior.cli import BehaveCommand
    parser = argparse.ArgumentParser()
    cmd = BehaveCommand()
    cmd.configure(parser)
    code = cmd.run(parser.parse_args(sys.argv[1:]))
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    except ImportError:  # Windows
        import psutil
        peak_mb = psutil.Process().memory_info().peak_wset / (1024 * 1024)
    print("PEAK_MB", round(peak_mb))
    sys.exit(code)
    """
)


def test_ten_thousand_devices_hourly_for_a_year_in_bounded_memory(tmp_path, capsys):
    params = tmp_path / "p.json"
    params.write_text(
        json.dumps(
            {
                "format": "shape-behavior-params",
                "version": 1,
                "primitive": "telemetry_series",
                "params": {"interval": "1 hour", "missing_rate": 0.01, "stuck_rate": 0.01},
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "o"
    r = subprocess.run(
        [sys.executable, "-c", CHILD, "run", "telemetry_series", "--params", str(params),
         "--population", "10000", "--years", "1", "--seed", "5", "--window-years",
         str(WINDOW_YEARS), "-o", str(out)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    peak = int(r.stdout.split("PEAK_MB")[1].split()[0])
    with capsys.disabled():
        print(f"\ntelemetry_series 10,000 devices x 1 hour x 1 year: peak memory {peak} MB")
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    slots = 10_000 * (366 * 24 + 1)  # a reading at the start and every hour of 2020 (366 days)
    # bound: five binomial sigmas around the 99 % of slots that are not skipped
    assert abs(manifest["events"] - 0.99 * slots) < 5 * (slots * 0.01 * 0.99) ** 0.5
    assert len(manifest["windows"]) == 52  # the 52nd window ends exactly at the end of the year
    assert peak < PEAK_BOUND_MB, f"peak {peak} MB"
