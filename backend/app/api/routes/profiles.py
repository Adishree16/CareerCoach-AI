from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user, require_csrf_protection
from app.database.session import get_db
from app.schemas.auth import AuthenticatedUser
from app.schemas.profiles import ProfileCreateRequest, ProfileResponse
from app.services.profiles import (
    ProfileConflictError,
    ProfilePersistenceError,
    get_profile,
    save_profile,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _verified_user_id(current_user: AuthenticatedUser) -> UUID:
    """Convert the identity verified by the auth dependency to the model UUID."""
    try:
        return UUID(current_user.id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        ) from None


@router.get("/me", response_model=ProfileResponse)
def read_my_profile(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    user_id = _verified_user_id(current_user)
    try:
        profile = get_profile(db, user_id)
    except ProfilePersistenceError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Profile data could not be retrieved.",
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Profile data could not be retrieved.",
        ) from None

    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found.",
        )
    return profile


@router.put(
    "/me",
    response_model=ProfileResponse,
    dependencies=[Depends(require_csrf_protection)],
)
def save_my_profile(
    profile_data: ProfileCreateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    user_id = _verified_user_id(current_user)
    try:
        return save_profile(db, user_id, profile_data)
    except ProfileConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Profile data could not be saved because of a conflict. Please retry.",
        ) from None
    except ProfilePersistenceError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Profile data could not be saved.",
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Profile data could not be saved.",
        ) from None
