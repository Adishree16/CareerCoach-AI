"""Authenticated endpoint for deterministic career recommendations."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.routes.auth import get_current_user, require_csrf_protection
from app.database.session import get_db
from app.schemas.auth import AuthenticatedUser
from app.schemas.recommendations import RecommendationRequest
from app.services.career_enrichment import RecommendationsEnrichment, career_enrichment_service
from app.services.career_recommendations import (
    InvalidPreferenceError,
    score_career_recommendations,
)
from app.services.profiles import ProfilePersistenceError, get_profile


router = APIRouter(tags=["recommendations"])


@router.post(
    "/recommendations",
    dependencies=[Depends(require_csrf_protection)],
)
async def create_recommendations(
    preferences: RecommendationRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        user_id = UUID(current_user.id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        ) from None

    try:
        profile = await run_in_threadpool(get_profile, db, user_id)
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

    try:
        deterministic_result = await run_in_threadpool(
            score_career_recommendations,
            profile,
            preferences.model_dump(by_alias=False),
        )
    except InvalidPreferenceError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="One or more career preference selections are invalid.",
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Career recommendations could not be calculated.",
        ) from None

    # Keep the deterministic response authoritative. Enrichment is optional and
    # is exposed only after the service has checked consent and validated output.
    response = dict(deterministic_result)
    try:
        enrichment = await career_enrichment_service.enrich_recommendations(
            db, user_id, deterministic_result
        )
        if isinstance(enrichment, dict) and enrichment.get("available") is True:
            content = RecommendationsEnrichment.model_validate(enrichment.get("content"))
            top_ids = deterministic_result.get("top_recommendation_role_ids")
            if isinstance(top_ids, list) and [item.role_id for item in content.roles] == top_ids:
                response["ai_enrichment"] = {
                    "available": True,
                    "roles": [item.model_dump() for item in content.roles],
                }
    except Exception:
        # No AI failure is allowed to make deterministic recommendations fail.
        pass
    return response
