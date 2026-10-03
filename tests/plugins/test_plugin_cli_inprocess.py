"""``shape.plugins.cli`` and the kit's ``main`` in process: error paths of plugin commands and
of ``shape plugins info``."""

from __future__ import annotations

import argparse
import sys
from importlib import metadata

import pytest

from shape.plugins import cli, kit
from shape.plugins.host import PluginHost


class _ExitCommand:
    name = "quit"
    help = "exits with a message"

    def configure(self, parser):
        pass

    def run(self, args):
        sys.exit("fatal: config file missing")


def test_a_command_exiting_with_a_message_prints_it(capsys):
    """#376: `sys.exit("message")` exits 1 and the message reaches stderr."""
    host = PluginHost(entry_points=lambda: [])
    host.register("shape.commands", "quit", _ExitCommand)
    assert cli.run_command(host, "quit", []) == 1
    assert "fatal: config file missing" in capsys.readouterr().err


def test_info_on_the_discovery_error_record_reports_it(capsys):
    """#375: the `<discovery>` record has no group; `info` must not raise KeyError."""

    def broken():
        raise RuntimeError("bad metadata")

    host = PluginHost(entry_points=broken)
    ns = argparse.Namespace(plugin="<discovery>", json=False)
    assert cli.cmd_info(host, ns) == 1
    assert "bad metadata" in capsys.readouterr().out


def test_a_duplicate_is_not_listed_twice_as_ambiguous(capsys):
    """#375: two distributions registering one name: `info NAME` resolves to the one loaded."""
    eps = [
        metadata.EntryPoint("csv", "aud_missing_a:C", "shape.sources"),
        metadata.EntryPoint("csv", "aud_missing_b:D", "shape.sources"),
    ]
    host = PluginHost(entry_points=lambda: eps)
    rc = cli.cmd_info(host, argparse.Namespace(plugin="csv", json=False))
    out = capsys.readouterr()
    assert "ambiguous" not in out.err
    assert rc == 1 and "shape.sources:csv" in out.out and "aud_missing_a" in out.out


@pytest.mark.parametrize("samples", ["aud_no_such_module:X", "shape.plugins.kit:NO_SUCH_ATTR"])
def test_kit_main_bad_samples_is_a_usage_error(samples, capsys):
    """#378: `--samples` that cannot be resolved exits 2 with one line, no traceback."""
    with pytest.raises(SystemExit) as info:
        kit.main(["sqllocks-shape", "--samples", samples])
    assert info.value.code == 2
    assert "--samples" in capsys.readouterr().err
