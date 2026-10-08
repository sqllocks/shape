"""W8-06 (#766): one switch turns realistic identifiers on for a run; reserved stays the default.

Items 1, 2 and 4 of the issue at the engine: the ``identifiers`` run switch and the schema's
``"identifiers"``, the precedence (column ``domains`` / ``range``, then the run switch, then the
schema, then ``reserved``), unknown values refused, the same values in both kernels and through a
schema rebuilt from ``engine.schema`` (a chunk worker or a Spark job), determinism, the default
output unchanged (dataset ids recorded from the code before this change), and the ``faker``
package's e-mail, URL, host and phone providers kept reserved by default (generator version 2,
version 1 unchanged).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pyarrow as pa
import pytest

from shape.builtins.strategies import providers
from shape.builtins.strategies.providers import RESERVED_DOMAINS
from shape.generation.engine import Engine
from shape.generation.identifiers import (
    IDENTIFIER_MODES,
    REALISTIC_NOTICE,
    announce,
    check_identifiers,
)
from shape.generation.schema import GenSchema, GenSchemaError
from shape.kernel import dispatch
from shape.repro import dataset_id

NATIVE = ["email", "company_email", "uri", "phone_number", "ssn"]
EXOTIC_EMAIL = ["free_email", "ascii_email", "ascii_free_email", "ascii_company_email"]
EXOTIC_URL = ["url", "image_url"]
EXOTIC_HOST = ["domain_name", "free_email_domain", "hostname", "dga"]
EXOTIC_PHONE = ["msisdn", "basic_phone_number"]
EXOTIC = EXOTIC_EMAIL + EXOTIC_URL + EXOTIC_HOST + EXOTIC_PHONE
REAL_EMAIL_DOMAINS = set(providers.pool("email_domains").to_pylist())
RESERVED_PHONE = re.compile(r"\([2-9]\d\d\) 555-01\d\d")

# The dataset id of ``doc(strategy, NATIVE)`` at its seed, recorded with the code before W8-06
# (``origin/int/INT-18`` 9886a7d8) in both kernel modes: the default output is unchanged.
BEFORE_NATIVE_ID = "sha256:af2a9714dfeab9d0fc4f2a22561779e2a1998403c2cfd641c0c046be829ce6bd"
# The installed domains at scale small, seed 42, recorded the same way. W8-04b (#768) re-recorded
# hr and healthcare once: their distribution columns are drawn with shape.kernel.pmath now (the
# old ids, 68c1d3ec... and df46193f..., held on this tree before that change and nothing else).
BEFORE_DOMAIN_IDS = {
    "retail": "sha256:64abbb26bc7b33d5cf8b0f5af993553077e33311759aab908d4e5d647e67a2f5",
    "hr": "sha256:e6a09f701cb4d3d6d329c71217fdc464343cc03b36a9bcab4a88a050b5aca6ca",
    "healthcare": "sha256:09bef3ac56e22f236eec89b97fe1955b12930e47bbb61fa75e3d6111e6847cb0",
}


def doc(
    strategy: str,
    names: list[str],
    rows: int = 300,
    *,
    options: dict[str, Any] | None = None,
    **top: Any,
) -> dict[str, Any]:
    columns: dict[str, Any] = {
        "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}}
    }
    for name in names:
        generator = {"strategy": strategy, "provider": name, **(options or {}).get(name, {})}
        columns[name] = {"name": name, "type": "string", "generator": generator}
    return {
        "schema_version": 1,
        "model": {"name": "ids", "seed": 7},
        "tables": {"t": {"name": "t", "primary_key": ["id"], "columns": columns}},
        "generation": {"scale": "s", "scales": {"s": {"t": rows}}},
        **top,
    }


def table(document: dict[str, Any], **engine: Any) -> pa.Table:
    return Engine(GenSchema.from_dict(document), **engine).generate().tables["t"]


def column(document: dict[str, Any], name: str, **engine: Any) -> list[str]:
    values: list[str] = table(document, **engine)[name].to_pylist()
    return values


@pytest.fixture(params=["rust", "python"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    assert dispatch.kernel_name() == request.param
    yield request.param
    dispatch.reset()


def realistic_email(value: str) -> bool:
    return value.split("@")[1] in REAL_EMAIL_DOMAINS


# ---- the modes and the checks ---------------------------------------------------------------


def test_the_modes_are_reserved_and_realistic() -> None:
    assert IDENTIFIER_MODES == ("reserved", "realistic")
    assert check_identifiers("reserved") == "reserved"
    assert check_identifiers("realistic") == "realistic"


@pytest.mark.parametrize("bad", ["", "Realistic", "assignable", "real", None, 1, True])
def test_any_other_value_is_refused_with_the_modes_named(bad: Any) -> None:
    with pytest.raises(ValueError, match=r"identifiers must be reserved or realistic, not"):
        check_identifiers(bad)


@pytest.mark.parametrize("bad", ["assignable", "REALISTIC", ""])
def test_the_engine_refuses_an_unknown_run_switch(bad: str) -> None:
    with pytest.raises(ValueError, match="identifiers must be reserved or realistic"):
        Engine(GenSchema.from_dict(doc("native", ["email"])), identifiers=bad)


@pytest.mark.parametrize("bad", ["assignable", "Reserved", "", 1, None])
def test_a_schema_with_an_unknown_value_does_not_load(bad: Any) -> None:
    with pytest.raises(GenSchemaError, match="identifiers"):
        GenSchema.from_dict(doc("native", ["email"], identifiers=bad))


def test_a_schema_value_set_in_code_is_checked_by_validate_and_the_engine() -> None:
    schema = GenSchema.from_dict(doc("native", ["email"]))
    schema.identifiers = "assignable"
    issues = [i for i in schema.validate() if i.location == "identifiers"]
    assert [(i.level, i.message) for i in issues] == [
        ("error", "must be reserved or realistic, not 'assignable'")
    ]
    with pytest.raises(ValueError, match="the schema's identifiers must be"):
        Engine(schema)


def test_the_announcement_is_one_line_for_realistic_and_nothing_for_reserved(
    capsys: pytest.CaptureFixture[str],
) -> None:
    announce("reserved")
    assert capsys.readouterr().err == ""
    announce("realistic")
    err = capsys.readouterr().err
    assert err == REALISTIC_NOTICE + "\n"
    assert "never use this data outside a test system" in err


# ---- the schema field -----------------------------------------------------------------------


def test_a_schema_without_the_field_reads_as_reserved_and_writes_nothing() -> None:
    document = doc("native", ["email"])
    schema = GenSchema.from_dict(document)
    assert schema.identifiers is None
    assert "identifiers" not in schema.to_dict()
    assert Engine(schema).identifiers == "reserved"


@pytest.mark.parametrize("mode", IDENTIFIER_MODES)
def test_the_schema_field_round_trips(mode: str) -> None:
    schema = GenSchema.from_dict(doc("native", ["email"], identifiers=mode))
    assert schema.identifiers == mode
    again = GenSchema.from_dict(json.loads(json.dumps(schema.to_dict())))
    assert again.identifiers == mode and again.to_dict() == schema.to_dict()


def test_the_published_spec_schema_takes_the_field() -> None:
    from shape.generation.spec_schema import published_schema
    from shape.schemacheck import validate

    published = published_schema()
    assert published["properties"]["identifiers"]["enum"] == ["reserved", "realistic"]
    assert not validate(doc("native", ["email"], identifiers="realistic"), published)
    assert validate(doc("native", ["email"], identifiers="assignable"), published)


# ---- precedence: column, run switch, schema, reserved ---------------------------------------


@pytest.mark.parametrize("strategy", ["native", "faker"])
def test_with_nothing_set_every_identifier_is_reserved(strategy: str) -> None:
    t = table(doc(strategy, NATIVE, 2000))
    assert {v.split("@")[1] for v in t["email"].to_pylist()} == set(RESERVED_DOMAINS)
    assert all(v.endswith(".example") for v in t["company_email"].to_pylist())
    assert {v.split("/")[2] for v in t["uri"].to_pylist()} == set(RESERVED_DOMAINS)
    assert all(RESERVED_PHONE.fullmatch(v) for v in t["phone_number"].to_pylist())
    assert all(900 <= int(v[:3]) <= 999 for v in t["ssn"].to_pylist())


@pytest.mark.parametrize("strategy", ["native", "faker"])
def test_the_run_switch_realistic_is_the_two_column_options_on_every_column(
    strategy: str,
) -> None:
    run = table(doc(strategy, NATIVE, 2000), identifiers="realistic")
    options = {name: {"domains": "realistic"} for name in ("email", "company_email", "uri")} | {
        name: {"range": "assignable"} for name in ("phone_number", "ssn")
    }
    per_column = table(doc(strategy, NATIVE, 2000, options=options))
    assert run.equals(per_column)
    assert all(realistic_email(v) for v in run["email"].to_pylist())
    assert all(v.endswith(".com") for v in run["company_email"].to_pylist())
    assert not any(RESERVED_PHONE.fullmatch(v) for v in run["phone_number"].to_pylist())
    assert min(int(v[:3]) for v in run["ssn"].to_pylist()) < 900


def test_the_run_switch_reserved_is_the_default_output() -> None:
    document = doc("native", NATIVE)
    assert table(document, identifiers="reserved").equals(table(document))


def test_the_schema_default_applies_without_a_run_switch() -> None:
    realistic = doc("native", NATIVE, identifiers="realistic")
    assert Engine(GenSchema.from_dict(realistic)).identifiers == "realistic"
    assert table(realistic).equals(table(doc("native", NATIVE), identifiers="realistic"))
    reserved = doc("native", NATIVE, identifiers="reserved")
    assert table(reserved).equals(table(doc("native", NATIVE)))


@pytest.mark.parametrize(("run", "schema"), [("reserved", "realistic"), ("realistic", "reserved")])
def test_the_run_switch_wins_over_the_schema(run: str, schema: str) -> None:
    got = table(doc("native", NATIVE, identifiers=schema), identifiers=run)
    want = table(doc("native", NATIVE), identifiers=run)
    assert got.equals(want)


@pytest.mark.parametrize(
    ("name", "key", "value"),
    [
        ("email", "domains", "reserved"),
        ("uri", "domains", "reserved"),
        ("company_email", "domains", "reserved"),
        ("phone_number", "range", "reserved"),
        ("ssn", "range", "reserved"),
    ],
)
def test_a_columns_own_key_wins_over_a_realistic_run_and_schema(
    name: str, key: str, value: str
) -> None:
    pinned = doc("native", NATIVE, identifiers="realistic", options={name: {key: value}})
    got = column(pinned, name, identifiers="realistic")
    assert got == column(doc("native", NATIVE), name)  # the reserved values


@pytest.mark.parametrize(
    ("name", "key", "value"),
    [("email", "domains", "realistic"), ("ssn", "range", "assignable")],
)
def test_a_columns_own_key_wins_over_a_reserved_run(name: str, key: str, value: str) -> None:
    got = column(doc("native", NATIVE, options={name: {key: value}}), name, identifiers="reserved")
    assert got == column(doc("native", NATIVE), name, identifiers="realistic")


def test_an_unknown_column_value_is_still_refused_under_a_run_switch() -> None:
    from shape.generation.strategy_kit import StrategyError

    bad = doc("native", ["email"], options={"email": {"domains": "assignable"}})
    with pytest.raises(StrategyError, match="takes domains reserved or realistic"):
        table(bad, identifiers="realistic")


def test_other_providers_do_not_change_with_the_switch() -> None:
    others = ["first_name", "name", "company", "city", "ipv4", "postcode", "word"]
    document = doc("native", others)
    assert table(document, identifiers="realistic").equals(table(document))


# ---- determinism, kernels, chunking and rebuilt engines ------------------------------------


@pytest.mark.parametrize("mode", IDENTIFIER_MODES)
def test_the_same_seed_and_mode_give_the_same_output(kernel: str, mode: str) -> None:
    document = doc("faker", NATIVE + EXOTIC_EMAIL + EXOTIC_PHONE)
    first = table(document, identifiers=mode, seed=11)
    assert table(document, identifiers=mode, seed=11).equals(first)
    assert not table(document, identifiers=mode, seed=12).equals(first)


def test_the_kernels_give_the_same_values_in_each_mode() -> None:
    pytest.importorskip("shape._kernel")
    script = (
        "import json, sys\n"
        "from shape.generation.engine import Engine\n"
        "from shape.generation.schema import GenSchema\n"
        "from shape.repro import dataset_id\n"
        "d = json.loads(sys.argv[1])\n"
        "print(json.dumps([dataset_id(Engine(GenSchema.from_dict(d), identifiers=m)"
        ".generate().tables) for m in ('reserved', 'realistic')]))\n"
    )
    document = json.dumps(doc("faker", NATIVE + EXOTIC))
    ids = {}
    for mode in ("rust", "python"):
        env = {**os.environ, "SHAPE_KERNEL": mode}
        done = subprocess.run(
            [sys.executable, "-c", script, document],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        ids[mode] = json.loads(done.stdout)
    assert ids["rust"] == ids["python"]
    assert ids["rust"][0] != ids["rust"][1]


@pytest.mark.parametrize("mode", IDENTIFIER_MODES)
def test_the_output_does_not_depend_on_the_chunk_size(mode: str) -> None:
    document = doc("native", NATIVE, 1000)
    whole = table(document, identifiers=mode)
    assert table(document, identifiers=mode, chunk_rows=97).equals(whole)


@pytest.mark.parametrize(("run", "schema"), [("realistic", None), ("reserved", "realistic")])
def test_an_engine_rebuilt_from_engine_schema_keeps_the_runs_mode(
    run: str, schema: str | None
) -> None:
    """A chunk worker and a Spark job rebuild the engine from ``engine.schema.to_dict()`` with no
    run switch: they generate what the run does."""
    top = {} if schema is None else {"identifiers": schema}
    engine = Engine(GenSchema.from_dict(doc("native", NATIVE, **top)), identifiers=run)
    assert engine.identifiers == run
    worker = Engine(GenSchema.from_dict(json.loads(json.dumps(engine.schema.to_dict()))))
    assert worker.identifiers == run
    assert worker.generate_chunk("t", 37, 100).equals(engine.generate_chunk("t", 37, 100))


def test_the_engine_never_changes_the_schema_it_was_given() -> None:
    schema = GenSchema.from_dict(doc("native", NATIVE))
    Engine(schema, identifiers="realistic")
    assert schema.identifiers is None


def test_the_multiprocess_chunk_worker_gets_the_mode(tmp_path: Any) -> None:
    from shape.scale import chunk_worker

    engine = Engine(GenSchema.from_dict(doc("native", NATIVE, 500)), identifiers="realistic")
    spec = {
        "key": "w8-06",
        "schema": engine.schema.to_dict(),
        "seed": engine.seed,
        "row_counts": dict(engine.row_counts),
        "chunk_rows": 100,
    }
    chunk_worker._ENGINES.clear()
    try:
        assert chunk_worker._engine(spec).identifiers == "realistic"
        rebuilt = chunk_worker._engine(spec).generate_chunk("t", 0, 500)
    finally:
        chunk_worker._ENGINES.clear()
    assert rebuilt.equals(engine.generate_chunk("t", 0, 500))


def test_the_spark_worker_gets_the_mode() -> None:
    from shape.scale.spark_worker import make_engine

    engine = Engine(GenSchema.from_dict(doc("native", NATIVE, 200)), identifiers="realistic")
    spec = {
        "schema": engine.schema.to_dict(),
        "seed": engine.seed,
        "row_counts": dict(engine.row_counts),
        "chunk_rows": 50,
    }
    worker = make_engine(spec)
    assert worker.identifiers == "realistic"
    assert worker.generate_chunk("t", 0, 200).equals(engine.generate_chunk("t", 0, 200))


# ---- the default output is unchanged --------------------------------------------------------


@pytest.mark.parametrize("strategy", ["native", "faker"])
def test_the_default_identifier_output_is_what_it_was_before_the_switch(
    kernel: str, strategy: str
) -> None:
    document = doc(strategy, NATIVE)
    assert dataset_id({"t": table(document)}) == BEFORE_NATIVE_ID
    assert dataset_id({"t": table(document, identifiers="reserved")}) == BEFORE_NATIVE_ID


@pytest.mark.parametrize("domain", sorted(BEFORE_DOMAIN_IDS))
def test_the_default_output_of_a_domain_is_what_it_was(kernel: str, domain: str) -> None:
    from shape.generation.domains import load_domain

    schema = load_domain(domain).schema
    assert schema.identifiers is None  # the domains keep the reserved default
    tables = Engine(schema, scale="small", seed=42).generate().tables
    assert dataset_id(tables) == BEFORE_DOMAIN_IDS[domain]


# ---- the faker package's own identifier providers (item 4) -----------------------------------


def faker_values(provider: str, mode: str | None = None, **spec: Any) -> list[str]:
    return column(
        doc("faker", [provider], 2000, options={provider: spec}),
        provider,
        **({} if mode is None else {"identifiers": mode}),
    )


@pytest.mark.parametrize("provider", EXOTIC_EMAIL)
def test_faker_emails_are_reserved_by_default(provider: str) -> None:
    pytest.importorskip("faker")
    values = faker_values(provider)
    assert {v.split("@")[1] for v in values} == set(RESERVED_DOMAINS)
    assert all(re.fullmatch(r"[^@\s]+@example\.(com|org|net)", v) for v in values)


@pytest.mark.parametrize("provider", EXOTIC_URL)
def test_faker_urls_are_reserved_by_default(provider: str) -> None:
    pytest.importorskip("faker")
    values = faker_values(provider)
    assert {re.match(r"https?://([^/]*)", v).group(1) for v in values} == set(RESERVED_DOMAINS)  # type: ignore[union-attr]


@pytest.mark.parametrize("provider", EXOTIC_HOST)
def test_faker_hosts_are_reserved_by_default(provider: str) -> None:
    pytest.importorskip("faker")
    values = faker_values(provider)
    assert all(re.fullmatch(r"([a-z0-9-]+\.)?example\.(com|org|net)", v) for v in values), values[
        :5
    ]


def test_faker_phone_numbers_are_the_fictional_lines_by_default() -> None:
    pytest.importorskip("faker")
    assert all(RESERVED_PHONE.fullmatch(v) for v in faker_values("basic_phone_number"))
    msisdn = faker_values("msisdn")
    assert all(re.fullmatch(r"1[2-9]\d\d55501\d\d", v) for v in msisdn)


@pytest.mark.parametrize("provider", EXOTIC_EMAIL)
def test_faker_emails_are_realistic_when_the_run_or_the_column_asks(provider: str) -> None:
    pytest.importorskip("faker")
    by_run = faker_values(provider, "realistic")
    assert not {v.split("@")[1] for v in by_run} & set(RESERVED_DOMAINS)
    assert by_run == faker_values(provider, domains="realistic")
    # the local part is the package's in both modes
    assert [v.split("@")[0] for v in by_run] == [v.split("@")[0] for v in faker_values(provider)]


@pytest.mark.parametrize("provider", EXOTIC_URL + EXOTIC_HOST)
def test_faker_hosts_and_urls_are_realistic_when_asked(provider: str) -> None:
    pytest.importorskip("faker")
    values = faker_values(provider, "realistic")
    assert not any(re.search(r"(^|[./])example\.(com|org|net)(/|$)", v) for v in values)
    assert values == faker_values(provider, domains="realistic")


@pytest.mark.parametrize("provider", EXOTIC_PHONE)
def test_faker_phones_are_realistic_when_asked(provider: str) -> None:
    pytest.importorskip("faker")
    values = faker_values(provider, "realistic")
    assert not any("55501" in v.replace("-", "").replace(" ", "") for v in values[:200])
    assert values == faker_values(provider, range="assignable")


def test_a_columns_own_key_wins_for_faker_providers_too() -> None:
    pytest.importorskip("faker")
    assert faker_values("free_email", "realistic", domains="reserved") == faker_values("free_email")
    assert faker_values("msisdn", "realistic", range="reserved") == faker_values("msisdn")


@pytest.mark.parametrize("provider", ["safe_email", "ascii_safe_email", "safe_domain_name"])
def test_the_safe_providers_of_the_package_are_left_alone(provider: str) -> None:
    pytest.importorskip("faker")
    values = faker_values(provider)
    assert values == faker_values(provider, "realistic")
    assert all(re.search(r"example\.(com|org|net)$", v) for v in values)


def test_other_faker_providers_do_not_change_with_the_switch() -> None:
    pytest.importorskip("faker")
    for provider in ("user_name", "job", "color_name", "domain_word"):
        assert faker_values(provider, "realistic") == faker_values(provider), provider


def test_a_faker_identifier_still_honours_max_length() -> None:
    pytest.importorskip("faker")
    document = doc("faker", ["free_email"], 200)
    document["tables"]["t"]["columns"]["free_email"]["max_length"] = 9
    assert all(len(v) <= 9 for v in column(document, "free_email"))


def test_the_faker_strategy_is_at_generator_version_2_and_keeps_1() -> None:
    from shape.generation import versions

    faker = providers.Faker()
    assert versions.generator_version(faker) == 2
    assert versions.supported(faker) == (1, 2)
    assert Engine(GenSchema.from_dict(doc("faker", ["free_email"]))).generator_versions == {
        "faker": 2,
        "sequence": 1,
    }


@pytest.mark.parametrize("version", [0, 3])
def test_a_faker_version_it_does_not_have_is_refused(version: int) -> None:
    from shape.generation.versions import GeneratorPinError

    pinned = doc("faker", ["email"], generators={"faker": version, "sequence": 1})
    with pytest.raises((GeneratorPinError, GenSchemaError), match="faker"):
        Engine(GenSchema.from_dict(pinned)).generate()


def test_faker_version_1_returns_what_the_package_makes(kernel: str) -> None:
    """A spec pinned at faker 1 keeps its data: the package's values, as before W8-06."""
    pytest.importorskip("faker")
    from shape.generation.rng import stream_key

    pins = {"faker": 1, "sequence": 1}
    for provider in EXOTIC:
        got = column(doc("faker", [provider], 300, generators=pins), provider)
        key = stream_key(7, "t", provider, "faker")
        want = providers._faker_pool("en_US", provider, "{}", key, 300).to_pylist()
        assert got == want, provider
        # a run switch does not change a version 1 provider of the package either
        pinned = doc("faker", [provider], 300, generators=pins)
        assert column(pinned, provider, identifiers="realistic") == want, provider


def test_faker_version_1_keeps_the_native_providers_and_the_switch() -> None:
    pins = {"faker": 1, "sequence": 1}
    document = doc("faker", NATIVE, generators=pins)
    assert dataset_id({"t": table(document)}) == BEFORE_NATIVE_ID
    assert table(document, identifiers="realistic").equals(
        table(doc("faker", NATIVE), identifiers="realistic")
    )


def test_realistic_faker_identifiers_are_what_the_package_makes(kernel: str) -> None:
    """Realistic is the package's own values, unchanged (what the strategy returned before W8-06);
    reserved changes only the host or the number."""
    pytest.importorskip("faker")
    from shape.generation.rng import stream_key

    for provider in EXOTIC:
        key = stream_key(7, "t", provider, "faker")
        want = providers._faker_pool("en_US", provider, "{}", key, 300).to_pylist()
        document = doc("faker", [provider], 300)
        assert column(document, provider, identifiers="realistic") == want, provider
        assert column(document, provider) != want, provider
