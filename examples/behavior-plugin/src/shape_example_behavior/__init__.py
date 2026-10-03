"""Example behavior plugin: a library member borrows, returns (late or on time) and lapses.

One state-machine document, wrapped as a ``shape.behaviors`` plugin. After installing this
package, ``shape plugins list`` shows ``shape.behaviors:library_loans`` and
``shape behave run library_loans --population 1000 --years 2 --seed 1 -o out/`` runs it.
"""

from __future__ import annotations

from shape_behavior.behaviors import ModuleBehavior

SHAPE_API = "1.0"

DOCUMENT = {
    "format": "shape-behavior/1",
    "name": "library_loans",
    "initial": "joined",
    "attributes": {"loans": {"kind": "constant", "value": 0}},
    "states": {
        "joined": {
            "type": "initial",
            "transition": {"direct": "member"},
        },
        "member": {
            "type": "event",
            "event": "member_joined",
            "transition": {"direct": "browse"},
        },
        "browse": {
            "type": "delay",
            "delay": {"kind": "exponential", "mean": 30, "unit": "days"},
            "transition": {
                "distributed": [{"p": 0.85, "to": "borrow"}, {"p": 0.15, "to": "lapse"}]
            },
        },
        "borrow": {
            "type": "event",
            "event": "book_borrowed",
            "transition": {"direct": "reading"},
        },
        "reading": {
            "type": "delay",
            "delay": {"kind": "uniform", "low": 7, "high": 35, "unit": "days"},
            "transition": {"distributed": [{"p": 0.8, "to": "returned"}, {"p": 0.2, "to": "late"}]},
        },
        "returned": {
            "type": "event",
            "event": "book_returned",
            "transition": {"direct": "browse"},
        },
        "late": {
            "type": "event",
            "event": "book_returned_late",
            "transition": {"direct": "browse"},
        },
        "lapse": {
            "type": "event",
            "event": "membership_lapsed",
            "transition": {"direct": "end"},
        },
        "end": {"type": "terminal"},
    },
}


class LibraryLoans(ModuleBehavior):
    """Library loans: borrow, return on time or late, and eventually lapse."""

    def __init__(self) -> None:
        super().__init__(DOCUMENT, version="0.1.0")
