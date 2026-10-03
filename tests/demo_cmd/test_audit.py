"""AUD-scenario: regression tests for the ``shape demo`` defects the audit lane found."""

from __future__ import annotations

import json

import pytest
from demo_helpers import write_schema

from shape.demo.api import demo_cleanup, demo_init, demo_run


def sessions(home) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((home / "sessions").glob("demo-*.json"))]


# ---- #511: an interrupted run saves its session ------------------------------------------------


def test_511_an_interrupted_seeding_run_is_recorded_and_can_be_cleaned_up(
    home, tmp_path, monkeypatch
):
    from shape.scale import router

    def interrupted(self):  # the first table is written, then Ctrl+C
        sink = self.sinks[0]
        sink.open(self.engine.schema)
        table = self.engine.order[0]
        for batch in self.engine.iter_chunks(table):
            sink.write_batch(table, batch)
        sink.finish_table(table)
        raise KeyboardInterrupt

    landing = tmp_path / "landing"
    demo_init("local", local_path=str(landing))
    real = router.ScaleRouter.run
    monkeypatch.setattr(router.ScaleRouter, "run", interrupted)
    params = {
        "scenario": "retail",
        "mode": "seeding",
        "connection": "local",
        "domain": str(write_schema(tmp_path / "shop.json")),
        "rows": 1000,
    }
    with pytest.raises(KeyboardInterrupt):
        demo_run(params)
    monkeypatch.setattr(router.ScaleRouter, "run", real)
    (record,) = sessions(home)
    assert record["success"] is False and "interrupted" in record["error"]
    assert record["artifacts"], "what the run wrote is in the record"
    assert any(landing.iterdir())
    outcome = demo_cleanup(record["session_id"])
    assert outcome["ok"] and not any(landing.iterdir())


# ---- #517: a name with a final line break is not a plain name ----------------------------------


@pytest.mark.parametrize("name", ["abc\n", "abc\r\n", "a\nb"])
def test_517_check_name_refuses_a_line_break(name):
    from shape.demo.home import check_name

    with pytest.raises(ValueError, match="not a plain name"):
        check_name(name, "session id")


def test_517_a_profile_name_with_a_line_break_is_refused(home):
    from shape.demo.errors import DemoError

    with pytest.raises(DemoError):
        demo_init("here\n", local_path="x")


# ---- #518: a session record that is not a JSON object is a DemoError ---------------------------


@pytest.mark.parametrize("content", ["null", "5", '"text"', "[1]", "{bad"])
def test_518_a_record_that_is_not_an_object_is_a_demo_error(home, content):
    from shape.demo.errors import DemoError
    from shape.demo.manifest import DemoManifest

    folder = home / "sessions"
    folder.mkdir(parents=True)
    (folder / "demo-abc.json").write_text(content, encoding="utf-8")
    with pytest.raises(DemoError, match="is not a demo session record"):
        DemoManifest.load("abc")


def test_518_status_of_such_a_record_is_a_message_and_exit_two(run, home):
    folder = home / "sessions"
    folder.mkdir(parents=True)
    (folder / "demo-abc.json").write_text("null", encoding="utf-8")
    code, _, err = run("demo", "status", "abc")
    assert code == 2 and "is not a demo session record" in err


# ---- #520: a flag must be true or false, a list setting text or a list -------------------------


@pytest.mark.parametrize("key", ["dry_run", "estimate_only", "auto_cleanup"])
@pytest.mark.parametrize("value", ["false", "no", 0, 1])
def test_520_a_flag_that_is_not_true_or_false_is_a_demo_error(key, value):
    from shape.demo.api import params_from
    from shape.demo.errors import DemoError

    with pytest.raises(DemoError, match=key):
        params_from({"scenario": "retail", key: value})


@pytest.mark.parametrize("key", ["domains", "output_formats", "db_tables"])
@pytest.mark.parametrize("value", [5, {"a": 1}, [1, 2]])
def test_520_a_list_setting_that_is_not_names_is_a_demo_error(key, value):
    from shape.demo.api import params_from
    from shape.demo.errors import DemoError

    with pytest.raises(DemoError, match=key):
        params_from({"scenario": "retail", key: value})


def test_520_flags_and_lists_that_are_right_still_work():
    from shape.demo.api import params_from

    p = params_from({"scenario": "retail", "dry_run": False, "domains": ("a", "b")})
    assert p.dry_run is False and p.domains == ["a", "b"]
    assert params_from({"scenario": "retail", "output_formats": "terminal, charts"}).output_formats


# ---- #521: a run does not overwrite a file in the output folder --------------------------------


def inference(schema_file, out_dir, formats):
    return demo_run(
        {
            "scenario": "retail",
            "domain": str(schema_file),
            "rows": 1000,
            "seed": 5,
            "output_formats": formats,
            "output_dir": str(out_dir),
        }
    )


@pytest.mark.parametrize(
    ("formats", "name"),
    [(["charts"], "retail_charts.html"), (["semantic_model"], "retail_model.bim")],
)
def test_521_an_existing_file_is_not_overwritten(tmp_path, schema_file, formats, name):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / name).write_text("mine", encoding="utf-8")
    result = inference(schema_file, out_dir, formats)
    assert result["success"] is False and "already exists" in result["error"]
    assert (out_dir / name).read_text(encoding="utf-8") == "mine"


def test_521_cleanup_of_an_older_session_keeps_the_newer_page(tmp_path, schema_file):
    out_dir = tmp_path / "out"
    first = inference(schema_file, out_dir, ["charts"])
    second = inference(schema_file, out_dir, ["charts"])
    assert first["success"] and not second["success"]
    page = out_dir / "retail_charts.html"
    assert page.is_file()
    assert demo_cleanup(first["session_id"])["ok"] and not page.exists()
