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
