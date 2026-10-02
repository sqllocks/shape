from shape.plugins import kit

from shape_example_behavior import LibraryLoans


def test_conforms_to_the_behavior_protocol():
    kit.check_behavior(LibraryLoans())


def test_emits_its_declared_events():
    events = LibraryLoans().simulate(population=300, seed=3, years=2)
    kinds = set(events.column("kind").to_pylist())
    assert {"member_joined", "book_borrowed"} <= kinds
