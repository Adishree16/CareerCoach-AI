"""Authenticated endpoint for deterministic analysis with optional AI enrichment."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.routes.auth import get_current_user, require_csrf_protection
from app.database.session import get_db
from app.schemas.auth import AuthenticatedUser
from app.schemas.career_analysis import CareerAnalysisRequest, CareerAnalysisResponse
from app.services.career_enrichment import CareerAnalysisEnrichment, career_enrichment_service
from app.services.career_analysis import InvalidCareerRoleError, build_career_analysis
from app.services.career_recommendations import InvalidPreferenceError
from app.services.profiles import get_profile


router = APIRouter(tags=["career-analysis"])


@router.post(
    "/career-analysis",
    response_model=CareerAnalysisResponse,
    response_model_exclude_unset=True,
    dependencies=[Depends(require_csrf_protection)],
)
async def create_career_analysis(
    request: CareerAnalysisRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Analyze a canonical role from the saved profile, then optionally enrich it with consent."""
    try:
        user_id = UUID(current_user.id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        ) from None

    try:
        profile = await run_in_threadpool(get_profile, db, user_id)
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

    preferences = request.model_dump(exclude={"role_id"}, by_alias=False)
    try:
        deterministic_analysis = await run_in_threadpool(
            build_career_analysis, profile, request.role_id, preferences
        )
    except (InvalidCareerRoleError, InvalidPreferenceError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The selected role or career preferences are invalid.",
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Detailed career analysis could not be calculated.",
        ) from None

    response = dict(deterministic_analysis)
    try:
        enrichment = await career_enrichment_service.enrich_career_analysis(
            db, user_id, deterministic_analysis
        )
        if isinstance(enrichment, dict) and enrichment.get("available") is True:
            content = CareerAnalysisEnrichment.model_validate(enrichment.get("content"))
            response["ai_enrichment"] = {
                "available": True,
                "content": content.model_dump(),
            }
    except Exception:
        # Enrichment is optional; deterministic analysis remains authoritative.
        pass
    return response
