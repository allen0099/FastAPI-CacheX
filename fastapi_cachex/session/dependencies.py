"""FastAPI dependency injection utilities for session management."""

from typing import Annotated

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status

from .models import Session


def get_optional_session(request: Request) -> Session | None:
    """Get session from request state (optional).

    Args:
        request: FastAPI request object

    Returns:
        Session object or None if not authenticated
    """
    return getattr(request.state, "__fastapi_cachex_session", None)


def get_session(request: Request) -> Session:
    """Get session from request state (required).

    Args:
        request: FastAPI request object

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


def require_session(request: Request) -> Session:
    """Require authenticated session (alias for get_session).

    Args:
        request: FastAPI request object

    Returns:
        Session object

    Raises:
        HTTPException: 401 if session not found
    """
    return get_session(request)


# Type annotations for dependency injection
OptionalSession = Annotated[Session | None, Depends(get_optional_session)]
RequiredSession = Annotated[Session, Depends(get_session)]
SessionDep = Annotated[Session, Depends(require_session)]
