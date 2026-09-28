"""Built-in implementation conformance smoke suite."""

from __future__ import annotations

import os
import tempfile

from shape.artifact.io import read_artifact, write_artifact
from shape.capture import capture_rows
from shape.generation.strategies import GenerationPlan, SequenceStrategy
from shape.validation.conformance import run


def conformance():
    def artifact():
        fd, p = tempfile.mkstemp(suffix=".shape")
        os.close(fd)
        try:
            write_artifact(p, {"format": "shape", "version": 1}, {"evidence/a": b"x"})
            _, c = read_artifact(p)
            assert c["evidence/a"] == b"x"
        finally:
            os.unlink(p)

    def capture():
        s = capture_rows([{"x": 1}, {"x": 2}])
        assert s.rows == 2 and s.columns["x"]["kind"] == "numeric"

    def generation():
        assert [r["id"] for r in GenerationPlan((("id", SequenceStrategy()),), 7).rows(3)] == [
            1,
            2,
            3,
        ]

    return run({"artifact": artifact, "capture": capture, "generation": generation})
