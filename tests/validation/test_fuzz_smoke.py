"""P7-04: a short seeded run of the artifact fuzzer in the normal suite. The nightly job
(``scripts/fuzz_artifacts.py``, fresh seed, many more iterations) does the real hunting; a
finding there becomes a regression case in this file."""

from __future__ import annotations

import random

import pytest

from shape.validation import fuzz

pytestmark = pytest.mark.security

SMOKE_SEED = 20261002
SMOKE_ITERATIONS = 40


def test_smoke_run_has_no_findings():
    findings = fuzz.run_fuzz(SMOKE_SEED, SMOKE_ITERATIONS)
    assert not findings, "\n".join(str(f) for f in findings)


def test_runs_are_deterministic():
    rng_a, rng_b = random.Random(7), random.Random(7)
    data = b"some bytes to mutate" * 4
    assert fuzz.mutate_bytes(rng_a, data) == fuzz.mutate_bytes(rng_b, data)
    obj = {"a": [1, 2, {"b": "c"}], "d": 1}
    assert fuzz.mutate_json(rng_a, obj) == fuzz.mutate_json(rng_b, obj)


@pytest.mark.parametrize("use_alarm", [True, False], ids=["sigalrm", "worker-thread"])
def test_harness_reports_unexpected_exceptions_and_hangs(monkeypatch, use_alarm):
    # The worker-thread limit is what Windows runs (no SIGALRM); force it on every platform.
    if not use_alarm:
        monkeypatch.setattr(fuzz, "_alarm_available", lambda: False)

    def boom(data, scratch, seeds):
        raise TypeError("a parser bug")

    def hang(data, scratch, seeds):
        import time

        time.sleep(5)

    def rejects(data, scratch, seeds):
        raise ValueError("a documented rejection")

    gen = fuzz.TARGETS[0].gen
    monkeypatch.setattr(
        fuzz,
        "TARGETS",
        (
            fuzz._TargetSpec("boom", boom, gen),
            fuzz._TargetSpec("hang", hang, gen),
            fuzz._TargetSpec("rejects", rejects, gen),
        ),
    )
    found = fuzz.run_fuzz(1, 1, time_limit=0.2)
    by_target = {f.target: f.error for f in found}
    assert by_target["boom"].startswith("TypeError")
    assert by_target["hang"].startswith("timeout")
    assert "rejects" not in by_target


def test_unknown_target_is_an_error():
    with pytest.raises(ValueError):
        fuzz.run_fuzz(1, 1, ["nope"])


# Regressions for what the fuzzer found.


def test_long_string_does_not_stall_the_safe_profile_validator():
    import time

    from shape.privacy.safe_validator import SafeProfileValidator

    doc = {"schema_version": 1, "tables": {}, "x": "a" * 200_000}
    start = time.monotonic()
    SafeProfileValidator().validate_data(doc)
    assert time.monotonic() - start < 2


def test_deeply_nested_yaml_is_a_pack_error(tmp_path):
    from shape.scenario.loader import PackError, PackLoader

    p = tmp_path / "deep.yaml"
    p.write_text("[" * 10_000)
    with pytest.raises(PackError):
        PackLoader().load(p)


def test_oversized_yaml_is_refused(tmp_path):
    from shape.scenario.loader import PackError, PackLoader
    from shape.security.yamlsafe import MAX_BYTES as MAX_YAML_BYTES

    p = tmp_path / "big.yaml"
    p.write_text("a: " + "x" * (MAX_YAML_BYTES + 1))
    with pytest.raises(PackError):
        PackLoader().load(p)


def test_malformed_signature_member_is_a_signature_error():
    from shape.artifact.io import ArtifactSignatureError
    from shape.artifact.signing import verify_manifest_signature

    pytest.importorskip("cryptography")
    with pytest.raises(ArtifactSignatureError):
        verify_manifest_signature(b"{}", b"[" * 5000, bytes(32))
