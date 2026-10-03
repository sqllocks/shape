"""``shape fabric notebook | deploy-notebook | setup`` and their aliases, end to end.

The Fabric REST API is a recorded conversation: the transport of ``commands.API_TRANSPORT`` is the
fake service behind the committed tapes (``tests/fixtures/fabric_*.json``), so a command that
asks for anything but what was recorded fails the test. Sign-in is the fake identity.
"""

from __future__ import annotations

import base64
import json

import pyarrow.parquet as pq
import pytest
from shape_fabric import commands
from shape_fabric.fabric_api import tuple_transport
from shape_fabric.recording import TapeTransport, load, replay_tape
from shape_fabric.testing import (
    FAKE_ENTRA_TOKEN,
    WORKSPACE_ID,
    FakeFabricItems,
    FakeIdentity,
)

from shape.cli.main import main

pytestmark = pytest.mark.contract

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


@pytest.fixture
def world(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    identity = FakeIdentity().install(monkeypatch)
    monkeypatch.setattr("shape_fabric.notebook._version", lambda: "0.0.0")
    monkeypatch.setattr("shape_fabric.setup_env._version", lambda: "0.0.0")

    class World:
        pass

    w = World()
    w.identity, w.tmp, w.monkeypatch = identity, tmp_path, monkeypatch

    def run(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    def serve(fake):
        monkeypatch.setattr(commands, "API_TRANSPORT", tuple_transport(fake))
        return fake

    def replay(tape_name):
        tape = replay_tape(load(FIXTURES / f"{tape_name}.json"))
        monkeypatch.setattr(commands, "API_TRANSPORT", tuple_transport(TapeTransport(tape)))
        return tape

    w.run, w.serve, w.replay = run, serve, replay
    return w


# --- notebook ------------------------------------------------------------------------------


def test_notebook_without_output_prints_the_ipynb_json(world):
    code, out, err = world.run("notebook", "retail")
    assert (code, err) == (0, "")
    nb = json.loads(out)
    assert nb["nbformat"] == 4 and nb["cells"]
    source = "".join("".join(c["source"]) for c in nb["cells"])
    assert "shape.generate('retail', scale='small', seed=42)" in source
    assert "%pip install sqllocks-shape==0.0.0 sqllocks-shape-domains==0.0.0" in source


def test_notebook_to_a_file_prints_the_summary(world):
    code, out, _ = world.run(
        "notebook", "retail", "-s", "small", "--seed", "7", "--target", "csv", "-o", "nb/r.ipynb"
    )
    assert code == 0
    nb = json.loads((world.tmp / "nb" / "r.ipynb").read_text())
    assert "seed=7" in "".join("".join(c["source"]) for c in nb["cells"])
    assert (
        "Notebook Generated" in out
        and "Target: csv" in out
        and f"Cells:  {len(nb['cells'])}" in out
    )


@pytest.mark.parametrize("target", ["lakehouse", "csv", "display"])
def test_the_notebooks_code_runs(world, target, monkeypatch):
    # the generated cells are real code: run them (a %pip line and display() are the notebook's)
    code, out, _ = world.run("notebook", "retail", "--target", target)
    assert code == 0
    cells = [c for c in json.loads(out)["cells"] if c["cell_type"] == "code"]
    monkeypatch.setenv("LAKEHOUSE_FILES_PATH", str(world.tmp / "lh"))
    shown = []
    scope: dict = {"display": shown.append}
    for cell in cells:
        text = "".join(cell["source"])
        if text.startswith("%pip"):
            continue
        exec(compile(text, f"<{target}>", "exec"), scope)  # noqa: S102
    if target == "lakehouse":
        files = sorted(p.name for p in (world.tmp / "lh" / "shape" / "retail").iterdir())
        assert "customer.parquet" in files
        assert pq.read_table(world.tmp / "lh" / "shape" / "retail" / "customer.parquet").num_rows
    elif target == "csv":
        assert (world.tmp / "shape_retail" / "customer.csv").is_file()
    else:
        assert shown and len(shown) == 5


def test_notebook_errors_are_exit_2(world):
    assert world.run("notebook", "nonesuch")[0] == 2
    code, _, err = world.run("notebook", "retail", "-s", "gigantic")
    assert code == 2 and "gigantic" in err and "small" in err
    assert world.run("notebook", "retail", "--target", "somewhere")[0] == 2


def test_the_alias_and_the_subcommand_print_the_same_notebook(world):
    a = world.run("notebook", "retail")
    b = world.run("fabric", "notebook", "retail")
    assert a == b and a[0] == 0


# --- deploy-notebook ---------------------------------------------------------------------


def test_deploy_replays_the_recorded_conversation(world):
    tape = world.replay("fabric_deploy_notebook")
    code, out, err = world.run(
        "deploy-notebook", "retail", "--workspace", "Demo", "--notebook-name", "Shape_retail_small"
    )
    assert (code, err) == (0, "")
    tape.assert_done()
    assert "Notebook created: Shape_retail_small" in out and "Item ID:" in out
    assert "Open the notebook in Fabric" in out


def test_deploy_makes_the_notebook_item_with_the_ipynb_part(world):
    fake = world.serve(FakeFabricItems())
    code, out, _ = world.run("deploy-notebook", "retail", "--workspace", "Demo")
    assert code == 0
    (body,) = fake.created
    assert body["displayName"] == "Shape_retail_small" and body["type"] == "Notebook"
    parts = {p["path"]: p for p in body["definition"]["parts"]}
    assert body["definition"]["format"] == "ipynb"
    assert set(parts) == {"notebook-content.ipynb", ".platform"}
    nb = json.loads(base64.b64decode(parts["notebook-content.ipynb"]["payload"]))
    assert nb["nbformat"] == 4
    platform = json.loads(base64.b64decode(parts[".platform"]["payload"]))
    assert platform["metadata"] == {"type": "Notebook", "displayName": "Shape_retail_small"}
    assert set(fake.auth) == {f"Bearer {FAKE_ENTRA_TOKEN}"}


def test_deploy_by_guid_does_not_list_workspaces(world):
    fake = world.serve(FakeFabricItems())
    assert world.run("deploy-notebook", "retail", "--workspace", WORKSPACE_ID)[0] == 0
    assert ("GET", "/v1/workspaces") not in fake.calls


def test_deploy_follows_an_accepted_creation(world):
    tape = world.replay("fabric_deploy_notebook_accepted")
    code, out, _ = world.run(
        "deploy-notebook", "retail", "--workspace", "Demo", "--notebook-name", "Shape_retail_small"
    )
    assert code == 0 and "Notebook created: Shape_retail_small" in out
    tape.assert_done()


def test_deploy_reports_a_failed_creation(world):
    world.replay("fabric_deploy_notebook_operation_fails")
    code, out, err = world.run(
        "deploy-notebook", "retail", "--workspace", "Demo", "--notebook-name", "Shape_retail_small"
    )
    assert code == 1 and "no capacity" in err and "Notebook created" not in out


def test_deploy_name_in_use_is_exit_1(world):
    world.replay("fabric_deploy_notebook_name_in_use")
    code, _, err = world.run(
        "deploy-notebook", "retail", "--workspace", "Demo", "--notebook-name", "Shape_retail_small"
    )
    assert code == 1 and "already exists" in err and "Shape_retail_small" in err


def test_deploy_unknown_workspace_is_exit_1(world):
    world.serve(FakeFabricItems())
    code, _, err = world.run("deploy-notebook", "retail", "--workspace", "Nowhere")
    assert code == 1 and "Nowhere" in err


def test_deploy_finds_a_workspace_on_a_later_page(world):
    world.replay("fabric_workspace_by_name_across_pages")  # the listing the tape pins
    fake = FakeFabricItems(
        workspaces=[("A", "22222222-2222-4222-8222-222222222222"), ("Demo", WORKSPACE_ID)],
        page_size=1,
    )
    world.serve(fake)
    assert world.run("deploy-notebook", "retail", "--workspace", "Demo")[0] == 0


def test_deploy_input_errors_are_exit_2_and_make_no_request(world):
    fake = world.serve(FakeFabricItems())
    assert world.run("deploy-notebook", "nonesuch", "--workspace", "Demo")[0] == 2
    assert world.run("deploy-notebook", "retail")[0] == 2  # --workspace is required
    code, _, err = world.run("deploy-notebook", "retail", "--workspace", "Demo", "--auth", "sql")
    assert code == 2 and "sql" in err
    assert fake.calls == []


@pytest.mark.parametrize("mode", ["cli", "msi", "device-code"])
def test_deploy_signs_in_with_each_entra_mode(world, mode):
    world.serve(FakeFabricItems())
    code, _, _ = world.run("deploy-notebook", "retail", "--workspace", "Demo", "--auth", mode)
    assert code == 0
    assert any(
        c.get("get_token") == ["https://api.fabric.microsoft.com/.default"]
        for c in world.identity.calls
    )


def test_deploy_with_a_service_principal_secret_from_the_environment(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_SECRET", "s3cr3t-value-for-test")
    fake = world.serve(FakeFabricItems())
    code, out, err = world.run(
        "deploy-notebook", "retail", "--workspace", "Demo", "--auth", "spn",
        "--tenant-id", "t", "--client-id", "c", "--client-secret", "env://SHAPE_TEST_SECRET",
    )  # fmt: skip
    assert code == 0 and fake.created
    assert "s3cr3t-value-for-test" not in out + err + json.dumps(world.identity.calls)


def test_a_literal_secret_is_refused_and_never_echoed(world):
    world.serve(FakeFabricItems())
    code, out, err = world.run(
        "deploy-notebook", "retail", "--workspace", "Demo", "--auth", "spn",
        "--tenant-id", "t", "--client-id", "c", "--client-secret", "hunter2hunter2",
    )  # fmt: skip
    assert code == 2 and "hunter2hunter2" not in out + err


def test_the_alias_and_the_subcommand_make_the_same_request(world):
    fake = world.serve(FakeFabricItems())
    world.run("deploy-notebook", "retail", "--workspace", "Demo")
    first = fake.created[0]
    fake2 = world.serve(FakeFabricItems())
    world.run("fabric", "deploy-notebook", "retail", "--workspace", "Demo")
    assert fake2.created[0] == first


# --- setup ---------------------------------------------------------------------------------


def test_setup_replays_the_recorded_conversation(world):
    tape = world.replay("fabric_setup_environment_and_lakehouse")
    code, out, err = world.run("setup-fabric", "--workspace", "Demo", "--create-lakehouse")
    assert (code, err) == (0, "")
    tape.assert_done()
    assert "Created Environment: shape-env" in out
    assert "Created Lakehouse:   shape-lakehouse" in out
    assert "sqllocks-shape 0.0.0" in out and "Publish the environment" in out


def test_setup_without_a_lakehouse_creates_only_the_environment(world):
    fake = world.serve(FakeFabricItems())
    code, out, _ = world.run("setup-fabric", "--workspace", "Demo", "--env-name", "my-env")
    assert code == 0 and [c["type"] for c in fake.created] == ["Environment"]
    assert fake.created[0]["displayName"] == "my-env" and "Lakehouse:   no" in out


def test_setup_names_the_lakehouse(world):
    fake = world.serve(FakeFabricItems())
    world.run(
        "setup-fabric", "--workspace", "Demo", "--create-lakehouse", "--lakehouse-name", "out"
    )
    assert [c["displayName"] for c in fake.created] == ["shape-env", "out"]


def test_setup_reuses_what_is_already_there(world):
    tape = world.replay("fabric_setup_reuses_existing")
    code, out, _ = world.run("setup-fabric", "--workspace", "Demo", "--create-lakehouse")
    assert code == 0
    tape.assert_done()
    assert "Found existing Environment: shape-env" in out and "Created Lakehouse" in out


def test_setup_snippet_prints_a_notebook_cell_and_needs_no_workspace_or_sign_in(world):
    fake = world.serve(FakeFabricItems())
    code, out, _ = world.run("setup-fabric", "--snippet")
    assert code == 0 and "%pip install sqllocks-shape==0.0.0" in out
    assert "shape.generate" in out and fake.calls == [] and world.identity.calls == []


def test_the_setup_snippet_runs(world):
    code, out, _ = world.run("setup-fabric", "--snippet")
    lines = [ln for ln in out.splitlines() if not ln.startswith("%pip")]
    scope: dict = {}
    exec(compile("\n".join(lines), "<snippet>", "exec"), scope)  # noqa: S102


def test_setup_needs_a_workspace_unless_snippet(world):
    fake = world.serve(FakeFabricItems())
    code, _, err = world.run("setup-fabric")
    assert code == 2 and "--workspace" in err and fake.calls == []


def test_setup_unknown_workspace_is_exit_1(world):
    world.replay("fabric_workspace_not_found")
    code, _, err = world.run("setup-fabric", "--workspace", "Nowhere")
    assert code == 1 and "Nowhere" in err


def test_setup_through_the_fabric_subcommand(world):
    fake = world.serve(FakeFabricItems())
    assert world.run("fabric", "setup", "--workspace", "Demo")[0] == 0
    assert [c["type"] for c in fake.created] == ["Environment"]


def test_a_changed_request_does_not_pass_a_replay(world):
    # the recording deployed seed 42; another seed is another notebook, so the request differs
    world.replay("fabric_deploy_notebook")
    code, out, err = world.run(
        "deploy-notebook", "retail", "--workspace", "Demo", "--seed", "1",
        "--notebook-name", "Shape_retail_small",
    )  # fmt: skip
    assert code == 1 and "differs from the recording" in err and "Notebook created" not in out
