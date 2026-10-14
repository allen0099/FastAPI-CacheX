"""Custom exception classes for FastAPI-CacheX."""


class CacheXError(Exception):
    """Base class for all exceptions in FastAPI-CacheX."""


class BackendNotFoundError(CacheXError):
    """Exception raised when a cache backend is not found."""


class ProxyNotSetError(BackendNotFoundError):
    """Exception raised when a manager proxy has no instance set.

    Raised by ``CacheManagerProxy``. It subclasses ``BackendNotFoundError``,
    which the manager proxies raised before 0.3.8, so existing handlers keep
    catching it.
    """


class RequestNotFoundError(CacheXError):
    """Exception raised when a request is not found."""


class BackendUnavailableError(CacheXError):
    """The cache backend's server could not be reached.

    Raised by the Redis and Memcached backends for a transport failure: a
    refused or reset connection, a timeout, or a server out of rotation. It is
    chained to the client's own exception (``raise ... from``). The instance
    raised is also of that exception's type, so an ``except`` clause naming
    the client's exception (``redis.exceptions.ConnectionError``,
    ``OSError``) keeps catching it (#260). The memory backend never raises it.
    """


class InvalidTTLError(CacheXError):
    """An invalid ``ttl``, wherever it is passed.

    Raised for a ``ttl`` (or ``stale_ttl``, ``default_ttl``, ``lock_ttl``)
    that ``@cache``, ``@cached``, a backend, ``CacheManager`` or ``CacheLock``
    refuses. Catch this class to handle all of them; the subclasses keep the
    built-in exception each place raised before (#337).
    """


class TTLTypeError(InvalidTTLError, TypeError):
    """A ``ttl`` that is not an ``int`` or a ``timedelta`` (``bool`` included).

    Also a ``TypeError``, which the backends, ``CacheManager`` and
    ``CacheLock`` raised before 0.5.1.
    """


class TTLValueError(InvalidTTLError, ValueError):
    """A ``ttl`` out of range, or a ``timedelta`` with a fraction of a second.

    Also a ``ValueError``, which the backends, ``CacheManager`` and
    ``CacheLock`` raised before 0.5.1.
    """


class LockTimeoutError(CacheXError, TimeoutError):
    """Exception raised when acquiring a lock or waiting for stampede protection times out."""
