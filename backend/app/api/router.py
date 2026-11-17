from fastapi import APIRouter

from app.api.routes.ai_consent import router as ai_consent_router
from app.api.routes.auth import router as auth_router
from app.api.routes.career_analysis import router as career_analysis_router
from app.api.routes.career_roadmaps import router as career_roadmaps_router
from app.api.routes.health import router as health_router
from app.api.routes.profiles import router as profiles_router
from app.api.routes.recommendations import router as recommendations_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(ai_consent_router)
api_router.include_router(auth_router)
api_router.include_router(profiles_router)
api_router.include_router(recommendations_router)
api_router.include_router(career_analysis_router)
api_router.include_router(career_roadmaps_router)
