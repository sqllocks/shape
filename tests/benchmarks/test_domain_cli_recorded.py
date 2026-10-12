"""The CLI verifier selects CLI output and authenticates it against the API recording."""

from .test_domain_verify_recorded import sample, verifier


def test_cli_selection_uses_cli_seed42_and_candidate1042(monkeypatch, tmp_path):
    v = verifier()
    raw, reference, pools = sample()
    candidate = reference
    monkeypatch.setattr(v, "load_schema_json", lambda domain: raw)
    monkeypatch.setattr(v, "load", lambda *a, **kw: {"seeds": {}})
    monkeypatch.setattr(v, "sha256", lambda *a: "a" * 64)
    monkeypatch.setattr(v, "authenticate_run", lambda *a, **kw: None)
    monkeypatch.setattr(v, "manifest", lambda: {"shape_commit": "a" * 40})
    monkeypatch.setattr(v, "Pools", lambda *a: pools)
    calls = []

    def load_run(tool, domain, scale, seed, tables):
        calls.append((tool, seed))
        return candidate if tool == "shape-cli" else reference

    monkeypatch.setattr(v, "load_run", load_run)
    assert v.main(["--cli", "--no-generate", "--report", str(tmp_path / "report.json")]) == 0
    assert ("baseline-cli", 42) in calls
    assert ("shape-cli", 1042) in calls
    assert {(tool, seed) for tool, seed in calls if seed in (43, 44, 45, 46)} == {
        ("baseline", seed) for seed in (43, 44, 45, 46)
    }
