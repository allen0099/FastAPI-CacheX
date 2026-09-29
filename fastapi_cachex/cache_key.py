"""The HTTP cache key: one type that builds, encodes and parses it.

``@cache``, ``build_cache_key()``, ``invalidate()``, ``clear_path()`` and the
monitoring routes all go through ``CacheKey``, so the format is defined here
and nowhere else.
"""

from dataclasses import dataclass
from operator import itemgetter
from urllib.parse import urlencode

from fastapi import Request

from .types import CACHE_KEY_SEPARATOR
from .types import escape_key_component
from .types import unescape_key_component

# A key has at least method, host and path; the query string is optional only
# when parsing keys that were written without it.
_MIN_PARTS = 3

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
    """
    if not sort_query:
        return str(request.query_params)
    return urlencode(sorted(request.query_params.multi_items(), key=itemgetter(0)))


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

    The string form is ``method|||host|||path|||query``, followed by one
    component per ``extra`` item. ``method``, ``host``, ``path`` and every
    ``extra`` item hold the plain text and are percent-encoded by ``to_str()``
    (see ``escape_key_component``), so a client-controlled value cannot
    contain the separator; a method token may contain ``|``. ``query`` is
    kept URL-encoded, as it appears in the key.

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
            host=request.headers.get("host", "unknown"),
            path=request.url.path,
            query=_query_component(request, sort_query),
            extra=tuple(_component_text(component) for component in components),
        )

    def to_str(self) -> str:
        """The key as stored in the backend."""
        return CACHE_KEY_SEPARATOR.join(
            [
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

        ``key`` is the logical key, without a backend's ``key_prefix``. A key
        with fewer than three components or an empty method (a ``CacheManager``
        or ``StateManager`` key, or one from a key builder that does not use
        ``build_cache_key``) is not an HTTP key. A key that ends at the path
        parses with an empty query.
        """
        parts = key.split(CACHE_KEY_SEPARATOR)
        if len(parts) < _MIN_PARTS or not parts[0]:
            return None
        return cls(
            method=unescape_key_component(parts[0]),
            host=unescape_key_component(parts[1]),
            path=unescape_key_component(parts[2]),
            query=parts[3] if len(parts) > _MIN_PARTS else "",
            extra=tuple(unescape_key_component(part) for part in parts[4:]),
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
        return f"*{CACHE_KEY_SEPARATOR}{escaped}{CACHE_KEY_SEPARATOR}*"
