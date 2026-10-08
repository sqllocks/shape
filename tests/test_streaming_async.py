import asyncio

from shape.streaming import aconsume


def test_async_streaming_checkpoint():
    async def src():
        for i in range(5):
            yield i

    seen = []
    cps = []
    n = asyncio.run(aconsume(src(), seen.append, 2, cps.append))
    assert n == 5 and seen == list(range(5)) and cps[-1].sequence == 5
