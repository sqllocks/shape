"""Parser for only the bounded CHECK expression vocabulary emitted in offline tests."""

from __future__ import annotations

import operator
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

_TOKEN = re.compile(
    r"\s*(\[(?:[^\]]|\]\])*\]|N?'(?:[^']|'')*'|[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?|>=|<=|<>|!=|[=><(),+]|AND\b|IN\b|IS\b|NULL\b|N?CHAR\b)"
)
_COMPARE = {
    "=": operator.eq,
    "!=": operator.ne,
    "<>": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
}


def predicate(expression: str) -> Callable[[dict[str, Any]], bool]:
    """Parse generated comparisons/conjunctions/enums/null checks, never evaluate SQL/Python."""
    tokens: list[str] = []
    offset = 0
    while offset < len(expression):
        match = _TOKEN.match(expression, offset)
        if match is None:
            raise RuntimeError("the fake cannot read this CHECK expression")
        tokens.append(match[1])
        offset = match.end()
    if len(tokens) > 4096:
        raise RuntimeError("the fake CHECK expression is too large")
    position = 0

    def take(wanted: str | None = None) -> str:
        nonlocal position
        if position >= len(tokens) or (wanted is not None and tokens[position] != wanted):
            raise RuntimeError("the fake cannot read this CHECK expression")
        token = tokens[position]
        position += 1
        return token

    def operand() -> Callable[[dict[str, Any]], Any]:
        token = take()
        if token.startswith("["):
            column = token[1:-1].replace("]]", "]")
            return lambda row: row[column]
        if token == "NULL":
            value: Any = None
        elif token in ("CHAR", "NCHAR"):
            take("(")
            value = chr(int(take()))
            take(")")
        elif token.startswith(("'", "N'")):
            value = token[token.index("'") + 1 : -1].replace("''", "'")
        else:
            value = Decimal(token)
        if position < len(tokens) and tokens[position] == "+":
            take("+")
            right = operand()
            return lambda row: value + right(row)
        return lambda row: value

    def expression_node(depth: int = 0) -> Callable[[dict[str, Any]], Any]:
        if depth > 32:
            raise RuntimeError("the fake CHECK expression is too deep")
        take("(")
        if position < len(tokens) and tokens[position] == "(":
            children = [expression_node(depth + 1)]
            while position < len(tokens) and tokens[position] == "AND":
                take("AND")
                children.append(expression_node(depth + 1))
            take(")")

            def conjunction(row: dict[str, Any]) -> Any:
                values = [child(row) for child in children]
                return False if False in values else None if None in values else True

            return conjunction
        left = operand()
        op = take()
        if op == "IS":
            take("NULL")

            def result(row: dict[str, Any]) -> Any:
                return left(row) is None
        elif op == "IN":
            take("(")
            values = [operand()]
            while position < len(tokens) and tokens[position] == ",":
                take(",")
                values.append(operand())
            take(")")

            def result(row: dict[str, Any]) -> Any:
                value, choices = left(row), [item(row) for item in values]
                return (
                    True
                    if value is not None and value in choices
                    else (None if value is None or None in choices else False)
                )
        else:
            if op not in _COMPARE:
                raise RuntimeError("the fake cannot read this CHECK operator")
            right = operand()

            def result(row: dict[str, Any]) -> Any:
                a, b = left(row), right(row)
                return None if a is None or b is None else _COMPARE[op](a, b)

        take(")")
        return result

    check = expression_node()
    if position != len(tokens):
        raise RuntimeError("the fake cannot read trailing CHECK expression tokens")
    return lambda row: check(row) is not False


def scalar(text: str) -> Any:
    """The finite scalar spelling used by generated DEFAULT clauses in the fake."""
    parts = re.findall(
        r"N?'(?:[^']|'')*'|N?CHAR\(\d+\)|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|NULL|\+",
        text,
    )
    if "".join(parts) != re.sub(r"\s+(?=(?:[^']*'[^']*')*[^']*$)", "", text):
        raise RuntimeError("the fake cannot read this DEFAULT literal")
    result: Any = None
    expect_value = True
    for token in parts:
        if not expect_value:
            if token != "+":
                raise RuntimeError("the fake cannot read this DEFAULT literal")
            expect_value = True
            continue
        if token == "NULL":
            value: Any = None
        elif token.startswith(("CHAR(", "NCHAR(")):
            value = chr(int(token[token.index("(") + 1 : -1]))
        elif token.startswith(("'", "N'")):
            value = token[token.index("'") + 1 : -1].replace("''", "'")
        else:
            number = Decimal(token)
            value = int(number) if number == number.to_integral_value() else number
        result = value if result is None else result + value
        expect_value = False
    if expect_value:
        raise RuntimeError("the fake cannot read this DEFAULT literal")
    return result
