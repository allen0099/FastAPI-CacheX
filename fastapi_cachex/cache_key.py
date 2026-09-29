"""The HTTP cache key: one type that builds, encodes and parses it.

``@cache``, ``build_cache_key()``, ``invalidate()``, ``clear_path()`` and the
monitoring routes all go through ``CacheKey``, so the format is defined here
and nowhere else.
"""

import hashlib
from dataclasses import dataclass
from operator import itemgetter
from typing import ClassVar
from urllib.parse import urlencode

from fastapi import Request

from .types import CACHE_KEY_SEPARATOR
from .types import HTTP_KEY_FORMAT_TAG
from .types import escape_key_component
from .types import unescape_key_component

# Format tag, method, host, path and query: a key never has fewer components.
_MIN_PARTS = 5

# The port a scheme implies when the Host header names none.
_DEFAULT_PORTS = {"http": "80", "https": "443", "ws": "80", "wss": "443"}

# A query component longer than this many bytes is stored as its digest.
_QUERY_HASH_THRESHOLD = 200

# Marks a hashed query. A query as sent never starts with it: the encoding
# writes ``:`` as ``%3A``.
_QUERY_HASH_PREFIX = "sha256:"

# Characters that are live in a Redis glob pattern.
_GLOB_SPECIAL = frozenset("*?[]\\")


def escape_glob(text: str) -> str:
    """Backslash-escape ``text`` so a Redis glob pattern matches it literally."""
    return "".join(f"\\{ch}" if ch in _GLOB_SPECIAL else ch for ch in text)


def _query_component(request: Request, sort_query: bool) -> str:
    """The query string as it appears in the key.

    Starlette parses the query (blank values kept, empty ``&&`` segments
    dropped, names and values percent-decoded) and ``str()`` re-encodes the
    pairs in the order sent. ``sort_query`` stable-sorts the same decoded
    pairs by name before encoding them the same way, so only the order of
    differently named parameters changes: repeated values of one name keep
    their relative order, and an already sorted query gives the unsorted key.

    A query longer than 200 bytes (``_QUERY_HASH_THRESHOLD``) is replaced by
    ``sha256:`` and its full hex digest, so a client cannot make the query
    part of the key arbitrarily long; the digest is taken after sorting, so ``sort_query``
    still merges reordered long queries.
    """
    if sort_query:
        query = urlencode(sorted(request.query_params.multi_items(), key=itemgetter(0)))
    else:
        query = str(request.query_params)
    encoded = query.encode()
    if len(encoded) > _QUERY_HASH_THRESHOLD:
        return _QUERY_HASH_PREFIX + hashlib.sha256(encoded).hexdigest()
    return query


def _host_component(request: Request) -> str:
    """The ``Host`` header as it appears in the key.

    Hostnames are case-insensitive (RFC 9110 section 4.2.3), and an empty port
    or the scheme's default one (``:80`` for http, ``:443`` for https) names
    the same origin as no port (RFC 3986 section 6.2.3). So the host is
    lower-cased and such a port dropped, and every spelling of one origin
    shares an entry. An IPv6 literal keeps its brackets. The scheme is the
    one the app sees (the ASGI scope's ``scheme``, which ``request.url``
    also uses); behind a TLS-terminating proxy that is ``http`` unless the
    proxy's headers are applied. A missing ``Host`` header gives
    ``unknown``.
    """
    host = request.headers.get("host")
    if host is None:
        return "unknown"
    host = host.lower()
    name, colon, port = host.rpartition(":")
    # No colon, no name before it, or the last colon is inside an IPv6
    # literal (``[::1]``) or an unbracketed one (not a valid Host, kept as
    # sent): no port to drop.
    bracketed = name.startswith("[") and name.endswith("]")
    if not colon or not name or (":" in name and not bracketed):
        return host
    if port in ("", _DEFAULT_PORTS.get(request.scope.get("scheme", "http"))):
        return name
    return host


def _component_text(component: str | int) -> str:
    """``component`` as key text: a ``str`` as is, an ``int`` in decimal.

    Raises:
        TypeError: For anything else, ``bool`` included.
    """
    if isinstance(component, bool) or not isinstance(component, (str, int)):
        msg = (
            "build_cache_key components must be str or int, "
            f"got {type(component).__name__}"
        )
        raise TypeError(msg)
    return str(component)


@dataclass(frozen=True)
class CacheKey:
    """An HTTP cache key, decoded into its components.

    The string form is ``http:v2|method|host|path|query``, followed by one
    component per ``extra`` item. The leading ``FORMAT_TAG`` names the key
    format; a later format gets another tag, so its keys never collide with
    these and ``clear_pattern(f"{CacheKey.FORMAT_TAG}|*")`` removes every key
    of this one. ``method``, ``host``, ``path`` and every
    ``extra`` item hold the plain text and are percent-encoded by ``to_str()``
    (see ``escape_key_component``), so a client-controlled value cannot
    contain the separator; a method token may contain ``|``. ``query`` is
    kept URL-encoded, as it appears in the key; ``from_request()`` stores a
    query longer than 200 bytes as ``sha256:`` and its hex digest instead,
    which no query as sent can look like.

    Build one from a request with ``from_request()`` and turn a stored key
    back into one with ``parse()``::

        key = CacheKey.from_request(request, request.state.tenant_id)
        await backend.delete(key.to_str())

        parsed = CacheKey.parse(stored_key)
        if parsed is not None:
            print(parsed.path, parsed.extra)

    Raises:
        ValueError: If ``query`` contains the separator. It is not
            percent-encoded, so the key could not be parsed back. A query
            taken from a request never does: Starlette encodes ``|``.
    """

    method: str
    host: str
    path: str
    query: str = ""
    extra: tuple[str, ...] = ()

    FORMAT_TAG: ClassVar[str] = HTTP_KEY_FORMAT_TAG

    def __post_init__(self) -> None:
        """Reject a ``query`` the string form could not keep apart."""
        if CACHE_KEY_SEPARATOR in self.query:
            msg = f"CacheKey.query cannot contain {CACHE_KEY_SEPARATOR!r}"
            raise ValueError(msg)

    @classmethod
    def from_request(
        cls, request: Request, *components: str | int, sort_query: bool = False
    ) -> "CacheKey":
        """The key ``@cache`` stores ``request`` under, plus extra components.

        ``build_cache_key(request, *components, sort_query=...)`` is
        ``CacheKey.from_request(...).to_str()``; see it for the arguments.

        Raises:
            TypeError: If a component is not a ``str`` or ``int``.
        """
        return cls(
            method=request.method,
            host=_host_component(request),
            path=request.url.path,
            query=_query_component(request, sort_query),
            extra=tuple(_component_text(component) for component in components),
        )

    def to_str(self) -> str:
        """The key as stored in the backend."""
        return CACHE_KEY_SEPARATOR.join(
            [
                self.FORMAT_TAG,
                escape_key_component(self.method),
                escape_key_component(self.host),
                escape_key_component(self.path),
                self.query,
                *(escape_key_component(part) for part in self.extra),
            ]
        )

    @classmethod
    def parse(cls, key: str) -> "CacheKey | None":
        """Decode a stored HTTP key, or return ``None`` for any other key.

        ``key`` is the logical key, without a backend's ``key_prefix``. Only
        a key that starts with ``FORMAT_TAG`` and has at least a method, host,
        path and query is an HTTP key. ``CacheManager`` and ``StateManager``
        keys, keys written by 0.3.x (``method|||host|||path|||query``) and keys
        from a key builder that does not use ``build_cache_key`` are not.
        """
        parts = key.split(CACHE_KEY_SEPARATOR)
        if len(parts) < _MIN_PARTS or parts[0] != cls.FORMAT_TAG or not parts[1]:
            return None
        return cls(
            method=unescape_key_component(parts[1]),
            host=unescape_key_component(parts[2]),
            path=unescape_key_component(parts[3]),
            query=parts[4],
            extra=tuple(unescape_key_component(part) for part in parts[5:]),
        )

    @staticmethod
    def path_glob(path: str) -> str:
        """A Redis glob that matches every HTTP key for ``path``.

        ``path`` is matched literally: glob characters in it are escaped. The
        glob cannot pin the path to its component, so it also matches keys
        that have the same text in another one; ``parse()`` each match and
        compare ``path`` to keep only the right ones. Backends put their own
        ``key_prefix`` in front.
        """
        escaped = escape_glob(escape_key_component(path))
        sep = CACHE_KEY_SEPARATOR
        return f"{escape_glob(CacheKey.FORMAT_TAG)}{sep}*{sep}{escaped}{sep}*"
