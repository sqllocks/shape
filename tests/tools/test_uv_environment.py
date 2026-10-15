"""Developer-environment contracts; runnable before bootstrap with the standard library."""

import importlib.util
import shlex
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class UvEnvironmentTests(unittest.TestCase):
    def test_workspace_and_groups(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(config["tool"]["uv"]["required-version"], "==0.8.17")
        self.assertEqual(config["tool"]["uv"]["workspace"]["members"], ["plugins/*"])
        self.assertEqual(len(list(ROOT.glob("plugins/*/pyproject.toml"))), 12)
        self.assertEqual(config["project"]["requires-python"], ">=3.11")
        self.assertEqual(config["build-system"]["build-backend"], "maturin")
        self.assertIn("dbt-core==1.12.5", config["dependency-groups"]["docs"])
        self.assertIn("dbt-duckdb==1.11.0", config["dependency-groups"]["docs"])

    def test_make_commands_and_failure_propagation(self):
        for target, expected in (
            (
                "bootstrap",
                [
                    "sync",
                    "--locked",
                    "--group",
                    "docs",
                    "--group",
                    "dbt",
                    "--group",
                    "benchmark",
                ],
            ),
            ("lock", ["lock"]),
            ("lock-check", ["lock", "--check"]),
            (
                "test",
                [
                    "run",
                    "--locked",
                    "--group",
                    "docs",
                    "--group",
                    "dbt",
                    "--group",
                    "benchmark",
                    "pytest",
                    "-m",
                    "not emulator and not live",
                ],
            ),
        ):
            with self.subTest(target=target):
                result = subprocess.run(
                    ["make", "--silent", "--dry-run", target, "UV=uv"],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(shlex.split(result.stdout), ["uv", *expected])
        result = subprocess.run(
            ["make", "--silent", "lock-check", f'UV="{sys.executable}" -c "raise SystemExit(23)"'],
            cwd=ROOT,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_stale_lock_is_rejected_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            for path in (
                ROOT / "pyproject.toml",
                ROOT / "uv.lock",
                *ROOT.glob("plugins/*/pyproject.toml"),
            ):
                dest = tree / path.relative_to(ROOT)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(path.read_bytes())
            command = ["uv", "lock", "--check", "--offline", "--directory", str(tree)]
            clean = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(clean.returncode, 0, clean.stderr)
            project = tree / "pyproject.toml"
            # Add a new group without editing the published package metadata.
            text = project.read_text().replace(
                "[dependency-groups]", '[dependency-groups]\nstale-probe = ["numpy>=2"]'
            )
            project.write_text(text)
            stale = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(stale.returncode, 0)
            self.assertEqual((tree / "uv.lock").read_bytes(), (ROOT / "uv.lock").read_bytes())

    def test_published_metadata_comparison_detects_changes(self):
        spec = importlib.util.spec_from_file_location(
            "metadata_guard", ROOT / "scripts/check_published_metadata.py"
        )
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        baseline = (
            b"Metadata-Version: 2.4\nName: sample\nVersion: 1\n"
            b"Requires-Dist: a>=1\nProvides-Extra: dev\n\ndescription\n"
        )
        guard.compare_metadata(baseline, baseline)
        for old, new in (
            (b"sample", b"other"),
            (b"Version: 1", b"Version: 2"),
            (b"a>=1", b"a>=2"),
            (b"dev", b"docs"),
            (b"description", b"changed description"),
        ):
            with self.assertRaises(ValueError):
                guard.compare_metadata(baseline, baseline.replace(old, new))


if __name__ == "__main__":
    unittest.main()
