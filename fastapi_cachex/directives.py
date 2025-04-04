from enum import Enum


class DirectiveType(Enum):
    """Enum representing Cache-Control directives."""

    MAX_AGE = "max-age"
    S_MAXAGE = "s-maxage"
    NO_CACHE = "no-cache"
    NO_STORE = "no-store"
    NO_TRANSFORM = "no-transform"
    MUST_REVALIDATE = "must-revalidate"
    PROXY_REVALIDATE = "proxy-revalidate"
    MUST_UNDERSTAND = "must-understand"
    PRIVATE = "private"
    PUBLIC = "public"
    IMMUTABLE = "immutable"
    STALE_WHILE_REVALIDATE = "stale-while-revalidate"
    STALE_IF_ERROR = "stale-if-error"
