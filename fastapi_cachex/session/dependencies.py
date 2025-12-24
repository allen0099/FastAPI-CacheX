"""FastAPI dependency injection utilities for session management."""

from typing import TYPE_CHECKING
from typing import Annotated

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security import HTTPBearer

from .models import Session

if TYPE_CHECKING:
    from .manager import SessionManager


# HTTPBearer security scheme for OpenAPI UI
_http_bearer = HTTPBearer(auto_error=False)


def get_optional_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),  # noqa: ARG001
) -> Session | None:
    """Get session from request state (optional).

    This dependency automatically displays the authorization input box in OpenAPI/Swagger UI.
    The actual authentication is handled by SessionMiddleware; the credentials parameter
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
    The actual authentication is handled by SessionMiddleware; the credentials parameter
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
    that was registered via SessionMiddleware. Use this when you need
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
        HTTPException: 500 if SessionManager not found in app state
    """
    manager: SessionManager | None = getattr(
        request.app.state, "__fastapi_cachex_session_manager", None
    )
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SessionManager not initialized. Ensure SessionMiddleware is added to the app.",
        )
    return manager


require_session = get_session  # Alias for required session dependency

# Type annotations for dependency injection
OptionalSession = Annotated[Session | None, Depends(get_optional_session)]
RequiredSession = Annotated[Session, Depends(get_session)]
SessionDep = Annotated[Session, Depends(get_session)]
UserSessionDep = Annotated[Session, Depends(get_session)]
SessionManagerDep = Annotated["SessionManager", Depends(get_session_manager)]
