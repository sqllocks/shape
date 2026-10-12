"""The live tests' item cleanup waits for an item that is not listed yet (it left 6 notebooks)."""

from shape_fabric.testing import remove_item


class Api:
    def __init__(self, visible_after):
        self.calls, self.deleted, self.visible_after = 0, [], visible_after

    def find_item(self, workspace, kind, name):
        self.calls += 1
        return {"id": "abc"} if self.calls > self.visible_after else None

    def delete_item(self, workspace, item_id):
        self.deleted.append(item_id)


def test_an_item_listed_late_is_still_deleted():
    now = [0.0]
    api = Api(visible_after=3)
    done = remove_item(
        api,
        "ws",
        "Notebook",
        "n",
        clock=lambda: now[0],
        sleep=lambda s: now.__setitem__(0, now[0] + s),
    )
    assert done and api.deleted == ["abc"] and api.calls == 4


def test_an_item_that_never_shows_gives_up_after_the_wait():
    now = [0.0]
    api = Api(visible_after=10**6)
    done = remove_item(
        api,
        "ws",
        "Notebook",
        "n",
        wait=10,
        clock=lambda: now[0],
        sleep=lambda s: now.__setitem__(0, now[0] + s),
    )
    assert not done and api.deleted == []
