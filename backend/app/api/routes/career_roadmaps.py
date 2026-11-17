"""Authenticated, read-only personalized career roadmap endpoint."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.routes.auth import get_current_user
from app.database.session import get_db
from app.schemas.auth import AuthenticatedUser
from app.schemas.career_roadmaps import CareerRoadmapResponse
from app.services.career_enrichment import (
    RoadmapEnrichment,
    career_enrichment_service,
    log_roadmap_ai_diagnostic,
)
from app.services.career_roadmaps import InvalidCareerRoleError, build_career_roadmap
from app.services.profiles import ProfilePersistenceError, get_profile


router = APIRouter(tags=["career-roadmaps"])


@router.get(
    "/career-roadmaps/{role_id}",
    response_model=CareerRoadmapResponse,
    response_model_exclude_unset=True,
)
async def read_career_roadmap(
    role_id: Annotated[str, Path(min_length=1, max_length=100)],
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Generate roadmap guidance for a canonical role using the current user's saved profile."""
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
        deterministic_roadmap = await run_in_threadpool(build_career_roadmap, profile, role_id)
    except InvalidCareerRoleError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Career role not found.",
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Career roadmap could not be prepared.",
        ) from None

    response = dict(deterministic_roadmap)
    enrichment_attached = False
    try:
        enrichment = await career_enrichment_service.enrich_roadmap(db, user_id, deterministic_roadmap)
        if isinstance(enrichment, dict) and enrichment.get("available") is True:
            content = RoadmapEnrichment.model_validate(enrichment.get("content"))
            log_roadmap_ai_diagnostic("final_route_validation", "passed")
            response["ai_enrichment"] = {
                "available": True,
                "content": content.model_dump(),
            }
            enrichment_attached = True
        else:
            log_roadmap_ai_diagnostic("final_route_validation", "skipped")
    except Exception:
        # Enrichment is optional; the deterministic roadmap remains usable.
        log_roadmap_ai_diagnostic("final_route_validation", "failed")
    log_roadmap_ai_diagnostic(
        "response_assembly",
        "ai_enrichment_attached" if enrichment_attached else "deterministic_fallback_returned",
    )
    return response
