"""``shape bisect``: git bisect for data. Binary search over the committed versions of a name for
the first version that tests bad.

The default test is ``shape diff`` of a version against the good version (under the source's
thresholds and ignore lists, restricted to a column and a kind when given); with a contract a
version is bad when the contract fails. At most ``ceil(log2(n)) + 2`` versions are tested for n
candidates: the bad version, the good version (only a contract can fail it) and the search over
the others.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from shape.registry.local import LocalRegistry
from shape.versions._common import (
    FORMAT_BISECT,
    VERSION,
    as_path,
    column_matches,
    diff_profiles,
    drift_options,
    filtered_changes,
    json_safe,
)
from shape.versions.versions import HistoryError, Version, Versions

WINDOWS = ("week", "month")
PERSISTS = "bisect assumes the change persists once it appears"


class BisectResult:
    """The result of :func:`bisect`. ``to_dict()`` is the JSON of ``shape bisect --json``."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self._doc = doc

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._doc)

    @property
    def found(self) -> bool:
        return bool(self._doc["found"])

    @property
    def first_bad(self) -> dict[str, Any] | None:
        found: dict[str, Any] | None = copy.deepcopy(self._doc["first_bad"])
        return found

    @property
    def last_good(self) -> dict[str, Any] | None:
        found: dict[str, Any] | None = copy.deepcopy(self._doc["last_good"])
        return found

    @property
    def changes(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._doc["changes"])

    @property
    def candidates(self) -> int:
        return int(self._doc["candidates"])

    @property
    def evaluated(self) -> int:
        return int(self._doc["evaluated"])

    @property
    def warnings(self) -> list[str]:
        return list(self._doc["warnings"])

    def __repr__(self) -> str:
        first = self._doc["first_bad"]
        where = first["business_date"] or first["content_id"][:12] if first else "none"
        return f"BisectResult(first_bad={where}, evaluated={self.evaluated})"


def max_evaluations(candidates: int) -> int:
    """The most versions a bisect over ``candidates`` versions tests."""
    return math.ceil(math.log2(max(candidates, 1))) + 2


def _short(ref: str) -> str:
    return ref[:12] if len(ref) == 64 and not ref.strip("0123456789abcdef") else ref


def window_label(day: Any, window: str) -> str:
    if window == "week":
        iso = day.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return f"{day.year}-{day.month:02d}"


class _Tester:
    """Tests versions: bad or good, with the evidence for it."""

    def __init__(
        self,
        history: Versions,
        good: Version,
        *,
        column: str | None,
        kind: str | None,
        contract: str | None,
        options: dict[str, Any],
    ) -> None:
        self.history = history
        self.good = good
        self.column = column
        self.kind = kind
        self.contract = contract
        self.options = options
        self.version_tests: dict[str, bool] = {}
        self.order: list[dict[str, Any]] = []
        self.window_tests = 0
        self._evidence: dict[str, list[dict[str, Any]]] = {}

    def _judge(self, profile: Any, window: bool = False) -> tuple[bool, list[dict[str, Any]]]:
        if self.contract is not None:
            import shape

            result = shape.check(profile, self.contract)
            found = [
                json_safe(v)
                for v in result.violations
                if self.column is None or column_matches(v.get("column"), self.column)
            ]
            return bool(found), found
        base = self.history.profile(self.good)
        changes = diff_profiles(base, profile, self.options)
        if window:  # a window holds several versions' rows: its row count is not a day's
            changes = [c for c in changes if c["kind"] != "row_count_change"]
        found = filtered_changes(changes, self.column, self.kind)
        return bool(found), found

    def test(self, version: Version) -> bool:
        """Is this version bad? Each version is tested once."""
        known = self.version_tests.get(version.content_id)
        if known is not None:
            return known
        bad, found = self._judge(self.history.profile(version))
        self.version_tests[version.content_id] = bad
        self._evidence[version.content_id] = found
        self.order.append(
            {
                "content_id": version.content_id,
                "business_date": version.business_date,
                "bad": bad,
            }
        )
        return bad

    def evidence(self, version: Version) -> list[dict[str, Any]]:
        return self._evidence.get(version.content_id, [])

    def test_window(self, label: str, members: Sequence[Version]) -> bool:
        from shape.profile.merge import merge_profiles

        profiles = [self.history.profile(v) for v in members]
        if len(profiles) > 1:
            missing = [v for v, p in zip(members, profiles, strict=True) if p.sketches is None]
            if missing:
                raise HistoryError(
                    f"{self.history.name}@{missing[0].content_id[:12]} ({missing[0].date}) has no "
                    "sketch state, which --coarse needs to merge a window: commit profiles made "
                    "with `shape profile --sketches`, or leave --coarse out"
                )
            merged = merge_profiles(profiles, name=self.history.name)
        else:
            merged = profiles[0]
        bad, _ = self._judge(merged, window=True)
        self.window_tests += 1
        self.order.append({"window": label, "versions": len(members), "bad": bad})
        return bad


def _first_bad(lo: int, hi: int, is_bad: Callable[[int], bool]) -> int:
    """The first bad index in ``(lo, hi]`` given that ``lo`` is good and ``hi`` is bad."""
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if is_bad(mid):
            hi = mid
        else:
            lo = mid
    return hi


def bisect(
    registry: str | Path | LocalRegistry,
    name: str,
    *,
    good: str,
    bad: str,
    column: str | None = None,
    kind: str | None = None,
    contract: str | Path | None = None,
    project: Any = None,
    source: str | None = None,
    thresholds: dict[str, Any] | None = None,
    ignore_columns: list[str] | None = None,
    column_thresholds: dict[str, dict[str, Any]] | None = None,
    only_columns: list[str] | None = None,
    policy: dict[str, Any] | str | Path | None = None,
    verify_all: bool = False,
    coarse: str | None = None,
) -> BisectResult:
    """Find the first version of ``name`` that tests bad, between the versions ``good`` and
    ``bad`` (registry refs: ``latest``, a tag, a content id), ordered by ``business_date``.

    The test is ``shape.diff`` of a version against ``good``; ``thresholds``, ``ignore_columns``,
    ``column_thresholds``, ``only_columns`` and ``policy`` are its options and override those of
    the ``project`` source (``source``, else the only source, else the one named ``name``).
    ``column`` and ``kind`` restrict which changes count; ``contract`` replaces the test by "the
    contract fails". ``verify_all`` tests every version and reports any that flips back to good;
    ``coarse`` (``week`` or ``month``) first bisects over merged windows. Raises
    :class:`HistoryError` when ``good`` tests bad, ``bad`` tests good, or a version cannot be
    tested."""
    if coarse is not None and coarse not in WINDOWS:
        raise HistoryError(f"--coarse is week or month, not {coarse!r}")
    if coarse is not None and contract is not None:
        raise HistoryError(
            "--coarse works with the diff test only: a contract's rules (row counts, uniqueness) "
            "are about one version, not about several merged"
        )
    if coarse is not None and verify_all:
        raise HistoryError(
            "--coarse and --verify-all cannot be combined: --verify-all tests every version"
        )
    if contract is not None and kind is not None:
        raise HistoryError("--kind restricts the diff test and cannot be combined with --contract")
    options, source_name = drift_options(
        project,
        source,
        name,
        {
            "thresholds": thresholds,
            "ignore_columns": ignore_columns,
            "column_thresholds": column_thresholds,
            "only_columns": only_columns,
            "policy": policy,
        },
    )
    history = Versions(registry, name)
    gi, bi = history.resolve(good), history.resolve(bad)
    if gi == bi:
        raise HistoryError(
            f"--good and --bad are the same version ({_short(good)} and {_short(bad)})"
        )
    if gi > bi:
        raise HistoryError(
            f"--good ({_short(good)}, {history.versions[gi].date}) is not older than --bad "
            f"({_short(bad)}, {history.versions[bi].date}): versions are ordered by business_date, "
            "else by commit time"
        )
    versions = history.versions
    good_v, bad_v = versions[gi], versions[bi]
    candidates = versions[gi + 1 : bi + 1]  # the last one is --bad
    n = len(candidates)
    tester = _Tester(
        history,
        good_v,
        column=column,
        kind=kind,
        contract=as_path(contract),
        options=options,
    )
    history.profile(good_v)  # a good version that cannot be read fails before anything else

    def is_bad(i: int) -> bool:
        return tester.test(candidates[i])

    if not is_bad(n - 1):
        raise HistoryError(
            f"--bad ({_short(bad)}, {bad_v.date}) tests good: there is no change to find"
            + _what_tested(tester)
        )
    if contract is not None and tester.test(good_v):
        raise HistoryError(
            f"--good ({_short(good)}, {good_v.date}) itself tests bad: the contract already fails "
            "there. Choose an older good version"
        )
    warnings: list[str] = []
    flips: list[dict[str, Any]] = []
    cost_window = 0
    if verify_all:
        flags = [is_bad(i) for i in range(n)]
        first = flags.index(True)
        seen_bad = False
        for i, flag in enumerate(flags):
            if flag:
                seen_bad = True
            elif seen_bad:
                flips.append(history.describe(candidates[i]))
        if flips:
            warnings.append(
                f"{len(flips)} version(s) test good after the first bad one: {PERSISTS}, so a "
                "plain bisect can land on either side of such a gap"
            )
        mode = "verify-all"
    elif coarse is not None:
        first, extra = _coarse(tester, candidates, coarse)
        warnings += extra
        cost_window = tester.window_tests
        mode = f"coarse-{coarse}"
    else:
        first = _first_bad(-1, n - 1, is_bad)
        mode = "bisect"
    first_v = candidates[first]
    last_v = candidates[first - 1] if first > 0 else good_v
    changes = filtered_changes(
        diff_profiles(history.profile(last_v), history.profile(first_v), options), column, kind
    )
    if not changes and contract is None:
        warnings.append(
            "the change built up gradually: no single step between the last good and the first "
            "bad version is a change at these thresholds (see changes_vs_good)"
        )
    doc: dict[str, Any] = {
        "format": FORMAT_BISECT,
        "version": VERSION,
        "name": name,
        "mode": mode,
        "test": {
            "kind": "contract" if contract is not None else "diff",
            "column": column,
            "change_kind": kind,
            "contract": as_path(contract),
            "source": source_name,
        },
        "found": True,
        "good": history.describe(good_v, good),
        "bad": history.describe(bad_v, bad),
        "first_bad": history.describe(first_v),
        "last_good": history.describe(last_v),
        "changes": changes,
        "candidates": n,
        "evaluated": len(tester.version_tests),
        "max_evaluations": max_evaluations(n),
        "evaluations": tester.order,
        "cost": {
            "versions_read": history.reads,
            "full_profile_tests": len(tester.version_tests),
            "window_tests": cost_window,
        },
        "flips": flips,
        "warnings": warnings,
    }
    if contract is not None:
        doc["violations"] = tester.evidence(first_v)
    else:
        doc["changes_vs_good"] = tester.evidence(first_v)
    return BisectResult(json_safe(doc))


def _what_tested(tester: _Tester) -> str:
    where = f" on column {tester.column!r}" if tester.column else ""
    what = "the contract passes" if tester.contract else f"shape diff finds no change{where}"
    return f" ({what})"


def _coarse(tester: _Tester, candidates: list[Version], window: str) -> tuple[int, list[str]]:
    """Bisect over windows of merged versions, then inside the first bad window (and the one
    before it, where a change near the end could have been diluted below the thresholds)."""
    n = len(candidates)
    spans: list[tuple[str, int, int]] = []  # label, first index, last index
    for i, v in enumerate(candidates):
        label = window_label(v.date, window)
        if spans and spans[-1][0] == label:
            spans[-1] = (label, spans[-1][1], i)
        else:
            spans.append((label, i, i))
    m = len(spans)
    warnings: list[str] = []

    def window_bad(j: int) -> bool:
        label, a, b = spans[j]
        return tester.test_window(label, candidates[a : b + 1])

    # the last window holds --bad, which is known to be bad: it is not tested
    j = _first_bad(-1, m - 1, window_bad)
    lower = spans[max(j - 1, 0)][1] - 1
    upper = spans[j][2]
    if upper != n - 1 and not tester.test(candidates[upper]):
        warnings.append(
            f"window {spans[j][0]} tests bad merged but its last version tests good: the change "
            f"does not persist, so the whole range was searched ({PERSISTS})"
        )
        lower, upper = -1, n - 1
    return _first_bad(lower, upper, lambda i: tester.test(candidates[i])), warnings
