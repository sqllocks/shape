"""Business-rule repairs that can run while later tables are still being generated.

:meth:`Engine.generate <shape.generation.engine.Engine.generate>` repairs rules after every table
is made and the compute phase has filled the ``computed`` columns, in the order the schema lists
them. A repair reads a few columns of two tables and rewrites one, so it does not have to wait for
tables it never reads: :class:`EarlyRules` runs the leading rules of the list on a helper thread
as soon as the tables they name exist, which takes the repair of ``order.order_date`` off the path
between the last table and the first write of ``order``.

The result is the one the plain order gives, because a rule runs early only when

* every rule before it has run (the rules apply in schema order, whichever the thread),
* each table it names is complete, and each column it reads or rewrites is generated, not
  ``computed`` (the compute phase would otherwise have filled it first), and
* the compute phase reads none of the columns it rewrites (a sum over the repaired column must see
  the values the plain order sees: those before the repair).

The generated tables themselves are never changed: strategies keep reading the values the plain
order lets them read. The repaired tables are returned separately, to replace the generated ones
when the compute phase starts.

A repair is also a *row-local* step (see :func:`~shape.generation.rules.fix_rule`), so for most
rules it need not wait for the whole table: :func:`plan_streamed_rules` finds the tables whose
every rule can be applied to each chunk as the chunk is made, which makes such a table final at
generation, so it is written while later tables are generated instead of after every post-pass.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass

import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.rules import can_repair, fix_rule, parse_comparison
from shape.generation.schema import BusinessRule, GenSchema


def _compute_inputs(schema: GenSchema) -> set[tuple[str, str]]:
    """Every ``(table, column)`` the compute phase reads, found the way
    :func:`~shape.generation.compute.apply_compute_phase` finds them: the parent's key, the
    child's foreign key and the column it aggregates; for ``lookup_parent`` the foreign key to the
    parent, the parent's key and the column it copies."""
    reads: set[tuple[str, str]] = set()
    for tname, tdef in schema.tables.items():
        for col in tdef.columns.values():
            if col.strategy != "computed":
                continue
            cfg = col.generator
            other, source = str(cfg.get("child_table", "")), str(cfg.get("child_column", ""))
            reads.add((other, source))
            if cfg.get("rule", "sum_children") == "lookup_parent":
                link = next(
                    (
                        c
                        for c in tdef.columns.values()
                        if c.fk_ref_table == other and c.fk_ref_column
                    ),
                    None,
                )
                if link is not None:
                    reads.update({(tname, link.name), (other, str(link.fk_ref_column))})
                continue
            reads.update((tname, key) for key in tdef.primary_key)
            if other in schema.tables:
                reads.update(
                    (other, c.name)
                    for c in schema.tables[other].columns.values()
                    if c.fk_ref_table == tname
                )
    return reads


def _columns_of(rule: BusinessRule) -> tuple[list[tuple[str, str]], str | None] | None:
    """``([(table, column) the rule reads or rewrites], the table it rewrites)``, or ``None`` for
    a rule :func:`fix_rule` leaves alone."""
    if not can_repair(rule):
        return None
    if rule.type == "cross_column":
        left, _op, right = parse_comparison(rule.rule)
        if not rule.table or not left or not right:
            return None
        return [(rule.table, left), (rule.table, right)], rule.table
    if rule.type == "cross_table":
        left, _op, right = parse_comparison(rule.rule)
        if not rule.via or "." not in left or "." not in right:
            return None
        ltable, lcol = left.split(".", 1)
        rtable, rcol = right.split(".", 1)
        return [(ltable, lcol), (rtable, rcol), (ltable, rule.via), (rtable, rule.via)], ltable
    return None


class EarlyRules:
    """Applies the leading rules of ``schema.business_rules`` on a helper thread, one batch per
    call of :meth:`advance`; :meth:`finish` waits for it and returns what it did."""

    def __init__(
        self,
        schema: GenSchema,
        seed: int,
        fetch: Callable[[str], pa.Table | None],
        skip: Collection[int] = (),
    ) -> None:
        self._skip = frozenset(skip)  # rules applied elsewhere (streamed per chunk)
        self._schema = schema
        self._seed = seed
        self._fetch = fetch
        self._rules = schema.business_rules
        self._reads = _compute_inputs(schema)
        self._thread: threading.Thread | None = None
        self.applied = 0
        self.tables: dict[str, pa.Table] = {}  # the repaired tables
        self._failed = False
        self._pending = 0

    # ---- planning ---------------------------------------------------------------------

    def _ready(self, rule: BusinessRule) -> bool:
        """Whether ``rule`` may run now (the rules before it have run)."""
        found = _columns_of(rule)
        if found is None:
            return True  # fix_rule leaves it alone
        columns, target = found
        assert target is not None
        for tname, cname in columns:
            tdef = self._schema.tables.get(tname)
            if (
                tdef is None
                or cname not in tdef.columns
                or tdef.columns[cname].strategy == "computed"
            ):
                return False
            if self._fetch(tname) is None:
                return False
        rewritten = columns[0]
        return rewritten not in self._reads

    def poll(self) -> None:
        """:meth:`advance` unless the helper thread is still on its last batch (it is asked
        again at the next table; :meth:`finish` waits for it)."""
        thread = self._thread
        if thread is None or not thread.is_alive():
            self.advance()

    def advance(self) -> None:
        """Wait for the helper thread, then start it on every further rule that can run now."""
        self._join()
        if self._failed:
            return
        batch: list[BusinessRule] = []
        position = self.applied
        while position < len(self._rules) and (
            position in self._skip or self._ready(self._rules[position])
        ):
            if position not in self._skip:
                batch.append(self._rules[position])
            position += 1
        if not batch or all(_columns_of(r) is None for r in batch):
            self.applied = position
            return
        self._pending = position
        self._thread = threading.Thread(
            target=self._work, args=(batch,), name="shape-rules", daemon=True
        )
        self._thread.start()

    def _work(self, batch: list[BusinessRule]) -> None:
        try:
            names = {t for rule in batch for t, _ in (_columns_of(rule) or ([], None))[0]}
            repaired: dict[str, pa.Table] = dict(self.tables)
            current = {n: t for n in names if (t := repaired.get(n) or self._fetch(n)) is not None}
            for rule in batch:
                after = fix_rule(rule, current, self._seed)
                repaired.update({n: t for n, t in after.items() if t is not current.get(n)})
                current = after
            self.tables = repaired
        except BaseException:  # the ordinary pass repeats the rules and reports the error
            self._failed = True

    def _join(self) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join()
            if not self._failed:
                self.applied = self._pending

    def finish(self) -> tuple[int, dict[str, pa.Table]]:
        """``(n, tables)``: the first ``n`` rules are done, ``tables`` are the repaired tables
        (empty after a failure, with ``n`` 0)."""
        self.advance()
        self._join()
        if self._failed:
            return 0, {}
        return self.applied, dict(self.tables)


# ---- repairs applied to each chunk ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreamPlan:
    """The rules applied to each chunk, by table: ``(index in the schema's list, rule)`` in the
    schema's order. A table is in the plan only when every rule that rewrites it is."""

    by_table: dict[str, tuple[tuple[int, BusinessRule], ...]]

    @property
    def indices(self) -> frozenset[int]:
        return frozenset(i for rules in self.by_table.values() for i, _ in rules)

    def __bool__(self) -> bool:
        return bool(self.by_table)


def plan_streamed_rules(
    schema: GenSchema, levels: list[list[str]], blocked: Collection[str] = ()
) -> StreamPlan:
    """The rules that can repair a chunk as it is made, with the same result as repairing the
    whole table afterwards in schema order.

    A table qualifies (all of its rules, or none) when ``blocked`` (tables a later pass changes
    anyway: ``computed`` or correlated columns) does not name it, and for each of its rules

    * the columns it reads and rewrites exist and none is ``computed``, and the compute phase
      reads none it rewrites (the conditions of :class:`EarlyRules`);
    * a ``cross_table`` rule reads a table of an earlier level, which is whole by then;
    * no rule of another table rewrites a column it reads unless that rule comes before it and is
      streamed too (it then reads the repaired column, as in the plain order), and no rule of
      another table reads a column it rewrites unless that rule comes after it (it reads the
      repaired column, as in the plain order).
    """
    level_of = {name: i for i, level in enumerate(levels) for name in level}
    reads = _compute_inputs(schema)
    rules = schema.business_rules
    found: dict[int, tuple[list[tuple[str, str]], str]] = {}
    for i, rule in enumerate(rules):
        columns = _columns_of(rule)
        if columns is not None and columns[1] is not None:
            found[i] = (columns[0], columns[1])
    rewrites = {i: cols[0] for i, (cols, _t) in found.items()}  # the rewritten (table, column)
    ok: set[int] = set()
    for i, (cols, table) in found.items():
        tables = schema.tables
        if table in blocked or table not in level_of:
            continue
        if any(
            t not in tables
            or c not in tables[t].columns
            or tables[t].columns[c].strategy == "computed"
            for t, c in cols
        ):
            continue
        if rewrites[i] in reads:
            continue
        if rules[i].type == "cross_table":
            other = cols[1][0]
            if other == table or other not in level_of or level_of[other] >= level_of[table]:
                continue
        ok.add(i)
    changed = True
    while changed:
        changed = False
        for i in sorted(ok):
            cols_i, table_i = found[i]
            bad = {j for j in found if j != i and found[j][1] == table_i and j not in ok}
            for j, (cols_j, table_j) in found.items():
                if table_j == table_i:
                    continue
                if rewrites[j] in cols_i and not (j < i and j in ok):
                    bad.add(j)
                if rewrites[i] in cols_j and not i < j:
                    bad.add(j)
            if bad:
                # all of a table's rules, or none: drop table_i
                drop = {k for k in ok if found[k][1] == table_i}
                ok -= drop
                changed = True
                break
    by_table: dict[str, list[tuple[int, BusinessRule]]] = {}
    for i in sorted(ok):
        by_table.setdefault(found[i][1], []).append((i, rules[i]))
    return StreamPlan({t: tuple(r) for t, r in by_table.items()})
