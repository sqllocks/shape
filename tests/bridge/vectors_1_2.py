"""The test vectors of the commands and arguments added in bridge 1.2 (W7-05).

``make_vectors.py`` merges these into the published vectors, as it merges ``vectors_1_1.py``:
``FILES`` are new vector files (one per new command) and ``EXTENDS`` adds setup and cases to the
vector files of earlier commands (their earlier cases stay as they are)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

D = "${DIR}"
V12 = "1.2"


def case(
    name: str,
    command: str,
    args: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    **flags: Any,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "api_version": V12,
        "id": name,
        "command": command,
        "args": args or {},
    }
    if options:
        request["options"] = options
    return {"name": name, "request": request, **flags}


def setup(command: str, **args: Any) -> dict[str, Any]:
    return {"api_version": V12, "id": "setup", "command": command, "args": args}


PROFILE_SHOP = setup("profile", source=f"{D}/shop", dataset=True, output=f"{D}/shop.shape")
PROPOSE_RULES = setup(
    "proposals_propose",
    profile=f"{D}/shop.shape",
    decisions=f"{D}/rules.json",
    kinds=["rule"],
    data=[f"{D}/shop"],
)


def accept(rule: str) -> dict[str, Any]:
    return setup(
        "proposals_decide", decisions=f"{D}/rules.json", proposal=rule, verb="accept", actor="ana"
    )


ACCEPTED = [
    accept("rule:customers.customer_id.unique"),
    accept("rule:orders.amount.dtype"),
    accept("rule:orders.row_count"),
]

CARD = setup(
    "report_card",
    real=f"{D}/report_real",
    synthetic=f"{D}/report_synthetic",
    output=f"{D}/card.json",
)
FEED = {"registry": f"{D}/feed_safe", "name": "orders"}
RAW_FEED = {"registry": f"{D}/feed_raw", "name": "orders"}

FILES: dict[str, dict[str, Any]] = {
    "proposals_contract": {
        "setup": [PROFILE_SHOP, PROPOSE_RULES, *ACCEPTED],
        "cases": [
            case(
                "accepted-rules",
                "proposals_contract",
                {"decisions": f"{D}/rules.json", "output": f"{D}/contract_rules.json"},
            ),
            case(
                "merge",
                "proposals_contract",
                {
                    "decisions": f"{D}/rules.json",
                    "output": f"{D}/contract_merged.json",
                    "merge": f"{D}/contract_base.json",
                },
            ),
            case(
                "conflict",
                "proposals_contract",
                {
                    "decisions": f"{D}/rules.json",
                    "output": f"{D}/contract_conflict.json",
                    "merge": f"{D}/contract_conflicting.json",
                },
            ),
            case(
                "missing-output",
                "proposals_contract",
                {"decisions": f"{D}/rules.json"},
                valid_request=False,
            ),
        ],
    },
    "report_card": {
        "cases": [
            case(
                "synthetic-against-real",
                "report_card",
                {"real": f"{D}/report_real", "synthetic": f"{D}/report_synthetic"},
            ),
            case(
                "written-too",
                "report_card",
                {
                    "real": f"{D}/report_real",
                    "synthetic": f"{D}/report_synthetic",
                    "tiers": [1],
                    "output": f"{D}/card_written.json",
                },
            ),
            case(
                "missing-real-data",
                "report_card",
                {"real": f"{D}/none", "synthetic": f"{D}/report_synthetic"},
            ),
            case(
                "tiers-is-an-array",
                "report_card",
                {"real": "a", "synthetic": "b", "tiers": "1"},
                valid_request=False,
            ),
        ]
    },
    "report_card_read": {
        "setup": [CARD],
        "cases": [
            case("stored-card", "report_card_read", {"path": f"{D}/card.json"}),
            case("newer-card", "report_card_read", {"path": f"{D}/card_newer.json"}),
            case("needs-a-path", "report_card_read", {}, valid_request=False),
        ],
    },
    "rules_mutate": {
        "cases": [
            case(
                "customers",
                "rules_mutate",
                {
                    "data": f"{D}/mutate_data",
                    "contract": f"{D}/contract_customers.json",
                    "seed": 3,
                    "min_score": 0.5,
                },
            ),
            case(
                "missing-contract",
                "rules_mutate",
                {"data": f"{D}/mutate_data", "contract": f"{D}/none.json"},
            ),
            case(
                "rate-out-of-range",
                "rules_mutate",
                {"data": "d", "contract": "c", "rate": 2},
                valid_request=False,
            ),
        ]
    },
    "rules_backtest": {
        "cases": [
            case("safe-history", "rules_backtest", {**FEED, "contract": f"{D}/contract_feed.json"}),
            case(
                "weekly",
                "rules_backtest",
                {**RAW_FEED, "contract": f"{D}/contract_feed.json", "window": "week"},
            ),
            case(
                "unknown-name",
                "rules_backtest",
                {
                    "registry": f"{D}/feed_safe",
                    "name": "ghost",
                    "contract": f"{D}/contract_feed.json",
                },
            ),
            case(
                "bad-window",
                "rules_backtest",
                {**FEED, "contract": "c", "window": "year"},
                valid_request=False,
            ),
        ]
    },
    "bisect": {
        "cases": [
            case("first-bad-version", "bisect", {**RAW_FEED, "good": "first", "bad": "last"}),
            case("good-tests-bad", "bisect", {**RAW_FEED, "good": "last", "bad": "first"}),
            case("needs-good-and-bad", "bisect", {**RAW_FEED}, valid_request=False),
        ]
    },
    "bisect_layers": {
        "cases": [
            case(
                "second-layer",
                "bisect_layers",
                {
                    "layers": ["raw", "clean"],
                    "good_date": "2026-03-01",
                    "bad_date": "2026-03-03",
                    "project": f"{D}/layers/shape.yml",
                },
            ),
            case(
                "needs-a-project",
                "bisect_layers",
                {"layers": ["raw", "clean"], "good_date": "2026-03-01", "bad_date": "2026-03-03"},
            ),
            case(
                "layers-is-an-array",
                "bisect_layers",
                {"layers": "raw", "good_date": "a", "bad_date": "b"},
                valid_request=False,
            ),
        ]
    },
    "timelapse": {
        "cases": [
            case("note-over-six-days", "timelapse", {**FEED, "column": "note"}),
            case("unknown-column", "timelapse", {**FEED, "column": "ghost"}),
            case("needs-a-column", "timelapse", {**FEED}, valid_request=False),
        ]
    },
    "registry_diff": {
        "cases": [
            case(
                "safe-forms",
                "registry_diff",
                {"root": f"{D}/feed_safe", "name": "orders", "ref1": "first", "ref2": "last"},
            ),
            case(
                "raw-forms",
                "registry_diff",
                {"root": f"{D}/feed_raw", "name": "orders", "ref1": "first", "ref2": "last"},
            ),
            case(
                "unknown-name",
                "registry_diff",
                {"root": f"{D}/feed_safe", "name": "ghost", "ref1": "first", "ref2": "last"},
            ),
            case(
                "needs-two-refs", "registry_diff", {"root": "r", "name": "n"}, valid_request=False
            ),
        ]
    },
    "chaos": {
        "cases": [
            case(
                "generated-tables",
                "chaos",
                {
                    "output_dir": f"{D}/chaos_out",
                    "domain": f"{D}/schema.json",
                    "corrupt": ["duplicates=0.05"],
                    "seed": 5,
                },
            ),
            case(
                "unmarked-input-refused",
                "chaos",
                {
                    "output_dir": f"{D}/chaos_refused",
                    "input": f"{D}/unmarked",
                    "corrupt": ["duplicates=0.5"],
                },
            ),
            case(
                "real-input-allowed",
                "chaos",
                {
                    "output_dir": f"{D}/chaos_real",
                    "input": f"{D}/unmarked",
                    "corrupt": ["duplicates=0.5"],
                    "allow_real_input": True,
                },
            ),
            case(
                "not-local",
                "chaos",
                {
                    "output_dir": "s3://bucket/out",
                    "input": f"{D}/unmarked",
                    "corrupt": ["duplicates=0.5"],
                },
            ),
            case("needs-a-corruption", "chaos", {"output_dir": "o"}, valid_request=False),
        ]
    },
}

EXTENDS: dict[str, dict[str, Any]] = {
    "proposals_propose": {
        "cases": [
            case(
                "rules",
                "proposals_propose",
                {
                    "profile": f"{D}/shop.shape",
                    "decisions": f"{D}/rules.json",
                    "kinds": ["rule"],
                    "data": [f"{D}/shop"],
                },
            ),
        ]
    },
}


def _write_registries(folder: Path) -> None:
    """The registries of the vectors: ``feed_safe`` and ``feed_raw`` (six daily versions of
    ``orders``: the null rate of `note` and the range of `amount` change from the fourth day) and
    ``layers`` (a project of two layers)."""
    import random
    import shutil
    import tempfile
    from unittest import mock

    import pyarrow as pa
    from data_1_2 import START, day_table, safe_bytes

    import shape
    from shape.registry import LocalRegistry

    for name in ("feed_safe", "feed_raw", "layers"):
        shutil.rmtree(folder / name, ignore_errors=True)
    clock = iter(range(1_800_000_000, 1_800_001_000))
    with mock.patch("shape.registry.local.time.time", lambda: float(next(clock))):
        for form in ("safe", "raw"):
            reg = LocalRegistry(folder / f"feed_{form}")
            for day in range(6):
                table = day_table(day, null_from=3, wide_from=3, rows=200)
                profile = shape.profile(table, name="orders", sketches=True)
                meta = {"business_date": f"2026-03-0{day + 1}", "profile_form": form}
                if form == "safe":
                    reg.commit("orders", safe_bytes(profile), meta)
                else:
                    with tempfile.TemporaryDirectory() as tmp:
                        path = Path(tmp) / "p.shape"
                        shape.save(profile, str(path))
                        reg.commit("orders", path.read_bytes(), meta, allow_raw=True)
            log = reg.log("orders")
            reg.tag("orders", "first", log[0]["content_id"])
            reg.tag("orders", "last", log[-1]["content_id"])
        layers = folder / "layers"
        reg = LocalRegistry(layers / "reg")
        for layer, shift in (("raw", False), ("clean", True)):
            for day in range(3):
                rng = random.Random(7 + day)
                bump = 1.4 if shift and day >= 2 else 1.0
                table = pa.table(
                    {
                        "total": [round(rng.gauss(100, 12) * bump, 3) for _ in range(300)],
                        "status": [rng.choice(["new", "paid"]) for _ in range(300)],
                    }
                )
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "p.shape"
                    shape.save(
                        shape.profile(table, name=f"layer_{layer}", sketches=True), str(path)
                    )
                    reg.commit(
                        f"layer_{layer}",
                        path.read_bytes(),
                        {"business_date": (START + _day(day)).isoformat()},
                        allow_raw=True,
                    )
        lines = ["format: shape-project", "version: 1", "sources:"]
        for layer in ("raw", "clean"):
            lines += [
                f"  {layer}:",
                f"    path: {layer}.csv",
                "    baseline:",
                "      kind: previous_run",
                "      registry: reg",
                f"      name: layer_{layer}",
            ]
        (layers / "shape.yml").write_text("\n".join(lines) + "\n")


def _day(n: int) -> Any:
    import datetime as dt

    return dt.timedelta(days=n)


def write_fixtures(folder: Path) -> None:
    from data_1_2 import real_tables, synthetic_tables, write_tables

    (folder / "unmarked").mkdir(exist_ok=True)
    (folder / "unmarked" / "orders.csv").write_text("id,amount\n1,5\n2,6\n3,7\n4,8\n")

    write_tables(folder / "report_real", real_tables())
    write_tables(folder / "report_synthetic", synthetic_tables())
    write_tables(folder / "mutate_data", real_tables())
    (folder / "contract_customers.json").write_text(
        json.dumps(
            {
                "columns": {
                    "customer_id": {"dtype": "integer", "unique": True},
                    "tier": {"allowed_values": ["gold", "silver", "bronze"]},
                    "amount": {"min": 0},
                    "email": {"nullable": False},
                },
                "row_count": {"min": 50},
            },
            indent=2,
        )
        + "\n"
    )
    (folder / "contract_feed.json").write_text(
        json.dumps(
            {
                "columns": {
                    "note": {"max_null_rate": 0.1},
                    "status": {"allowed_values": ["new", "paid", "shipped"]},
                    "amount": {"max": 12000},
                },
                "row_count": {"min": 100},
            },
            indent=2,
        )
        + "\n"
    )
    (folder / "contract_base.json").write_text(
        json.dumps({"tables": {"customers": {"row_count": {"min": 1}}}}, indent=2) + "\n"
    )
    (folder / "contract_conflicting.json").write_text(
        json.dumps({"tables": {"orders": {"row_count": {"min": 5}}}}, indent=2) + "\n"
    )
    (folder / "card_newer.json").write_text(
        json.dumps(
            {"format": "shape-report-card", "version": 2, "inputs": {}, "sections": {}}, indent=2
        )
        + "\n"
    )
    _write_registries(folder)
