"""A sink that fails to open does not leave the others open (HUNT2-fabric #713)."""

from __future__ import annotations

import pytest

from shape.cli.generation import load_target
from shape.generation.engine import Engine
from shape.scale.router import ScaleRouter
from shape.scale.sinks.base import BaseSink, SinkError


class Recorder(BaseSink):
    def __init__(self, name: str, fail_open: bool = False) -> None:
        self.name = name
        self.fail_open = fail_open
        self.opened = False
        self.closed = 0

    def open(self, schema) -> None:
        if self.fail_open:
            raise RuntimeError(f"cannot open {self.name}")
        self.opened = True

    def close(self) -> None:
        self.closed += 1


def _router(sinks):
    engine = Engine(load_target("retail", None), scale="small")
    return ScaleRouter(engine, sinks, mode="local_single")


def test_the_sinks_that_opened_are_closed_when_another_fails_to_open():
    first, broken, third = Recorder("a"), Recorder("b", fail_open=True), Recorder("c")
    with pytest.raises(SinkError, match="cannot open b"):
        _router([first, broken, third]).run()
    assert (first.opened, third.opened) == (True, True)
    assert (first.closed, third.closed) == (1, 1)


def test_a_sink_that_never_opened_is_not_closed():
    first, broken = Recorder("a"), Recorder("b", fail_open=True)
    with pytest.raises(SinkError):
        _router([first, broken]).run()
    assert broken.closed == 0


def test_the_pool_of_the_registry_is_shut_down_after_a_failed_open():
    router = _router([Recorder("a"), Recorder("b", fail_open=True)])
    with pytest.raises(SinkError):
        router.run()
    assert router._registry._pool is None


def test_every_sink_is_closed_once_on_a_good_run():
    sinks = [Recorder("a"), Recorder("b")]
    _router(sinks).run()
    assert [s.closed for s in sinks] == [1, 1]
