"""FastAPI dependency injection utilities for session management."""

from typing import TYPE_CHECKING
from typing import Annotated

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security import HTTPBearer

from .middleware import get_client_ip
from .models import Session

if TYPE_CHECKING:
    from .manager import SessionManager


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
    session: Session | None = getattr(request.state, "__fastapi_cachex_session", None)
    if session is None:
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
    """
    manager: SessionManager | None = getattr(
        request.app.state, "__fastapi_cachex_session_manager", None
    )
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "SessionManager not initialized. Ensure "
                "FastAPICacheXSessionMiddleware is added to the app."
            ),
        )
    return manager


async def rotate_session_id(request: Request) -> bool:
    """Give the request's session a new ID, as a defence against session fixation.

    Call it at login, before attaching the user. A session token the client
    arrived with may have been planted by someone else; after rotation the
    old token no longer resolves, and the middleware sends the client a token
    for the new ID through the transport the request used. The session keeps
    its data, user and expiry.

    With no session loaded there is nothing to rotate: the first write to
    ``request.session`` (or ``create_session()``) starts a session under a
    fresh ID anyway. So this works for a new visitor and a returning one alike.

    Example:
        ```python
        from fastapi_cachex.session.dependencies import rotate_session_id


        @app.post("/login")
        async def login(request: Request):
            ...  # verify the credentials
            await rotate_session_id(request)
            request.session["user_id"] = "123"
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
UserSessionDep = Annotated[Session, Depends(get_session)]
SessionManagerDep = Annotated["SessionManager", Depends(get_session_manager)]
ClientIPDep = Annotated[str | None, Depends(get_session_client_ip)]
