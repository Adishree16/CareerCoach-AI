"""Authenticated API for recording optional AI personalization consent."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user, require_csrf_protection
from app.database.session import get_db
from app.schemas.ai_consent import AIConsentDecisionRequest, AIConsentResponse
from app.schemas.auth import AuthenticatedUser
from app.services.ai_consent import (
    AIConsentDecisionError,
    AIConsentPersistenceError,
    AIConsentVersionError,
    NoActiveAIConsentError,
    get_ai_consent,
    save_ai_consent,
    withdraw_ai_consent,
)


router = APIRouter(prefix="/ai-consent", tags=["ai-consent"])


def _verified_user_id(current_user: AuthenticatedUser) -> UUID:
    try:
        return UUID(current_user.id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        ) from None


def _safe_database_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="AI consent could not be processed.",
    )


@router.get("", response_model=AIConsentResponse)
def read_ai_consent(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    user_id = _verified_user_id(current_user)
    try:
        return get_ai_consent(db, user_id)
    except AIConsentPersistenceError:
        raise _safe_database_error() from None
    except Exception:
        raise _safe_database_error() from None


@router.post(
    "",
    response_model=AIConsentResponse,
    dependencies=[Depends(require_csrf_protection)],
)
def decide_ai_consent(
    request: AIConsentDecisionRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    user_id = _verified_user_id(current_user)
    try:
        return save_ai_consent(
            db,
            user_id,
            request.decision,
            request.notice_version,
            request.scope_version,
        )
    except AIConsentVersionError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The AI consent notice must be reviewed again.",
        ) from None
    except AIConsentDecisionError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The AI consent decision is invalid.",
        ) from None
    except AIConsentPersistenceError:
        raise _safe_database_error() from None
    except Exception:
        raise _safe_database_error() from None


@router.post(
    "/withdraw",
    response_model=AIConsentResponse,
    dependencies=[Depends(require_csrf_protection)],
)
def withdraw_my_ai_consent(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    user_id = _verified_user_id(current_user)
    try:
        return withdraw_ai_consent(db, user_id)
    except NoActiveAIConsentError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There is no active AI consent to withdraw.",
        ) from None
    except AIConsentPersistenceError:
        raise _safe_database_error() from None
    except Exception:
        raise _safe_database_error() from None
