"""Additive CLI policies retain the legacy parser default and choices."""

import argparse

import pytest

from shape.cli.to import add_to_arguments


@pytest.mark.parametrize("policy", ["keys", "all", "none", "keep", "disable"])
def test_constraint_policy_is_accepted(policy):
    parser = argparse.ArgumentParser()
    add_to_arguments(parser)
    assert parser.parse_args(["--sql-constraints", policy]).sql_constraints == policy


def test_omitted_constraint_policy_keeps_parser_default_none():
    parser = argparse.ArgumentParser()
    add_to_arguments(parser)
    assert parser.parse_args([]).sql_constraints is None


def generation_args(*args):
    from shape.cli import generation

    parser = argparse.ArgumentParser()
    generation.add_arguments(parser.add_subparsers())
    return parser.parse_args(["generate", "retail", *args])


@pytest.mark.parametrize("fmt", ["sql", "delta"])
def test_file_constraint_policy_and_contract_reach_output(fmt, tmp_path):
    from shape.cli import generation

    path = tmp_path / "contract.json"
    path.write_text('{"columns": {"status": {"enum": ["new", "done"]}}}')
    args = generation_args("--format", fmt, "--sql-constraints", "all", "--contract", str(path))
    options = generation._sink_options(args)
    assert options["sql_constraints"] == "all"
    assert options["contract"] == {"columns": {"status": {"enum": ["new", "done"]}}}


def test_contract_reaches_target_options(tmp_path):
    from shape.cli.to import target_options

    path = tmp_path / "contract.json"
    path.write_text('{"columns": {"status": {"unique": true}}}')
    args = generation_args("--contract", str(path))
    assert target_options(args, "parquet").contract == {"columns": {"status": {"unique": True}}}


def test_contract_must_be_an_object(tmp_path):
    from shape.cli.to import target_options

    path = tmp_path / "contract.json"
    path.write_text("[]")
    args = generation_args("--contract", str(path))
    with pytest.raises(ValueError, match="contract must be an object"):
        target_options(args, "parquet")
