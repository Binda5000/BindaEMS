import asyncio

from bindaems.core.broadcast import Broadcast


async def test_broadcast_drops_oldest_for_slow_subscriber() -> None:
    b = Broadcast(maxsize=2)
    async with b.subscribe() as it:
        for i in range(3):
            b.publish({"i": i})
        assert [(await anext(it))["i"], (await anext(it))["i"]] == [1, 2]


async def test_publish_without_subscribers_does_nothing() -> None:
    Broadcast().publish({"i": 1})


async def test_each_subscriber_gets_every_message_until_it_leaves() -> None:
    b = Broadcast()
    async with b.subscribe() as first:
        async with b.subscribe() as second:
            b.publish({"i": 1})
            assert (await anext(first))["i"] == 1 and (await anext(second))["i"] == 1
        b.publish({"i": 2})  # der zweite ist abgemeldet
        assert (await anext(first))["i"] == 2
        assert not asyncio.get_running_loop().is_closed()


async def test_leaving_subscribers_are_removed() -> None:
    b = Broadcast()
    async with b.subscribe():
        async with b.subscribe():
            assert b.subscriber_count == 2
        assert b.subscriber_count == 1
    assert b.subscriber_count == 0
