import pytest

from shape.streaming import FileCheckpointStore, replay_with_failures


def test_recovery_no_duplicate_after_checkpoint(tmp_path):
    store = FileCheckpointStore(tmp_path / "cp")
    seen = []
    with pytest.raises(RuntimeError):
        replay_with_failures(list(range(10)), seen.append, store, fail_after=4)
    assert seen == [0, 1, 2, 3]
    replay_with_failures(list(range(10)), seen.append, store)
    assert seen == list(range(10))
