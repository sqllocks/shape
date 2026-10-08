"""W7-03 items 3 and 7 (profile side): the share-safe profile leaves out ``mixture`` and
``seasonality``, an older profile is unchanged, and the display prints both."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa

import shape
from shape.cli.main import main
from shape.privacy.safe_profile import to_safe_profile
from shape.privacy.safe_validator import SafeProfileValidator

OLD = Path(__file__).parents[1] / "fixtures" / "profiles" / "pre_w3_07.shape"
NEW = ("mixture", "seasonality")


def _table(n: int = 210) -> pa.Table:
    rng = np.random.default_rng(9)
    days = np.datetime64("2023-01-02") + np.arange(n).astype("timedelta64[D]")
    wk = np.tile([0.0, 1.0, 2.0, 3.0, 2.0, -3.0, -5.0], n // 7)
    price = np.where(
        rng.random(n) < 0.4, rng.normal(12345.0, 10.0, n), rng.normal(98765.0, 10.0, n)
    )
    return pa.table(
        {
            "day": pa.array(days.astype("datetime64[s]")),
            "price": pa.array(price),
            "volume": pa.array(1000.0 + 20.0 * wk + rng.normal(0, 3.0, n)),
        }
    )


def test_without_univariate_the_profile_has_neither() -> None:
    """Both belong to the opt-in univariate depth (``univariate=True``, ``--univariate``), as the
    other univariate fields do: the default profile is unchanged (T-19)."""
    cols = shape.profile(_table()).to_dict()["columns"]
    for name in ("price", "volume"):
        for field in NEW:
            assert field not in cols[name], (name, field)
    deep = shape.profile(_table(), univariate=True).to_dict()["columns"]
    assert "mixture" in deep["price"] and "seasonality" in deep["volume"]


def test_the_profile_has_both_and_the_share_safe_profile_has_neither() -> None:
    p = shape.profile(_table(), univariate=True)
    cols = p.to_dict()["columns"]
    assert cols["price"]["mixture"]["k"] == 2  # a profile of this table has both statistics
    assert cols["volume"]["seasonality"]["seasonal"] is True
    safe = to_safe_profile(p).to_dict()
    text = json.dumps(safe)
    for field in NEW:
        assert f'"{field}"' not in text, field
    # none of the component means (values) is in the share-safe profile
    for c in cols["price"]["mixture"]["components"]:
        assert f"{c['mean']:.4g}"[:5] not in text.replace(" ", "")
    assert SafeProfileValidator().validate_data(safe).is_clean


def test_the_leak_scanner_passes_the_safe_profile_of_a_table_that_has_both(tmp_path: Path) -> None:
    out = tmp_path / "safe.json"
    src = tmp_path / "t.parquet"
    import pyarrow.parquet as pq

    pq.write_table(_table(), src)
    full = tmp_path / "t.shape"
    assert main(["profile", str(src), "--univariate", "-o", str(full)]) == 0
    assert main(["profile", "safe", str(full), "-o", str(out)]) == 0
    assert SafeProfileValidator().validate_file(out).exit_code == 0
    assert not any(f in out.read_text() for f in ('"mixture"', '"seasonality"'))


def test_an_older_profile_has_neither_field_and_loads_displays_and_diffs() -> None:
    old = shape.load(OLD)
    cols = old.to_dict()["columns"]
    assert not any(set(NEW) & set(c) for c in cols.values())
    assert "visits" in old.to_html()
    assert not any(
        d["kind"] in ("mixture_change", "seasonality_change") for d in shape.diff(old, old).changes
    )


def test_the_html_and_show_print_the_new_statistics(tmp_path: Path, capsys) -> None:
    p = shape.profile(_table(), univariate=True)
    html = p.to_html()
    assert "mixture: k=2" in html and "seasonality: period 7 days" in html
    path = tmp_path / "t.shape"
    shape.save(p, path)
    capsys.readouterr()
    assert main(["show", str(path)]) == 0
    shown = capsys.readouterr().out
    assert '"mixture"' in shown and '"seasonality"' in shown
