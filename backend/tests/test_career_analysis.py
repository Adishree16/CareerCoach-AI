"""Offline tests for the deterministic detailed career-analysis API."""

import unittest
import copy
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.api.routes import career_analysis
from app.api.routes.auth import get_current_user, require_csrf_protection
from app.api.router import api_router
from app.database.session import get_db
from app.schemas.auth import AuthenticatedUser
from app.schemas.career_analysis import CareerAnalysisRequest, CareerAnalysisResponse
from app.services.career_enrichment import CareerAnalysisEnrichment
from app.services.career_analysis import InvalidCareerRoleError, build_career_analysis
from app.services.career_knowledge_graph import load_knowledge_base
from app.services.career_recommendations import InvalidPreferenceError


def run_without_event_loop(awaitable):
    """Drive the route coroutine when all mocked awaits resolve inline."""
    try:
        awaitable.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("A mocked route operation unexpectedly performed I/O.")


class CareerAnalysisServiceTests(unittest.TestCase):
    def setUp(self):
        self.knowledge_base = load_knowledge_base()
        self.role = next(role for role in self.knowledge_base["roles"] if role["id"] == "software_engineer")
        self.profile = {
            "careerSkills": {
                "careerStatus": "Student",
                "technicalSkills": ["Python"],
                "internshipExperience": [],
                "professionalExperience": [],
            },
            "additionalInformation": {"projects": [], "certifications": [], "achievements": None},
        }
        self.preferences = {
            "career_interest": "artificial_intelligence_machine_learning",
            "preferred_work_activity": "working_with_ai_and_automation",
            "career_goal": "get_my_first_job",
            "career_priority": "continuous_learning_and_growth",
        }
        self.recommendation_result = {
            "contextual_preferences": {
                "career_goal": {"option_id": "get_my_first_job", "label": "Get my first job"},
                "career_priority": {
                    "option_id": "continuous_learning_and_growth",
                    "label": "Continuous learning and growth",
                },
            },
            "roles": [{
                "role_id": self.role["id"],
                "role_name": self.role["name"],
                "components": {
                    "skill_alignment": {
                        "score": 12.25, "maximum_points": 43.75,
                        "maximum_applicable_points": 43.75, "available": True,
                        "status": "scored", "details": {
                            "evidence_coverage_percentage": 28.0,
                            "matched_skills": [{"skill_id": "python", "skill_name": "Python"}],
                            "unknown_unassessed_requirements": [{"skill_id": "go", "skill_name": "Go"}],
                            "alternative_groups": [{"group_id": "languages"}],
                            "complementary_groups": [], "compatible_stacks": {"options": []},
                            "excluded_conditional_requirements": [],
                        },
                    },
                    "projects_experience": {
                        "score": 4.0, "maximum_points": 31.25,
                        "maximum_applicable_points": 18.75, "available": True,
                        "status": "partially_available", "details": {
                            "categories": {"projects": {"earned_points": 4.0}},
                            "inapplicable_categories": ["professional_experience"],
                        },
                    },
                    "interest_alignment": {
                        "score": 15.0, "maximum_points": 25.0,
                        "maximum_applicable_points": 25.0, "available": True,
                        "status": "available", "details": {"career_interest_status": "matched"},
                    },
                },
                "status": "available",
                "unavailable_reasons": [],
            }],
        }

    def test_valid_role_uses_saved_profile_scoring_and_curated_catalogue_data(self):
        with patch("app.services.career_analysis.load_knowledge_base", return_value=self.knowledge_base), \
             patch("app.services.career_analysis.score_career_recommendations", return_value=self.recommendation_result) as scorer:
            result = build_career_analysis(self.profile, "software_engineer", self.preferences)

        scorer.assert_called_once_with(self.profile, self.preferences)
        self.assertEqual(result["role_overview"]["name"], self.role["name"])
        self.assertEqual(result["role_overview"]["domain"], self.role["career_domain"])
        self.assertEqual(result["role_overview"]["typical_responsibilities"], self.role["typical_responsibilities"])
        self.assertEqual(result["skill_alignment"]["score"], 12.25)
        self.assertEqual(result["skill_alignment"]["evidence_coverage_percentage"], 28.0)
        self.assertEqual(result["skill_alignment"]["requirements_not_demonstrated_in_profile"][0]["skill_name"], "Go")
        self.assertIn("not confirmed deficiencies", result["skill_alignment"]["requirements_interpretation"])
        self.assertEqual(result["projects_experience"]["category_breakdown"], {"projects": {"earned_points": 4.0}})
        self.assertEqual(result["projects_experience"]["inapplicable_categories"], ["professional_experience"])
        self.assertEqual(result["project_directions"]["catalogue_suggestions"], self.role["relevant_project_types"])
        self.assertIn("not projects recorded as completed", result["project_directions"]["distinction"])
        self.assertEqual(result["career_progression"]["paths"], self.role["career_progression_paths"])
        self.assertEqual(result["contextual_preferences"]["career_goal"]["label"], "Get my first job")
        self.assertEqual(result["contextual_preferences"]["career_priority"]["label"], "Continuous learning and growth")
        self.assertNotIn("career_goal", result["skill_alignment"])
        CareerAnalysisResponse.model_validate(result)

    def test_unknown_or_malformed_role_is_rejected_before_scoring(self):
        with patch("app.services.career_analysis.load_knowledge_base", return_value=self.knowledge_base), \
             patch("app.services.career_analysis.score_career_recommendations") as scorer:
            for role_id in ("unknown-role", " ", None):
                with self.subTest(role_id=role_id), self.assertRaises(InvalidCareerRoleError):
                    build_career_analysis(self.profile, role_id, self.preferences)
        scorer.assert_not_called()

    def test_invalid_preferences_are_propagated_for_safe_route_validation(self):
        with patch("app.services.career_analysis.load_knowledge_base", return_value=self.knowledge_base), \
             patch("app.services.career_analysis.score_career_recommendations", side_effect=InvalidPreferenceError):
            with self.assertRaises(InvalidPreferenceError):
                build_career_analysis(self.profile, "software_engineer", self.preferences)

    def test_real_deterministic_scorers_produce_a_valid_selected_role_analysis(self):
        result = build_career_analysis(self.profile, "software_engineer", self.preferences)
        validated = CareerAnalysisResponse.model_validate(result)
        self.assertEqual(validated.role_overview.role_id, "software_engineer")
        self.assertEqual(validated.role_overview.name, "Software Engineer")
        self.assertEqual(validated.skill_alignment.maximum_points, 43.75)
        self.assertEqual(validated.projects_experience.maximum_points, 31.25)
        self.assertEqual(validated.interest_alignment.maximum_points, 25.0)
        self.assertEqual(
            validated.contextual_preferences["career_goal"].label,
            "Get my first job",
        )

    def test_response_explicitly_handles_empty_project_and_progression_data(self):
        role = dict(self.role)
        role["relevant_project_types"] = []
        role["career_progression_paths"] = []
        kb = dict(self.knowledge_base)
        kb["roles"] = [role if item["id"] == role["id"] else item for item in kb["roles"]]
        with patch("app.services.career_analysis.load_knowledge_base", return_value=kb), \
             patch("app.services.career_analysis.score_career_recommendations", return_value=self.recommendation_result):
            result = build_career_analysis(self.profile, role["id"], self.preferences)
        self.assertEqual(result["project_directions"]["catalogue_suggestions"], [])
        self.assertEqual(result["career_progression"]["paths"], [])
        self.assertTrue(any("No curated project directions" in item for item in result["availability"]["limitations"]))
        self.assertTrue(any("No curated career progression" in item for item in result["availability"]["limitations"]))
        CareerAnalysisResponse.model_validate(result)


class CareerAnalysisApiTests(unittest.TestCase):
    def setUp(self):
        self.user_id = uuid4()
        self.db = object()
        self.current_user = AuthenticatedUser(id=str(self.user_id), email="user@example.test")
        self.profile = {"careerSkills": {"careerStatus": "Student", "technicalSkills": []}}
        self.request = CareerAnalysisRequest.model_validate({
            "role_id": "software_engineer",
            "career_interest": "ai_and_machine_learning",
            "preferred_work_activity": "working_with_ai_and_automation",
            "career_goal": "get_my_first_job",
            "career_priority": "continuous_learning_and_growth",
        })
        self.response = {"role_overview": {"role_id": "software_engineer"}}

    def call_endpoint(self, *, request=None, current_user=None):
        async def immediate_thread_call(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch.object(career_analysis, "run_in_threadpool", new=immediate_thread_call):
            return run_without_event_loop(career_analysis.create_career_analysis(
                self.request if request is None else request,
                self.current_user if current_user is None else current_user,
                self.db,
            ))

    def test_route_scopes_profile_to_authenticated_user_and_passes_four_preferences(self):
        with patch.object(career_analysis, "get_profile", return_value=self.profile) as get_profile, \
             patch.object(career_analysis, "build_career_analysis", return_value=self.response) as service:
            self.assertEqual(self.call_endpoint(), self.response)
        get_profile.assert_called_once_with(self.db, self.user_id)
        service.assert_called_once_with(self.profile, "software_engineer", {
            "career_interest": "ai_and_machine_learning",
            "preferred_work_activity": "working_with_ai_and_automation",
            "career_goal": "get_my_first_job",
            "career_priority": "continuous_learning_and_growth",
        })

    def test_successful_enrichment_is_separate_and_preserves_deterministic_analysis(self):
        deterministic = {
            "role_overview": {"role_id": "software_engineer", "name": "Software Engineer"},
            "scores": {"final_score": 42.5},
            "project_directions": {"catalogue_suggestions": ["REST service"]},
        }
        ai_content = CareerAnalysisEnrichment.model_validate({
            "why_it_may_suit": [{"text": "This role connects with Python in the profile.", "skill_ids": ["python"]}],
            "strengths": [{"text": "The profile lists Python as a relevant skill.", "skill_ids": ["python"]}],
            "areas_to_explore": [{"text": "Explore another applicable role skill.", "skill_ids": ["testing"]}],
            "practice_project_suggestions": [{
                "project_id": "software_engineer::project::rest-service",
                "explanation": {"text": "This idea can exercise Python through practice.", "skill_ids": ["python"]},
            }],
        })
        events = []

        class FakeEnrichment:
            async def enrich_career_analysis(self, db, user_id, analysis):
                events.append(("enrichment", db, user_id, analysis))
                return {"available": True, "content": ai_content.model_dump(), "provider": "hidden"}

        original = copy.deepcopy(deterministic)
        with patch.object(career_analysis, "get_profile", return_value=self.profile), \
             patch.object(career_analysis, "build_career_analysis", side_effect=lambda *args: (events.append(("deterministic",)) or deterministic)), \
             patch.object(career_analysis, "career_enrichment_service", FakeEnrichment()):
            response = self.call_endpoint()

        self.assertEqual(events[0], ("deterministic",))
        self.assertEqual(events[1], ("enrichment", self.db, self.user_id, deterministic))
        self.assertEqual({key: value for key, value in response.items() if key != "ai_enrichment"}, original)
        self.assertEqual(response["ai_enrichment"], {"available": True, "content": ai_content.model_dump()})
        self.assertNotIn("provider", response["ai_enrichment"])

    def test_enrichment_unavailable_invalid_or_failing_keeps_deterministic_result(self):
        deterministic = {"role_overview": {"role_id": "software_engineer"}, "scores": {"score": 1}}
        outcomes = (
            {"available": False, "content": None},
            {"available": True, "content": {"unexpected": "data"}},
            RuntimeError("private provider detail"),
        )
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                class FakeEnrichment:
                    async def enrich_career_analysis(self, *_args):
                        if isinstance(outcome, Exception):
                            raise outcome
                        return outcome

                with patch.object(career_analysis, "get_profile", return_value=self.profile), \
                     patch.object(career_analysis, "build_career_analysis", return_value=deterministic), \
                     patch.object(career_analysis, "career_enrichment_service", FakeEnrichment()):
                    response = self.call_endpoint()
                self.assertEqual(response, deterministic)

    def test_ai_response_schema_is_optional_and_validates_project_suggestions(self):
        deterministic = build_career_analysis(self.profile, "software_engineer", {
            "career_interest": "artificial_intelligence_machine_learning",
            "preferred_work_activity": "working_with_ai_and_automation",
            "career_goal": "get_my_first_job",
            "career_priority": "continuous_learning_and_growth",
        })
        parsed_deterministic = CareerAnalysisResponse.model_validate(deterministic)
        self.assertNotIn("ai_enrichment", parsed_deterministic.model_dump(exclude_unset=True))
        enriched = {
            **deterministic,
            "ai_enrichment": {
                "available": True,
                "content": CareerAnalysisEnrichment.model_validate({
                    "why_it_may_suit": [{"text": "This role connects with Python in the profile.", "skill_ids": ["python"]}],
                    "practice_project_suggestions": [{
                        "project_id": "software_engineer::project::rest-service",
                        "explanation": {"text": "This idea can exercise Python through practice.", "skill_ids": ["python"]},
                    }],
                }).model_dump(),
            },
        }
        parsed = CareerAnalysisResponse.model_validate(enriched)
        self.assertEqual(parsed.ai_enrichment.content.practice_project_suggestions[0].project_id,
                         "software_engineer::project::rest-service")

    def test_missing_profile_returns_safe_404(self):
        with patch.object(career_analysis, "get_profile", return_value=None), \
             patch.object(career_analysis, "build_career_analysis") as service:
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint()
        self.assertEqual(raised.exception.status_code, 404)
        service.assert_not_called()

    def test_invalid_authenticated_user_id_returns_401(self):
        bad_user = AuthenticatedUser(id="not-a-uuid", email="ignored@example.test")
        with patch.object(career_analysis, "get_profile") as get_profile:
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint(current_user=bad_user)
        self.assertEqual(raised.exception.status_code, 401)
        get_profile.assert_not_called()

    def test_invalid_role_and_preferences_return_safe_422(self):
        for error in (InvalidCareerRoleError(), InvalidPreferenceError()):
            with self.subTest(error=type(error).__name__), \
                 patch.object(career_analysis, "get_profile", return_value=self.profile), \
                 patch.object(career_analysis, "build_career_analysis", side_effect=error):
                with self.assertRaises(HTTPException) as raised:
                    self.call_endpoint()
            self.assertEqual(raised.exception.status_code, 422)
            self.assertNotIn("traceback", str(raised.exception.detail).casefold())

    def test_profile_and_analysis_failures_are_safe_500s(self):
        with patch.object(career_analysis, "get_profile", side_effect=RuntimeError("private detail")):
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint()
        self.assertEqual(raised.exception.status_code, 500)
        self.assertNotIn("private detail", str(raised.exception.detail))

        with patch.object(career_analysis, "get_profile", return_value=self.profile), \
             patch.object(career_analysis, "build_career_analysis", side_effect=RuntimeError("internal detail")):
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint()
        self.assertEqual(raised.exception.status_code, 500)
        self.assertNotIn("internal detail", str(raised.exception.detail))

    def test_request_requires_role_and_all_four_preferences_and_rejects_extra_identity_data(self):
        valid = {
            "roleId": "software_engineer",
            "careerInterest": "ai_and_machine_learning",
            "preferredWorkActivity": "working_with_ai_and_automation",
            "careerGoal": "get_my_first_job",
            "careerPriority": "continuous_learning_and_growth",
        }
        parsed = CareerAnalysisRequest.model_validate(valid)
        self.assertEqual(parsed.role_id, "software_engineer")
        for field in valid:
            with self.subTest(missing=field), self.assertRaises(ValidationError):
                CareerAnalysisRequest.model_validate({key: value for key, value in valid.items() if key != field})
        with self.assertRaises(ValidationError):
            CareerAnalysisRequest.model_validate({**valid, "user_id": str(self.user_id), "profile": {}})

    def test_route_has_auth_csrf_and_is_registered_under_api_prefix(self):
        route = next(item for item in career_analysis.router.routes if isinstance(item, APIRoute))
        self.assertEqual(route.path, "/career-analysis")
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        self.assertIn(get_current_user, dependencies)
        self.assertIn(get_db, dependencies)
        self.assertIn(require_csrf_protection, dependencies)
        app = FastAPI()
        app.include_router(api_router, prefix="/api")
        self.assertIn("/api/career-analysis", app.openapi()["paths"])


if __name__ == "__main__":
    unittest.main()
