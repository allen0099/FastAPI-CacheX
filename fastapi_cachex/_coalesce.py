"""In-process coalescing of concurrent misses for `@cache(coalesce=True)` (#252).

The first request to miss a key leads: it runs the handler as usual. Requests
that miss the same key while it runs follow: they wait until the leader has
answered, then read the backend again. A follower that still finds no entry
(the leader's response was not stored, or the handler failed) renders its own
response without waiting again, so followers never queue behind each other.

Only requests in this process are coalesced. The registry is keyed by event
loop and backend as well as the cache key, so two apps or loops never wait on
each other.
"""

import asyncio

# One future per key being rendered by a leader; done once it has answered.
_IN_FLIGHT: dict[tuple[int, int, str], asyncio.Future[None]] = {}


class _Flight:
    """One request's part in coalescing: leader, follower or neither.

    The wrapper creates one per request and calls ``finish()`` once the
    request has been answered, however it ended, so a leader always releases
    its followers and leaves nothing behind in the registry.
    """

    def __init__(self) -> None:
        self._key: tuple[int, int, str] | None = None
        self._future: asyncio.Future[None] | None = None

    def join(
        self, backend: object, cache_key: str, *, may_lead: bool
    ) -> asyncio.Future[None] | None:
        """Return the leader's future to wait on, or ``None`` to render.

        With ``may_lead`` and no leader for the key, this request becomes the
        leader. Without it (HEAD, whose response is never stored), it renders
        without blocking anyone.
        """
        loop = asyncio.get_running_loop()
        key = (id(loop), id(backend), cache_key)
        running = _IN_FLIGHT.get(key)
        if running is not None or not may_lead:
            return running
        self._key = key
        self._future = loop.create_future()
        _IN_FLIGHT[key] = self._future
        return None

    def finish(self) -> None:
        """Release the followers, if this request led."""
        if self._key is None or self._future is None:
            return
        # Only the leader registers or removes its key, and followers wait
        # through a shield, so the future is still pending. `pop` because a
        # test teardown may have cleared the registry already.
        _IN_FLIGHT.pop(self._key, None)
        self._future.set_result(None)
        self._key = None
        self._future = None


async def _wait_for(leader: asyncio.Future[None]) -> None:
    """Wait until the leader has answered.

    Shielded: a follower that is cancelled stops waiting without cancelling
    the leader's future for the other followers.
    """
    await asyncio.shield(leader)
