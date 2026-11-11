"""The `Cache-Control` header `@cache` sends, and which responses stay private."""

from collections.abc import Iterable
from typing import Literal

from fastapi import Response

from .directives import DirectiveType
from .events import CacheReason

_NO_STORE = DirectiveType.NO_STORE.value


class CacheControl:
    """Manages Cache-Control header directives."""

    def __init__(self) -> None:
        """Initialize an empty CacheControl instance."""
        self.directives: list[str] = []

    def add(self, directive: DirectiveType, value: int | None = None) -> None:
        """Add a Cache-Control directive.

        Args:
            directive: The directive type to add
            value: Optional value for the directive
        """
        if value is not None:
            self.directives.append(f"{directive.value}={value}")
        else:
            self.directives.append(directive.value)

    def __str__(self) -> str:
        """Return the Cache-Control header value as a string."""
        return ", ".join(self.directives)


def _build_cache_control(
    *,
    ttl: int | None,
    stale: Literal["error", "revalidate"] | None,
    stale_ttl: int | None,
    no_cache: bool,
    public: bool,
    private: bool,
    immutable: bool,
    must_revalidate: bool,
    s_maxage: int | None = None,
    proxy_revalidate: bool = False,
    no_transform: bool = False,
) -> str:
    """The ``Cache-Control`` value for a ``@cache`` route's arguments.

    ``no_cache`` sends only ``no-cache`` (plus ``must-revalidate``,
    ``proxy-revalidate`` and ``no-transform``); otherwise the directives
    follow in a fixed order: scope, ``max-age``, ``s-maxage``,
    ``must-revalidate``, ``proxy-revalidate``, the stale directive,
    ``immutable``, ``no-transform``.
    """
    directive = DirectiveType
    parts: list[tuple[object, DirectiveType | None, int | None]]
    if no_cache:
        parts = [
            (True, directive.NO_CACHE, None),
            (must_revalidate, directive.MUST_REVALIDATE, None),
            (proxy_revalidate, directive.PROXY_REVALIDATE, None),
            (no_transform, directive.NO_TRANSFORM, None),
        ]
    else:
        # stale_ttl is validated at decoration time.
        stale_directive = {
            "revalidate": directive.STALE_WHILE_REVALIDATE,
            "error": directive.STALE_IF_ERROR,
        }.get(stale or "")
        parts = [
            (public, directive.PUBLIC, None),
            (private and not public, directive.PRIVATE, None),
            (ttl is not None, directive.MAX_AGE, ttl),
            (s_maxage is not None, directive.S_MAXAGE, s_maxage),
            (must_revalidate, directive.MUST_REVALIDATE, None),
            (proxy_revalidate, directive.PROXY_REVALIDATE, None),
            (stale_directive is not None, stale_directive, stale_ttl),
            (immutable, directive.IMMUTABLE, None),
            (no_transform, directive.NO_TRANSFORM, None),
        ]

    cache_control = CacheControl()
    for include, name, value in parts:
        if include and name is not None:
            cache_control.add(name, value)
    return str(cache_control)


# Response directives by which the handler says its response belongs to one
# caller (``private``) or must not be kept at all (``no-store``).
_UNSHAREABLE_DIRECTIVES = frozenset(
    {DirectiveType.PRIVATE.value, DirectiveType.NO_STORE.value}
)


def _marked_unshareable(response: Response) -> bool:
    """Whether the handler's own ``Cache-Control`` has ``private`` or ``no-store``.

    Directive names are matched as whole tokens, case-insensitively, across
    every ``Cache-Control`` field the response carries.
    """
    return _has_unshareable_directive(response.headers.getlist("cache-control"))


def _has_unshareable_directive(cache_control: Iterable[str]) -> bool:
    """Whether any of these ``Cache-Control`` values has ``private`` or ``no-store``."""
    return any(
        directive.split("=", 1)[0].strip().lower() in _UNSHAREABLE_DIRECTIVES
        for value in cache_control
        for directive in value.split(",")
    )


def _unshareable_reason(response: Response) -> CacheReason | None:
    """Why a rendered response must not be stored, or None when it may be."""
    if _marked_unshareable(response):
        return "response-cache-control"
    if "set-cookie" in response.headers:
        return "set-cookie"
    return None


def _cache_control_for(
    response: Response, cache_control: str, private_cache_control: str
) -> str:
    """The ``Cache-Control`` to send for a response the handler just rendered.

    A handler that marked its response ``private`` or ``no-store`` keeps its own
    header; the decorator's would widen what the handler allowed. A response
    that sets a cookie gets ``private_cache_control``, so a shared cache in
    front of the app does not store it either. When the decorator has no
    directive to send (a bare ``@cache()``), the handler's own header is kept,
    or none is sent (#363); a cookie still gets ``private_cache_control``.
    """
    if _marked_unshareable(response):
        return ", ".join(response.headers.getlist("cache-control"))
    if "set-cookie" in response.headers:
        return private_cache_control
    if not cache_control:
        return ", ".join(response.headers.getlist("cache-control"))
    return cache_control


def _with_cache_control(
    response: Response, cache_control: str, private_cache_control: str
) -> Response:
    if not _marked_unshareable(response):
        value = _cache_control_for(response, cache_control, private_cache_control)
        if value:
            response.headers["Cache-Control"] = value
    return response
