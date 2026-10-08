from shape.streaming import FileCheckpointStore, StreamCheckpoint


def test_store(tmp_path):
    s = FileCheckpointStore(tmp_path / "cp.json")
    assert s.load() is None
    s.save(StreamCheckpoint(12, "abc"))
    assert s.load() == StreamCheckpoint(12, "abc")
    s.save(StreamCheckpoint(13, None))
    assert s.load() == StreamCheckpoint(13, None)
