"""FastAPI dependency injection utilities for session management."""

import warnings
from typing import TYPE_CHECKING
from typing import Annotated

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security import HTTPBearer

from fastapi_cachex.exceptions import ProxyNotSetError

from .middleware import _SESSION_READ_KEY
from .middleware import _log_in
from .middleware import _RequestSession
from .middleware import get_client_ip
from .models import Session
from .proxy import SessionManagerProxy

if TYPE_CHECKING:
    from .manager import SessionManager
    from .models import SessionUser


# ``app.state`` flag: get_session_manager() already warned about #131 there.
_PROXY_WARNED = "__fastapi_cachex_session_manager_proxy_warned"

# HTTPBearer security scheme for OpenAPI UI
_http_bearer = HTTPBearer(
    scheme_name="SessionBearer",
    description="Session authentication using Bearer token",
    auto_error=False,
)


def get_optional_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),  # noqa: ARG001
) -> Session | None:
    """Get session from request state (optional).

    This dependency automatically displays the authorization input box in OpenAPI/Swagger UI.
    The actual authentication is handled by the session middleware
    (``FastAPICacheXSessionMiddleware``); the credentials parameter
    is only used to generate the OpenAPI security scheme.

    Args:
        request: FastAPI request object
        credentials: HTTPBearer credentials (for OpenAPI UI display only)

    Returns:
        Session object or None if not authenticated
    """
    return _read_session(request)


def _read_session(request: Request) -> Session | None:
    """The session the middleware loaded for ``request``, or None.

    Records the read, so the session middleware adds ``Vary`` for the headers
    the token may come from: the response depends on them even when the
    handler never touches ``request.session`` (#372).
    """
    setattr(request.state, _SESSION_READ_KEY, True)
    return getattr(request.state, "__fastapi_cachex_session", None)


def get_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),  # noqa: ARG001
) -> Session:
    """Get session from request state (required).

    This dependency automatically displays the authorization input box in OpenAPI/Swagger UI.
    The actual authentication is handled by the session middleware
    (``FastAPICacheXSessionMiddleware``); the credentials parameter
    is only used to generate the OpenAPI security scheme.

    Args:
        request: FastAPI request object
        credentials: HTTPBearer credentials (for OpenAPI UI display only)

    Returns:
        Session object

    Raises:
        HTTPException: 401 if session not found
    """
    session = _read_session(request)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return session


def require_user_session(session: Session = Depends(get_session)) -> Session:
    """Get the request's session, requiring a logged-in user.

    ``get_session`` only checks that a session exists. Under
    ``FastAPICacheXSessionMiddleware`` any visitor who reaches a route that
    writes to ``request.session`` (a cart, a CSRF value) gets an anonymous
    session with ``user=None``, which ``get_session`` accepts. Use this
    dependency to guard routes that need an authenticated user.

    Args:
        session: The request's session, from ``get_session``

    Returns:
        Session object whose ``user`` is set

    Raises:
        HTTPException: 401 if there is no session, or the session has no user
    """
    if session.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return session


def get_session_manager(request: Request) -> "SessionManager":
    """Get SessionManager instance from app state.

    This dependency allows you to access the SessionManager instance
    that the session middleware (``FastAPICacheXSessionMiddleware``)
    registered on ``app.state`` when it handled its first request. Use this when you need
    to perform session operations like create, delete, or regenerate.

    Example:
        ```python
        from fastapi import Depends
        from fastapi_cachex.session import get_session_manager, SessionManager


        @app.post("/login")
        async def login(
            username: str, manager: SessionManager = Depends(get_session_manager)
        ):
            session, token = await manager.create_session(...)
            return {"token": token}
        ```

    Args:
        request: FastAPI request object

    Returns:
        SessionManager instance

    Raises:
        HTTPException: 500 if no session middleware has registered a
            SessionManager yet

    Warns:
        FutureWarning: Once per app, if the manager the middleware registered
            is not the one set with ``SessionManagerProxy.set()`` (or none is
            set there). 0.4.0 resolves this dependency through
            ``SessionManagerProxy`` only.
    """
    state = request.app.state
    manager: SessionManager | None = getattr(
        state, "__fastapi_cachex_session_manager", None
    )
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "SessionManager not initialized. Ensure "
                "FastAPICacheXSessionMiddleware is added to the app."
            ),
        )
    proxy_manager = _proxy_manager()
    if not getattr(state, _PROXY_WARNED, False) and manager is not proxy_manager:
        setattr(state, _PROXY_WARNED, True)
        registered = (
            "no SessionManager is set in SessionManagerProxy"
            if proxy_manager is None
            else "a different SessionManager is set in SessionManagerProxy"
        )
        warnings.warn(
            "get_session_manager() returned the SessionManager the session "
            f"middleware registered, but {registered}. Version 0.4.0 resolves "
            "get_session_manager() (and SessionManagerDep, ClientIPDep and "
            "rotate_session_id(), which use it) through SessionManagerProxy "
            "only. Call SessionManagerProxy.set(session_manager) at startup "
            "(https://github.com/allen0099/FastAPI-CacheX/issues/131).",
            FutureWarning,
            stacklevel=2,
        )
    return manager


def _proxy_manager() -> "SessionManager | None":
    """Return the manager set in ``SessionManagerProxy``, or None."""
    try:
        return SessionManagerProxy.get()
    except ProxyNotSetError:
        return None


async def rotate_session_id(request: Request) -> bool:
    """Give the request's session a new ID, as a defence against session fixation.

    To log a user in under ``FastAPICacheXSessionMiddleware``, call
    :func:`login` instead: it rotates the ID the same way, attaches the
    ``SessionUser`` that ``require_user_session`` / ``AuthenticatedSession``
    check, and makes the middleware save the session and send its token,
    also for a visitor who had no session yet. Use this function on its own
    when the ID should change without a login (a privilege change, say).

    A session token the client arrived with may have been planted by someone
    else; after rotation the old token no longer resolves, and the middleware
    sends the client a token for the new ID through the transport the request
    used. The session keeps its data, user and expiry.

    With no session loaded there is nothing to rotate: the first write to
    ``request.session`` (or ``create_session()``) starts a session under a
    fresh ID anyway.

    ``request.session["user_id"] = ...`` is application data, not a login:
    ``require_user_session`` / ``AuthenticatedSession`` still answer ``401``
    for such a session.

    Example:
        ```python
        from fastapi_cachex.session import rotate_session_id


        @app.post("/sudo")
        async def sudo(request: Request, session: AuthenticatedSession):
            ...  # re-check the password
            await rotate_session_id(request)
            request.session["elevated"] = True
            return {"ok": True}
        ```

    Args:
        request: FastAPI request object

    Returns:
        True if a loaded session was given a new ID, False if none was loaded

    Raises:
        HTTPException: 500 if no session middleware has registered a
            SessionManager yet
    """
    manager = get_session_manager(request)
    session: Session | None = getattr(request.state, "__fastapi_cachex_session", None)
    if session is None:
        return False
    await manager.regenerate_session_id(session)
    return True


async def login(request: Request, user: "SessionUser") -> Session:
    """Log ``user`` in on the request's session, under a new session ID.

    This is the way to log in under ``FastAPICacheXSessionMiddleware`` when
    routes are guarded by ``require_user_session`` / ``AuthenticatedSession``:
    a later request with the token the response carries passes them.

    - An anonymous loaded session (a visitor's cart, say) keeps its data and
      gets the user and a new ID, as with :func:`rotate_session_id`, so a
      token planted before the login is worthless: the old token no longer
      resolves.
    - A loaded session of the same ``user_id`` (a re-login) is handled the
      same way: its data is kept, the ID rotated, and ``user`` replaces the
      stored ``SessionUser``, so changed roles or metadata take effect.
    - A loaded session of a different user is deleted, and so is anything
      written to ``request.session`` earlier in this request: none of the
      previous user's data reaches the new one. A new session is created as
      below, and its old token no longer resolves.
    - With no session loaded (a new visitor, or a token that did not
      resolve) a new session is created with the user, bound to the client
      IP and User-Agent as configured.

    The middleware then saves the session, including anything written to
    ``request.session`` after the call (and, unless the loaded session was a
    different user's, before it), and sends its token through
    the transport the request used: the response header (``header_name``)
    when the request carried a header or ``Authorization: Bearer`` token,
    otherwise an HttpOnly ``Set-Cookie``. So a request that carried no token
    at all gets only the cookie; an API client that needs the token in the
    body can return ``SessionManager.issue_token()`` for the returned session.
    The response is marked ``Cache-Control: private, no-store``, as for every
    response that carries a session token.

    Within the same request, ``request.session.clear()`` after ``login()`` is
    a logout: the logged-in session is deleted and no token is sent (a cookie
    client gets its cookie expired). ``clear()`` before ``login()`` logs the
    loaded session out, and ``login()`` then starts a new session instead of
    rotating it. Calling ``login()`` twice rotates again and keeps the last
    user.

    Example:
        ```python
        from fastapi_cachex.session import SessionUser, login


        @app.post("/login")
        async def log_in(credentials: Credentials, request: Request):
            ...  # verify the credentials
            await login(request, SessionUser(user_id=credentials.username))
            return {"ok": True}
        ```

    Args:
        request: FastAPI request object
        user: The user to attach

    Returns:
        The logged-in session, also what ``get_session`` returns for the rest
        of the request

    Raises:
        RuntimeError: If the request did not pass through
            ``FastAPICacheXSessionMiddleware``. Without it, create the
            session with ``SessionManager.create_session(user=...)`` and
            return its token.
    """
    request_session = request.scope.get("session")
    if (
        not isinstance(request_session, _RequestSession)
        or request_session.middleware is None
    ):
        msg = (
            "login() needs FastAPICacheXSessionMiddleware: add it to the app "
            "so it can save the session and send its token"
        )
        raise RuntimeError(msg)
    return await _log_in(request, request_session, user)


def get_session_client_ip(
    request: Request,
    manager: "SessionManager" = Depends(get_session_manager),
) -> str | None:
    """Get the client IP address the session middleware binds sessions to.

    Resolves the address with the registered SessionManager's configuration,
    honouring `trusted_proxies` exactly like the middleware does. Pass it to
    `create_session()` so `ip_binding` checks later requests against the same
    address.

    Example:
        ```python
        from fastapi_cachex.session.dependencies import ClientIPDep, SessionManagerDep


        @app.post("/login")
        async def login(manager: SessionManagerDep, client_ip: ClientIPDep):
            session, token = await manager.create_session(user, ip_address=client_ip)
            return {"token": token}
        ```

    Args:
        request: FastAPI request object
        manager: SessionManager registered by the session middleware

    Returns:
        Client IP address or None
    """
    return get_client_ip(request, manager.config)


require_session = get_session  # Alias for required session dependency

# Type annotations for dependency injection
OptionalSession = Annotated[Session | None, Depends(get_optional_session)]
RequiredSession = Annotated[Session, Depends(get_session)]
SessionDep = Annotated[Session, Depends(get_session)]
# Despite its name, UserSessionDep accepts anonymous sessions too; making it
# require a user is a breaking change planned for 0.4.0. Use AuthenticatedSession.
UserSessionDep = Annotated[Session, Depends(get_session)]
AuthenticatedSession = Annotated[Session, Depends(require_user_session)]
SessionManagerDep = Annotated["SessionManager", Depends(get_session_manager)]
ClientIPDep = Annotated[str | None, Depends(get_session_client_ip)]
