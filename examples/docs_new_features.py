"""Local, synthetic exercises for docs/NEW_FEATURES.md; run in an empty directory."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

import shape


def write_json(path: str, value: Any) -> None:
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def request(command: str, *, version: str = "1.6", **args: Any) -> dict[str, Any]:
    """Exercise the CLI wire protocol and require a successful response envelope."""
    payload = {"api_version": version, "id": command, "command": command, "args": args}
    run = subprocess.run(
        ["shape", "bridge"],
        input=json.dumps(payload) + "\n",
        text=True,
        capture_output=True,
        check=True,
    )
    response = json.loads(run.stdout)
    assert response["ok"], response
    return response["result"]


def locales() -> None:
    """Draw from each newly shipped list, including a complete GB name."""
    columns = {}
    for locale, providers in {
        "GB": ["first_name", "last_name", "name"],
        "CA": ["first_name"],
        "AU": ["first_name"],
        "DE": ["first_name"],
        "FR": ["last_name"],
    }.items():
        for provider in providers:
            name = f"{locale.lower()}_{provider}"
            columns[name] = {
                "name": name,
                "type": "string",
                "generator": {"strategy": "locale", "locale": locale, "provider": provider},
            }
    schema = {
        "schema_version": 1,
        "model": {"name": "names", "seed": 7},
        "tables": {"names": {"name": "names", "columns": columns}},
        "generation": {"scale": "small", "scales": {"small": {"names": 3}}},
    }
    write_json("names.gen.json", schema)
    result = shape.generate(schema)
    print(json.dumps(result.tables["names"].to_pylist(), ensure_ascii=False, sort_keys=True))


def rules() -> None:
    """Write accepted proposals while preserving a pre-existing contract rule."""
    from shape.proposals import DecisionFile, Proposal

    decisions = DecisionFile.empty()
    proposal = Proposal(
        "rule:orders.id.unique",
        "rule",
        "orders.id.unique",
        {"columns": {"id": {"unique": True}}},
        0.9,
    )
    rejected = Proposal(
        "rule:orders.amount.nullable",
        "rule",
        "orders.amount.nullable",
        {"columns": {"amount": {"nullable": False}}},
        0.8,
    )
    decisions.update([proposal, rejected])
    decisions.decide(proposal.id, "accepted", actor="docs")
    decisions.decide(rejected.id, "rejected", actor="docs")
    decisions.write("decisions.json")
    write_json("rules.contract.json", {"row_count": {"min": 1}})
    print("Prepared one accepted and one rejected rule; existing row_count.min=1")


def suite() -> None:
    """Select generated local tables, then run a scenario without modifying those inputs."""
    selected = Path("selected")
    selected.mkdir()
    generated = shape.generate("retail", scale="small", seed=42)
    for name, table in generated.tables.items():
        pq.write_table(table, selected / f"{name}.parquet")
    before = {p.name: p.read_bytes() for p in selected.iterdir()}
    write_json(
        "selected.suite.json",
        {
            "format": "shape-suite",
            "version": 1,
            "scenarios": ["clean_baseline", "nulls_injected"],
        },
    )
    result = request(
        "suite_run",
        version="1.5",
        suite="selected.suite.json",
        input="selected",
        name="daily",
    )
    assert before == {p.name: p.read_bytes() for p in selected.iterdir()}
    print(
        json.dumps(
            {
                "suite": result["suite"],
                "scenarios": [
                    {"scenario": s["scenario"], "met": s["met"]} for s in result["scenarios"]
                ],
                "inputs_unchanged": True,
            },
            sort_keys=True,
        )
    )
    card = request("report_card", version="1.5", source="selected", path="selected", tiers=[1])
    assert card["passed"] is (card["overall"] == "pass")
    print(json.dumps({"overall": card["overall"], "passed": card["passed"]}, sort_keys=True))


def vault() -> None:
    """Authenticate, rotate and change policy on a disposable encrypted vault."""
    from shape.artifact.io import read_artifact
    from shape.vault.format import open_vault, seal_vault
    from shape.vault.ops import attach_vault

    # Random keys stay only in this process environment and its bridge children.
    old_key, new_key = os.urandom(32), os.urandom(32)
    os.environ["DOCS_VAULT_OLD"] = base64.b64encode(old_key).decode()
    os.environ["DOCS_VAULT_NEW"] = base64.b64encode(new_key).decode()
    Path(".gitignore").write_text("*.shapevault\n")
    profile = shape.profile(pa.table({"status": ["synthetic-a", "synthetic-b"]}), name="orders")
    shape.save(profile, "vault.shape")
    manifest, _ = read_artifact("vault.shape", notice=False)
    raw = seal_vault(
        {"orders.status": ("categories", {"categories": [["synthetic-a", 1]]})},
        manifest["shape_content_id"],
        old_key,
    )
    Path("orders.shapevault").write_bytes(raw)
    attach_vault("vault.shape", raw)
    status = request("vault_status", vault="orders.shapevault")
    print(json.dumps({"status": status["ok"], "columns": [c["column"] for c in status["columns"]]}))
    verified = request(
        "vault_verify",
        vault="orders.shapevault",
        shape="vault.shape",
        kek="env://DOCS_VAULT_OLD",
    )
    assert verified["ok"]
    print("Original profile/vault reference and authenticated payload: verified")
    args = {
        "vault": "orders.shapevault",
        "shape": "vault.shape",
        "kek": "env://DOCS_VAULT_OLD",
        "new_kek": "env://DOCS_VAULT_NEW",
        "out_shape": "rotated.shape",
        "out_vault": "rotated.shapevault",
    }
    plan = request("vault_rewrap", **args, dry_run=True)
    assert not Path("rotated.shapevault").exists()
    rotated = request("vault_rewrap", **args)
    assert rotated["ok"] and not rotated["dry_run"] and plan["dry_run"]
    assert request(
        "vault_verify",
        vault="rotated.shapevault",
        shape="rotated.shape",
        kek="env://DOCS_VAULT_NEW",
    )["ok"]
    print("Rotation: dry run writes nothing; actual write creates a verified new pair")
    write_json(
        "vault-policy.json",
        {
            "format": "shape-vault-policy",
            "version": 1,
            "default": "none",
            "columns": {"orders.status": "categories"},
            "by_classification": {"PII": "extremes"},
        },
    )
    policy = request("vault_policy", policy="vault-policy.json", column="orders.status")
    print("Policy-file lookup: " + policy["effective"])
    before_payload = (
        open_vault(Path("rotated.shapevault").read_bytes(), new_key)
        .columns["orders.status"]
        .payload
    )
    changed = request(
        "vault_policy",
        vault="rotated.shapevault",
        kek="env://DOCS_VAULT_NEW",
        column="orders.status",
        new_policy="none",
    )
    after = open_vault(Path("rotated.shapevault").read_bytes(), new_key).columns["orders.status"]
    assert after.policy == "none" and after.payload == before_payload
    assert changed["before_policy"]["columns"]["orders.status"] == "categories"
    print("Authenticated policy write: categories -> none; retained payload is unchanged")
    stale = request(
        "vault_verify",
        vault="rotated.shapevault",
        shape="rotated.shape",
        kek="env://DOCS_VAULT_NEW",
    )
    assert not stale["ok"]
    print("Old profile reference after policy write: fails verification")


if __name__ == "__main__":
    {"locales": locales, "rules": rules, "suite": suite, "vault": vault}[sys.argv[1]]()
