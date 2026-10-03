"""The `Cache-Control` header `@cache` sends, and which responses stay private."""

from collections.abc import Iterable
from typing import Literal

from fastapi import Response

from .directives import DirectiveType

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
) -> str:
    """The ``Cache-Control`` value for a ``@cache`` route's arguments.

    ``no_cache`` sends only ``no-cache`` (plus ``must-revalidate``); otherwise
    the directives follow in a fixed order: scope, ``max-age``,
    ``must-revalidate``, the stale directive, ``immutable``.
    """
    cache_control = CacheControl()
    if no_cache:
        cache_control.add(DirectiveType.NO_CACHE)
        if must_revalidate:
            cache_control.add(DirectiveType.MUST_REVALIDATE)
        return str(cache_control)

    # 1. Access scope (public/private)
    if public:
        cache_control.add(DirectiveType.PUBLIC)
    elif private:
        cache_control.add(DirectiveType.PRIVATE)

    # 2. Cache time settings
    if ttl is not None:
        cache_control.add(DirectiveType.MAX_AGE, ttl)

    # 3. Validation related
    if must_revalidate:
        cache_control.add(DirectiveType.MUST_REVALIDATE)

    # 4. Stale response handling (stale_ttl is validated at decoration time)
    if stale == "revalidate":
        cache_control.add(DirectiveType.STALE_WHILE_REVALIDATE, stale_ttl)
    elif stale == "error":
        cache_control.add(DirectiveType.STALE_IF_ERROR, stale_ttl)

    # 5. Special flags
    if immutable:
        cache_control.add(DirectiveType.IMMUTABLE)

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


def _unshareable_reason(response: Response) -> str | None:
    """Why a rendered response must not be stored, or None when it may be."""
    if _marked_unshareable(response):
        return "response Cache-Control is private or no-store"
    if "set-cookie" in response.headers:
        return "response sets a cookie"
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
