"""The `vary=` request headers that `@cache` adds to a key."""

import hashlib
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence

from fastapi import Request

from .exceptions import CacheXError

# RFC 9110 §5.1: a field name is a token.
_FIELD_NAME_CHARS = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


# What ``vary={name: normaliser}`` maps a header to: the header's value in,
# the key component's value out.
VaryNormaliser = Callable[[str], str | int]
Vary = Sequence[str] | Mapping[str, VaryNormaliser | None]


def _validate_vary(vary: Vary | None) -> dict[str, VaryNormaliser | None]:
    """Check ``@cache(vary=...)``; return each name, first spelling, with its normaliser.

    A list gives every name ``None``, as a mapping does for a name mapped to
    ``None``: the header's own value goes into the key.

    Raises:
        CacheXError: If ``vary`` is a single string instead of a sequence or
            mapping of names, a name is not a non-empty header field name or
            is ``*``, or a mapping's value is neither callable nor ``None``.
    """
    if vary is None:
        return {}
    if isinstance(vary, Mapping):
        items = list(vary.items())
    elif isinstance(vary, (str, bytes)) or not isinstance(vary, Sequence):
        msg = (
            "vary must be a list of header names, e.g. vary=['Accept-Language'], "
            "or a mapping of header names to normalisers, "
            f"got {type(vary).__name__}"
        )
        raise CacheXError(msg)
    else:
        items = [(name, None) for name in vary]
    names: dict[str, tuple[str, VaryNormaliser | None]] = {}
    for name, normaliser in items:
        if not isinstance(name, str) or not name or not set(name) <= _FIELD_NAME_CHARS:
            msg = f"vary entries must be header field names, got {name!r}"
            raise CacheXError(msg)
        if name == "*":
            msg = "vary cannot contain '*': the key can only vary on named headers"
            raise CacheXError(msg)
        if normaliser is not None and not callable(normaliser):
            # Unreachable for type checkers; guards untyped callers.
            msg = (  # type: ignore[unreachable]
                f"vary[{name!r}] must be a callable taking the header value, "
                f"or None, got {type(normaliser).__name__}"
            )
            raise CacheXError(msg)
        names.setdefault(name.lower(), (name, normaliser))
    return dict(names.values())


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


def _vary_components(
    request: Request, vary: Mapping[str, VaryNormaliser | None]
) -> list[str]:
    """The key components for ``@cache(vary=...)``: ``name=value`` each.

    The name is lower-cased and the value trimmed; repeated header lines are
    joined with ``,`` as RFC 9110 §5.3 allows, and a missing header gives an
    empty value, the same as an empty one. A normaliser then gets that value
    and returns what goes into the key. For a credential header
    (``Authorization``, ``Proxy-Authorization``, ``Cookie`` and
    ``X-Session-Token``) a non-empty value, normalised or not, is replaced by
    ``sha256:`` and its full hex SHA-256; an empty or missing one stays
    ``name=``, so anonymous requests share one entry.

    Raises:
        CacheXError: If a normaliser returns something other than a ``str``
            or an ``int``.
    """
    components = []
    for name, normaliser in vary.items():
        lowered = name.lower()
        value = ",".join(line.strip() for line in request.headers.getlist(name))
        if normaliser is not None:
            normalised = normaliser(value)
            if isinstance(normalised, bool) or not isinstance(normalised, (str, int)):
                msg = (
                    f"the vary normaliser for {name!r} must return a str or an "
                    f"int, got {type(normalised).__name__}"
                )
                raise CacheXError(msg)
            value = str(normalised)
        if value and lowered in _HASHED_VARY_HEADERS:
            digest = hashlib.sha256(value.encode("utf-8", "surrogatepass"))
            value = _HASHED_VARY_MARKER + digest.hexdigest()
        components.append(f"{lowered}={value}")
    return components
