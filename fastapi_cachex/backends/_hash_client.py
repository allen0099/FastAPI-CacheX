"""pymemcache's ``HashClient`` with thread-safe server failure bookkeeping (#504).

Imported only once pymemcache is known to be installed.
"""

import threading
import time
from typing import Any

from pymemcache import HashClient


class LockedHashClient(HashClient):  # type: ignore[misc]  # pymemcache is untyped
    """A ``HashClient`` that updates its failed and dead server tables under a lock.

    ``MemcachedBackend`` calls the client from several threads at once
    (``asyncio.to_thread``), and pymemcache 4.0 updates these tables without a
    lock. When a server failed for several calls at once, the calls raised
    ``KeyError``, ``ValueError`` or ``RuntimeError`` instead of the socket
    error, and a server could be dropped from the hash ring without being
    recorded as dead, so it was never tried again.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Reentrant: _mark_failed_server() calls remove_server().
        self._tables_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def _retry_dead(self) -> None:
        with self._tables_lock:
            super()._retry_dead()

    def _mark_failed_server(self, server: Any) -> None:
        with self._tables_lock:
            super()._mark_failed_server(server)

    def remove_server(self, server: Any, port: int | None = None) -> None:
        """Take ``server`` out of rotation until the dead timeout passes.

        Unlike pymemcache's, a no-op for a server already out of rotation:
        another thread whose call failed on it got there first.
        """
        if port is not None:
            server = (server, port)
        with self._tables_lock:
            self._failed_clients.pop(server, None)
            key = self._make_client_key(server)
            if key in self.hasher.nodes:
                self._dead_clients[server] = time.time()
                self.hasher.remove_node(key)
