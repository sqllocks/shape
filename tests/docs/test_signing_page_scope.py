"""Keep experimental migration and vault instructions outside the signing page."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_migration_example_moves_with_its_manifest_entry() -> None:
    signing = (ROOT / "docs/SIGNING.md").read_text()
    migration = ROOT / "docs/MIGRATION.md"
    assert "## Signed artifacts and migration" not in signing
    assert "<!-- example: 4 -->" not in signing
    assert migration.exists()
    text = migration.read_text()
    assert "Status: experimental." in text
    assert "<!-- example: 4 -->" in text
    assert 'scripts/docs_example_setup.py" MIGRATION' in text
    items = json.loads((ROOT / "tests/docs/reference_examples.json").read_text())
    assert {"page": "docs/MIGRATION.md", "index": 4, "kind": "local"} in items
    assert {"page": "docs/SIGNING.md", "index": 4, "kind": "local"} not in items


def test_signing_scope_excludes_experimental_vault_and_query() -> None:
    signing = (ROOT / "docs/SIGNING.md").read_text()
    vault = (ROOT / "docs/VAULT.md").read_text()
    assert "A profile that has a value vault" not in signing
    assert "A profile that has a value vault" in vault
    assert "`diff`, `query`" not in signing
    assert "also check, diff, query, plan" not in signing
    assert "the Fabric plugin provides Azure Key Vault" not in signing
