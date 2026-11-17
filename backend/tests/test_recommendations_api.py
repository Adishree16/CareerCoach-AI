"""Offline route and schema tests for career recommendation requests."""

import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.api.routes import recommendations
from app.api.routes.auth import get_current_user, require_csrf_protection
from app.database.session import get_db
from app.schemas.auth import AuthenticatedUser
from app.schemas.recommendations import RecommendationRequest
from app.services.career_recommendations import (
    InvalidPreferenceError,
    RecommendationDataIntegrityError,
)


def run_without_event_loop(awaitable):
    """Drive route coroutines when all mocked awaited operations resolve inline."""
    try:
        awaitable.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("A mocked route operation unexpectedly performed I/O.")


class RecommendationApiTests(unittest.TestCase):
    def setUp(self):
        self.user_id = uuid4()
        self.db = object()
        self.current_user = AuthenticatedUser(id=str(self.user_id), email="user@example.test")
        self.profile = {
            "basicInformation": {"firstName": "Example"},
            "careerSkills": {
                "careerStatus": "Student",
                "technicalSkills": ["Python"],
                "internshipExperience": [],
                "professionalExperience": [],
            },
            "additionalInformation": {"projects": [], "certifications": [], "achievements": None},
        }
        # These exact labels are the values produced by the current frontend selects.
        self.preferences = {
            "career_interest": "Software Development",
            "preferred_work_activity": "Building applications and software",
            "career_goal": "Get my first job",
            "career_priority": "Continuous learning and growth",
        }
        self.response_payload = {
            "maximum_score": 100.0,
            "roles_evaluated": 22,
            "contextual_preferences": {"career_goal": {"option_id": "get_my_first_job", "label": "Get my first job"}, "career_priority": {"option_id": "continuous_learning_and_growth", "label": "Continuous learning and growth"}},
            "top_recommendation_role_ids": ["role_0", "role_1", "role_2"],
            "roles": [
                {
                    "role_id": f"role_{index}",
                    "role_name": f"Role {index}",
                    "components": {
                        "skill_alignment": {"score": 12.5, "maximum_points": 43.75, "maximum_applicable_points": 43.75, "available": True, "status": "scored", "details": {"evidence_coverage_percentage": 35.7}},
                        "projects_experience": {"score": 7.0, "maximum_points": 31.25, "maximum_applicable_points": 18.75, "available": True, "status": "partially_available", "details": {"evaluated_maximum_points": 18.75, "unavailable_maximum_points": 12.5, "redistribution": {"applied": True}}},
                        "interest_alignment": {"score": None, "maximum_points": 25.0, "maximum_applicable_points": 0.0, "available": False, "status": "unavailable", "details": {}},

                    },
                    "final_score": 19.5,
                    "maximum_applicable_score": 62.5,
                    "maximum_score": 100.0,
                    "available": True,
                    "status": "partially_available",
                    "unavailable_reasons": [{"component": "interest_alignment", "reason": "No reviewed relationship."}],
                }
                for index in range(22)
            ],
        }
        self.app = FastAPI()
        self.app.include_router(recommendations.router)

    def request_model(self, values=None):
        return RecommendationRequest.model_validate(values or self.preferences)

    def call_endpoint(self, *, preferences=None, current_user=None, db=None):
        async def immediate_thread_call(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch.object(recommendations, "run_in_threadpool", new=immediate_thread_call):
            return run_without_event_loop(recommendations.create_recommendations(
                preferences or self.request_model(),
                self.current_user if current_user is None else current_user,
                self.db if db is None else db,
            ))

    def recommendation_route(self):
        return next(
            route for route in recommendations.router.routes
            if isinstance(route, APIRoute) and route.path == "/recommendations"
        )

    def test_authenticated_route_returns_all_22_results_and_preserves_details(self):
        with patch.object(recommendations, "get_profile", return_value=self.profile) as get_profile, \
             patch.object(recommendations, "score_career_recommendations", return_value=self.response_payload) as scorer:
            response = self.call_endpoint()

        self.assertEqual(response, self.response_payload)
        self.assertEqual(len(response["roles"]), 22)
        self.assertEqual(response["top_recommendation_role_ids"], ["role_0", "role_1", "role_2"])
        self.assertEqual([role["role_id"] for role in response["roles"]], [f"role_{index}" for index in range(22)])
        self.assertEqual(response["roles"][0]["components"]["projects_experience"]["maximum_applicable_points"], 18.75)
        self.assertEqual(response["roles"][0]["components"]["skill_alignment"]["details"]["evidence_coverage_percentage"], 35.7)
        get_profile.assert_called_once_with(self.db, self.user_id)
        scorer.assert_called_once_with(self.profile, self.preferences)

    def test_successful_enrichment_is_separate_and_cannot_change_scores_or_ranking(self):
        deterministic = self.response_payload
        explanations = {
            "available": True,
            "provider": "gemini",
            "content": {"roles": [
                {"role_id": role_id, "explanation": {"text": "Grounded explanation based on matched skills.", "skill_ids": ["python"]}}
                for role_id in deterministic["top_recommendation_role_ids"]
            ]},
            "reason": None,
        }

        class FakeEnrichment:
            def __init__(self):
                self.calls = []

            async def enrich_recommendations(self, db, user_id, deterministic_result):
                self.calls.append((db, user_id, deterministic_result))
                return explanations

        enrichment = FakeEnrichment()
        with patch.object(recommendations, "get_profile", return_value=self.profile), \
             patch.object(recommendations, "score_career_recommendations", return_value=deterministic) as scorer, \
             patch.object(recommendations, "career_enrichment_service", enrichment):
            response = self.call_endpoint()

        self.assertEqual(response["roles"], deterministic["roles"])
        self.assertEqual(response["top_recommendation_role_ids"], deterministic["top_recommendation_role_ids"])
        self.assertEqual(response["ai_enrichment"]["available"], True)
        self.assertEqual([item["role_id"] for item in response["ai_enrichment"]["roles"]], ["role_0", "role_1", "role_2"])
        self.assertNotIn("provider", response["ai_enrichment"])
        self.assertNotIn("reason", response["ai_enrichment"])
        self.assertEqual(len(response["roles"][3:]), 19)
        self.assertEqual(enrichment.calls, [(self.db, self.user_id, deterministic)])
        scorer.assert_called_once_with(self.profile, self.preferences)

    def test_unavailable_or_invalid_enrichment_returns_deterministic_response_unchanged(self):
        for outcome in (
            {"available": False, "provider": None, "content": None, "reason": "consent_not_current"},
            {"available": False, "provider": None, "content": None, "reason": "providers_unavailable_or_invalid_output"},
            {"available": True, "content": {"roles": [{"role_id": "other_role", "explanation": {"text": "Not aligned.", "skill_ids": ["python"]}}]}},
            RuntimeError("private enrichment exception"),
        ):
            with self.subTest(outcome=outcome):
                class FakeEnrichment:
                    async def enrich_recommendations(self, _db, _user_id, _result):
                        if isinstance(outcome, Exception):
                            raise outcome
                        return outcome

                deterministic = dict(self.response_payload)
                with patch.object(recommendations, "get_profile", return_value=self.profile), \
                     patch.object(recommendations, "score_career_recommendations", return_value=deterministic), \
                     patch.object(recommendations, "career_enrichment_service", FakeEnrichment()):
                    response = self.call_endpoint()
                self.assertNotIn("ai_enrichment", response)
                self.assertEqual(response["roles"], deterministic["roles"])
                self.assertEqual(response["top_recommendation_role_ids"], deterministic["top_recommendation_role_ids"])

    def test_stable_ids_are_accepted_and_passed_unchanged_to_service(self):
        body = {
            "career_interest": "software_development",
            "preferred_work_activity": "building_applications_and_software",
            "career_goal": "get_my_first_job",
            "career_priority": "continuous_learning_and_growth",
        }
        request = self.request_model(body)
        with patch.object(recommendations, "get_profile", return_value=self.profile), \
             patch.object(recommendations, "score_career_recommendations", return_value=self.response_payload) as scorer:
            self.call_endpoint(preferences=request)
        scorer.assert_called_once_with(self.profile, body)

    def test_authenticated_user_dependency_is_wired_to_route(self):
        calls = {dependency.call for dependency in self.recommendation_route().dependant.dependencies}
        self.assertIn(get_current_user, calls)
        self.assertIn(get_db, calls)

    def test_missing_profile_raises_existing_not_found_response(self):
        with patch.object(recommendations, "get_profile", return_value=None) as get_profile, \
             patch.object(recommendations, "score_career_recommendations") as scorer:
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint()
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "Profile not found.")
        get_profile.assert_called_once_with(self.db, self.user_id)
        scorer.assert_not_called()

    def test_invalid_preferences_map_to_safe_422(self):
        request = self.request_model()
        with patch.object(recommendations, "get_profile", return_value=self.profile), \
             patch.object(recommendations, "score_career_recommendations", side_effect=InvalidPreferenceError("catalogue detail")):
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint(preferences=request)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail, "One or more career preference selections are invalid.")

    def test_unknown_preference_ids_or_labels_are_rejected_by_scoring_service(self):
        for field, value in (
            ("career_interest", "invented_interest"),
            ("preferred_work_activity", "invented activity"),
            ("career_goal", "invented goal"),
            ("career_priority", "invented priority"),
        ):
            with self.subTest(field=field):
                request = self.request_model({**self.preferences, field: value})
                with patch.object(recommendations, "get_profile", return_value=self.profile):
                    with self.assertRaises(HTTPException) as raised:
                        self.call_endpoint(preferences=request)
                self.assertEqual(raised.exception.status_code, 422)
                self.assertEqual(raised.exception.detail, "One or more career preference selections are invalid.")

    def test_missing_preference_fields_and_client_profile_fields_are_rejected(self):
        incomplete = dict(self.preferences)
        incomplete.pop("career_goal")
        with self.assertRaises(ValidationError):
            self.request_model(incomplete)

        for extra in (
            {"user_id": str(uuid4())},
            {"profile_id": str(uuid4())},
            {"profile": {"careerSkills": {"technicalSkills": ["Injected"]}}},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                self.request_model({**self.preferences, **extra})

    def test_endpoint_schema_is_required_and_forbids_extra_identity_fields(self):
        schema = self.app.openapi()["components"]["schemas"]["RecommendationRequest"]
        self.assertEqual(
            set(schema["required"]),
            {"careerInterest", "preferredWorkActivity", "careerGoal", "careerPriority"},
        )
        self.assertFalse(schema["additionalProperties"])

    def test_scoring_exceptions_return_safe_internal_error(self):
        with patch.object(recommendations, "get_profile", return_value=self.profile), \
             patch.object(recommendations, "score_career_recommendations", side_effect=RecommendationDataIntegrityError("private scorer traceback")):
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint()
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Career recommendations could not be calculated.")
        self.assertNotIn("private scorer traceback", str(raised.exception.detail))

    def test_preferences_are_request_scoped_and_not_persisted(self):
        with patch.object(recommendations, "get_profile", return_value=self.profile) as get_profile, \
             patch.object(recommendations, "score_career_recommendations", return_value=self.response_payload) as scorer:
            self.call_endpoint()
        get_profile.assert_called_once()
        scorer.assert_called_once_with(self.profile, self.preferences)
        self.assertFalse(hasattr(recommendations, "save_profile"))

    def test_csrf_dependency_is_required_by_the_post_route(self):
        calls = {dependency.call for dependency in self.recommendation_route().dependant.dependencies}
        self.assertIn(require_csrf_protection, calls)
        self.assertEqual(self.recommendation_route().methods, {"POST"})

    def test_unauthenticated_dependency_is_present_before_profile_or_scoring_access(self):
        route = self.recommendation_route()
        calls = {dependency.call for dependency in route.dependant.dependencies}
        self.assertIn(get_current_user, calls)
        self.assertIs(route.endpoint, recommendations.create_recommendations)
        with patch.object(recommendations, "get_profile") as get_profile, \
             patch.object(recommendations, "score_career_recommendations") as scorer:
            # The route requires an authenticated user argument supplied by the registered dependency.
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint(current_user=AuthenticatedUser(id="not-a-uuid", email="user@example.test"))
        self.assertEqual(raised.exception.status_code, 401)
        get_profile.assert_not_called()
        scorer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
