"""FRZ-01 retirement keeps the active CI surface independent of the archived engine."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_no_engine_in_ci():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        text = path.read_text()
        for retired in ("REFENGINE_DEPLOY_KEY", "REFENGINE_NAME", "setup_refengine.sh"):
            assert retired not in text, path
    assert "REFENGINE_" not in (ROOT / "scripts/env.sh").read_text()
    for name in (
        "run.py",
        "record_baseline.py",
        "nightly.py",
        "recorded_store.py",
        "profile_1to1/verify.py",
        "profile_1to1/bench.py",
        "profile_1to1/bench_cli.py",
        "domain_1to1/generate.py",
        "domain_1to1/verify.py",
        "domain_1to1/rowcounts.py",
        "domain_1to1/bench.py",
        "domain_1to1/bench_cli.py",
        "stream_1to1/verify.py",
        "stream_1to1/bench.py",
        "stream_1to1/stream_common.py",
        "db_1to1/verify.py",
        "verify_1to1/verify.py",
    ):
        assert "import _refpkg" not in (ROOT / "benchmarks/regression" / name).read_text()


def test_clauses_g_and_h_are_retired():
    text = (ROOT / "benchmarks/regression/domain_1to1/verify.py").read_text()
    for retired in (
        "BusinessRulesEngine",
        "FidelityComparator",
        "rebuild_schema",
        "business_rules",
        "refengine_fidelity_comparator",
    ):
        assert retired not in text
