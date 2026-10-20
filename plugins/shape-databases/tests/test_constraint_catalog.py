"""Quoted catalog literals must stay opaque when comparing server expressions."""


def test_catalog_comparison_never_removes_literal_whitespace_or_parentheses():
    from shape_databases._constraint_catalog import _expression

    assert _expression("((amount >= 0))") == _expression("amount>=0")
    assert _expression("status = 'a b'") != _expression("status = 'ab'")
    assert _expression("status = '(a)'") != _expression("status = 'a'")


def test_mysql_show_create_only_reads_real_constraint_tokens():
    from shape_databases._constraint_catalog import _mysql_checks

    ddl = """CREATE TABLE `t` (
        `text` varchar(80) DEFAULT 'CONSTRAINT `fake` CHECK (1)',
        CONSTRAINT `real``name` CHECK ((`text` <> 'a) b'))
    )"""
    checks = _mysql_checks(ddl, backslashes=True)
    assert set(checks) == {"real`name"}
    assert "'a) b'" in checks["real`name"]


def test_mysql_backslash_modes_preserve_quoted_check_literals():
    from shape_databases._constraint_catalog import _mysql_checks

    for backslashes, value in [(True, r"'a\' b'"), (False, "'a\\'' b'")]:
        ddl = f"CREATE TABLE `t` (`s` text, CONSTRAINT `ck` CHECK (`s` <> {value}))"
        assert set(_mysql_checks(ddl, backslashes=backslashes)) == {"ck"}
