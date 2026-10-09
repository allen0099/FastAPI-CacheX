"""The `vary=` request headers that `@cache` adds to a key."""

import hashlib
from collections.abc import Sequence

from fastapi import Request

from .exceptions import CacheXError

# RFC 9110 §5.1: a field name is a token.
_FIELD_NAME_CHARS = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


def _validate_vary(vary: Sequence[str] | None) -> list[str]:
    """Check ``@cache(vary=...)`` and return the names, first spelling of each.

    Raises:
        CacheXError: If ``vary`` is a single string instead of a sequence of
            names, or a name is not a non-empty header field name, or is ``*``.
    """
    if vary is None:
        return []
    if isinstance(vary, (str, bytes)) or not isinstance(vary, Sequence):
        msg = (
            "vary must be a list of header names, e.g. vary=['Accept-Language'], "
            f"got {type(vary).__name__}"
        )
        raise CacheXError(msg)
    names: dict[str, str] = {}
    for name in vary:
        if not isinstance(name, str) or not name or not set(name) <= _FIELD_NAME_CHARS:
            msg = f"vary entries must be header field names, got {name!r}"
            raise CacheXError(msg)
        if name == "*":
            msg = "vary cannot contain '*': the key can only vary on named headers"
            raise CacheXError(msg)
        names.setdefault(name.lower(), name)
    return list(names.values())


# Request headers whose values are credentials: ``vary`` keys on a digest of
# the value instead of the value, so the key (shown by ``get_all_keys()``, the
# monitoring routes and the Redis/Memcached keyspace) never holds a token.
_HASHED_VARY_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        # The token header of the session middleware removed in 0.5.0. Apps
        # that still send it under their own session scheme keep the digest,
        # so upgrading never puts their tokens into keys.
        "x-session-token",
    }
)
_HASHED_VARY_MARKER = "sha256:"


def _vary_components(request: Request, names: Sequence[str]) -> list[str]:
    """The key components for ``@cache(vary=names)``: ``name=value`` each.

    The name is lower-cased and the value trimmed; repeated header lines are
    joined with ``,`` as RFC 9110 §5.3 allows, and a missing header gives an
    empty value, the same as an empty one. For a credential header
    (``Authorization``, ``Proxy-Authorization``, ``Cookie`` and
    ``X-Session-Token``) a non-empty value is replaced by ``sha256:`` and the
    full hex SHA-256 of the joined value; an empty or missing one stays
    ``name=``, so anonymous requests share one entry.
    """
    components = []
    for name in names:
        lowered = name.lower()
        value = ",".join(line.strip() for line in request.headers.getlist(name))
        if value and lowered in _HASHED_VARY_HEADERS:
            digest = hashlib.sha256(value.encode("utf-8", "surrogatepass"))
            value = _HASHED_VARY_MARKER + digest.hexdigest()
        components.append(f"{lowered}={value}")
    return components
