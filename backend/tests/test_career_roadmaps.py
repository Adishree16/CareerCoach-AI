"""Tests for deterministic personalized career roadmap generation."""

import unittest
import copy
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute

from app.api.routes import career_roadmaps
from app.api.routes.auth import get_current_user
from app.api.router import api_router
from app.database.session import get_db
from app.schemas.auth import AuthenticatedUser
from app.schemas.career_roadmaps import CareerRoadmapResponse
from app.services.career_roadmaps import InvalidCareerRoleError, build_career_roadmap
from app.services.career_knowledge_graph import load_knowledge_base


def student_profile(*, skills=None, projects=None, internships=None, certifications=None):
    return {
        "careerSkills": {
            "careerStatus": "Student",
            "technicalSkills": list(skills or []),
            "internshipExperience": list(internships or []),
            "professionalExperience": [],
        },
        "additionalInformation": {
            "projects": list(projects or []),
            "certifications": list(certifications or []),
            "achievements": "private achievement text",
        },
    }


class CareerRoadmapServiceTests(unittest.TestCase):
    def setUp(self):
        self.knowledge_base = load_knowledge_base()

    def test_valid_role_returns_canonical_overview_and_projects(self):
        result = build_career_roadmap(student_profile(skills=["Python"]), "software_engineer")
        CareerRoadmapResponse.model_validate(result)
        role = next(item for item in self.knowledge_base["roles"] if item["id"] == "software_engineer")
        self.assertEqual(result["career_overview"]["role_id"], role["id"])
        self.assertEqual(result["career_overview"]["name"], role["name"])
        self.assertEqual(result["career_overview"]["domain"], role["career_domain"])
        self.assertEqual(result["career_overview"]["main_responsibilities"], role["typical_responsibilities"])
        self.assertEqual(result["practice_projects"]["ideas"], role["relevant_project_types"])
        self.assertIn("not projects recorded as completed", result["practice_projects"]["note"])
        self.assertEqual(result["career_growth"]["progression_paths"], role["career_progression_paths"])
        self.assertIn("not a guarantee", result["career_growth"]["note"])

    def test_all_22_canonical_roles_build_schema_valid_roadmaps(self):
        profile = student_profile(skills=["Python"])
        for role in self.knowledge_base["roles"]:
            with self.subTest(role_id=role["id"]):
                result = build_career_roadmap(profile, role["id"])
                validated = CareerRoadmapResponse.model_validate(result)
                self.assertEqual(validated.career_overview.role_id, role["id"])

    def test_invalid_or_unknown_role_is_rejected(self):
        for role_id in ("", "not-a-role", None):
            with self.subTest(role_id=role_id), self.assertRaises(InvalidCareerRoleError):
                build_career_roadmap(student_profile(), role_id)

    def test_existing_skills_projects_and_internships_use_structured_evidence_only(self):
        profile = student_profile(
            skills=["Python"],
            projects=[{
                "name": "Private project title",
                "description": "This description mentions React and Kubernetes.",
                "technologies": "Python",
            }],
            internships=[{
                "role": "Private internship title",
                "company": "Private Company",
                "duration": "2 months",
                "responsibilities": "This text mentions AWS.",
                "technologies": "FastAPI",
            }],
        )
        result = build_career_roadmap(profile, "backend_developer")
        existing = result["your_existing_skills"]
        self.assertIn("Python", [item["name"] for item in existing["demonstrated_technical_skills"]])
        evidence = {item["source_category"]: item for item in existing["structured_project_and_experience"]}
        self.assertIn("projects", evidence)
        self.assertIn("internships", evidence)
        self.assertIn("Python", [item["name"] for item in evidence["projects"]["relevant_skills"]])
        self.assertNotIn("React", [item["name"] for item in evidence["projects"]["relevant_skills"]])
        self.assertNotIn("AWS", [item["name"] for item in evidence["internships"]["relevant_skills"]])
        serialized = str(result)
        self.assertNotIn("Private Company", serialized)
        self.assertNotIn("Private project title", serialized)
        self.assertNotIn("private achievement text", serialized)

    def test_alternative_requirements_preserve_minimum_and_choices(self):
        result = build_career_roadmap(student_profile(), "software_engineer")
        alternatives = result["skills_to_explore"]["alternative_requirements"]
        language_group = next(item for item in alternatives if item["name"] == "Programming language options")
        self.assertEqual(language_group["minimum_options_to_learn"], 1)
        self.assertEqual(language_group["additional_options_to_explore"], 1)
        self.assertGreater(len(language_group["options"]), 1)
        self.assertEqual({item["category_id"] for item in language_group["options"]}, {"programming_languages"})

    def test_complementary_requirements_are_grouped_and_only_unshown_members_listed(self):
        result = build_career_roadmap(student_profile(skills=["HTML"]), "frontend_developer")
        group = next(
            item for item in result["skills_to_explore"]["complementary_requirements"]
            if item["name"] == "HTML and CSS foundations"
        )
        self.assertEqual([item["name"] for item in group["skills_to_explore"]], ["CSS"])

    def test_compatible_stacks_remain_intact_and_role_minimum_is_respected(self):
        result = build_career_roadmap(student_profile(skills=["Python"]), "backend_developer")
        requirements = result["skills_to_explore"]["compatible_stack_requirements"]
        self.assertEqual(len(requirements), 1)
        requirement = requirements[0]
        self.assertEqual(requirement["minimum_options_to_learn"], 1)
        self.assertEqual(requirement["additional_options_to_explore"], 1)
        python_stacks = [item for item in requirement["options"] if item["name"].startswith("Python with")]
        self.assertEqual(len(python_stacks), 2)
        for stack in python_stacks:
            self.assertTrue(all(item["name"] != "Python" for item in stack["skills_to_explore"]))
            self.assertEqual([item["name"] for item in stack["skills_already_demonstrated"]], ["Python"])
        self.assertTrue(all(" with " in item["name"] for item in requirement["options"]))

    def test_conditional_requirements_are_not_presented_as_universal_suggestions(self):
        profile = student_profile(
            skills=["HashiCorp Vault"],
            projects=[{"name": "Project", "description": "", "technologies": "Cloud Key Management Service"}],
        )
        result = build_career_roadmap(profile, "security_engineer")
        suggestions = result["skills_to_explore"]
        all_skill_ids = {
            item["skill_id"]
            for group in suggestions["individual_requirements"]
            for item in group["skills"]
        }
        all_skill_ids.update(
            item["skill_id"]
            for group in suggestions["complementary_requirements"]
            for item in group["skills_to_explore"]
        )
        conditional = next(
            group for group in next(item for item in self.knowledge_base["roles"] if item["id"] == "security_engineer")["skill_groups"]
            if group["id"] == "secrets_and_key_management"
        )
        self.assertFalse(all_skill_ids.intersection(conditional["skill_ids"]))
        self.assertIn("Conditional topics are omitted", suggestions["note"])
        existing = result["your_existing_skills"]
        displayed_ids = {item["skill_id"] for item in existing["demonstrated_technical_skills"]}
        displayed_ids.update(
            skill["skill_id"]
            for category in existing["structured_project_and_experience"]
            for skill in category["relevant_skills"]
        )
        self.assertFalse(displayed_ids.intersection(conditional["skill_ids"]))

    def test_only_existing_scorer_eligible_certifications_are_returned(self):
        role = next(item for item in self.knowledge_base["roles"] if item["id"] == "software_engineer")
        cert_category = {
            "eligible_credentials": [{
                "name": "Verified Certificate",
                "issuing_organization": "Verified Issuer",
                "credential_type": "industry_certification",
                "mapped_evaluated_skill_ids": ["python"],
            }],
        }
        project_result = {
            "role_id": role["id"],
            "categories": {"certifications": cert_category, "projects": {"matched_role_skills": []}, "internships": {"matched_role_skills": []}},
        }
        with patch("app.services.career_roadmaps.career_intelligence.score_projects_experience", return_value=project_result):
            result = build_career_roadmap(student_profile(), role["id"])
        certifications = result["your_existing_skills"]["eligible_certifications"]
        self.assertEqual(len(certifications), 1)
        self.assertEqual(certifications[0]["name"], "Verified Certificate")
        self.assertEqual(certifications[0]["relevant_skills"][0]["name"], "Python")

    def test_catalogue_unavailability_is_not_silently_replaced(self):
        with patch("app.services.career_roadmaps.load_knowledge_base", side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                build_career_roadmap(student_profile(), "software_engineer")


class CareerRoadmapApiTests(unittest.TestCase):
    def setUp(self):
        self.user_id = uuid4()
        self.db = object()
        self.current_user = AuthenticatedUser(id=str(self.user_id), email="user@example.test")
        self.profile = student_profile()

    def call_endpoint(self, role_id="software_engineer", current_user=None):
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        coroutine = career_roadmaps.read_career_roadmap(
            role_id,
            self.current_user if current_user is None else current_user,
            self.db,
        )
        with patch.object(career_roadmaps, "run_in_threadpool", side_effect=immediate):
            try:
                coroutine.send(None)
            except StopIteration as completed:
                return completed.value
        raise AssertionError("The mocked roadmap endpoint unexpectedly performed asynchronous I/O.")

    def test_route_uses_only_authenticated_user_for_profile_ownership(self):
        roadmap = {"career_overview": {"role_id": "software_engineer"}}
        with patch.object(career_roadmaps, "get_profile", return_value=self.profile) as get_profile, \
             patch.object(career_roadmaps, "build_career_roadmap", return_value=roadmap) as builder:
            self.assertEqual(self.call_endpoint(), roadmap)
        get_profile.assert_called_once_with(self.db, self.user_id)
        builder.assert_called_once_with(self.profile, "software_engineer")

    def test_route_adds_validated_enrichment_without_mutating_deterministic_roadmap(self):
        roadmap = build_career_roadmap(self.profile, "software_engineer")
        original = copy.deepcopy(roadmap)
        gap = next(
            skill
            for group in roadmap["skills_to_explore"]["individual_requirements"]
            for skill in group["skills"]
        )

        async def enrich(_db, user_id, deterministic):
            self.assertEqual(user_id, self.user_id)
            self.assertEqual(deterministic, original)
            return {
                "available": True,
                "provider": "must_not_be_returned",
                "content": {
                    "learning_guidance": [{
                        "text": f"Explore {gap['name']} as one topic for this role.",
                        "skill_ids": [gap["skill_id"]],
                    }],
                },
            }

        with patch.object(career_roadmaps, "get_profile", return_value=self.profile), \
             patch.object(career_roadmaps, "build_career_roadmap", return_value=roadmap), \
             patch.object(career_roadmaps.career_enrichment_service, "enrich_roadmap", side_effect=enrich) as enrichment:
            response = self.call_endpoint()

        self.assertEqual({key: value for key, value in response.items() if key != "ai_enrichment"}, original)
        self.assertEqual(response["ai_enrichment"]["available"], True)
        self.assertEqual(response["ai_enrichment"]["content"]["learning_guidance"][0]["skill_ids"], [gap["skill_id"]])
        self.assertNotIn("provider", response["ai_enrichment"])
        enrichment.assert_awaited_once_with(self.db, self.user_id, roadmap)
        validated = CareerRoadmapResponse.model_validate(response)
        self.assertEqual(validated.career_overview.role_id, "software_engineer")

    def test_route_omits_unavailable_or_invalid_optional_enrichment(self):
        roadmap = build_career_roadmap(self.profile, "software_engineer")
        outcomes = (
            {"available": False, "reason": "consent_not_current"},
            {"available": True, "content": {"learning_guidance": [{"text": "x"}]}},
            RuntimeError("private provider error"),
        )
        for outcome in outcomes:
            with self.subTest(outcome=type(outcome).__name__), \
                 patch.object(career_roadmaps, "get_profile", return_value=self.profile), \
                 patch.object(career_roadmaps, "build_career_roadmap", return_value=roadmap), \
                 patch.object(career_roadmaps.career_enrichment_service, "enrich_roadmap", side_effect=outcome):
                response = self.call_endpoint()
            self.assertEqual(response, roadmap)
            serialized = CareerRoadmapResponse.model_validate(response).model_dump(exclude_unset=True)
            self.assertNotIn("ai_enrichment", serialized)

    def test_route_diagnostics_report_validation_and_fallback_without_exception_details(self):
        roadmap = build_career_roadmap(self.profile, "software_engineer")
        private_error = "PRIVATE-ERROR-TEXT"
        with patch.object(career_roadmaps, "get_profile", return_value=self.profile), \
             patch.object(career_roadmaps, "build_career_roadmap", return_value=roadmap), \
             patch.object(career_roadmaps.career_enrichment_service, "enrich_roadmap", side_effect=RuntimeError(private_error)), \
             self.assertLogs("app.services.career_enrichment", level="INFO") as captured:
            response = self.call_endpoint()

        self.assertEqual(response, roadmap)
        output = "\n".join(captured.output)
        self.assertIn("career_roadmap_ai.final_route_validation.failed", output)
        self.assertIn("career_roadmap_ai.response_assembly.deterministic_fallback_returned", output)
        self.assertNotIn(private_error, output)

    def test_route_diagnostics_confirm_validated_enrichment_attachment(self):
        roadmap = build_career_roadmap(self.profile, "software_engineer")
        gap = next(
            skill
            for group in roadmap["skills_to_explore"]["individual_requirements"]
            for skill in group["skills"]
        )

        async def enrich(_db, _user_id, _roadmap):
            return {
                "available": True,
                "content": {"learning_guidance": [{
                    "text": f"Explore {gap['name']} as one topic for this role.",
                    "skill_ids": [gap["skill_id"]],
                }]},
            }

        with patch.object(career_roadmaps, "get_profile", return_value=self.profile), \
             patch.object(career_roadmaps, "build_career_roadmap", return_value=roadmap), \
             patch.object(career_roadmaps.career_enrichment_service, "enrich_roadmap", side_effect=enrich), \
             self.assertLogs("app.services.career_enrichment", level="INFO") as captured:
            response = self.call_endpoint()

        self.assertIn("ai_enrichment", response)
        output = "\n".join(captured.output)
        self.assertIn("career_roadmap_ai.final_route_validation.passed", output)
        self.assertIn("career_roadmap_ai.response_assembly.ai_enrichment_attached", output)

    def test_missing_profile_returns_safe_404(self):
        with patch.object(career_roadmaps, "get_profile", return_value=None), \
             patch.object(career_roadmaps, "build_career_roadmap") as builder:
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint()
        self.assertEqual(raised.exception.status_code, 404)
        builder.assert_not_called()

    def test_invalid_authenticated_identity_returns_401_without_profile_lookup(self):
        user = AuthenticatedUser(id="not-a-uuid", email="ignored@example.test")
        with patch.object(career_roadmaps, "get_profile") as get_profile:
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint(current_user=user)
        self.assertEqual(raised.exception.status_code, 401)
        get_profile.assert_not_called()

    def test_invalid_role_returns_404_and_internal_errors_are_sanitized(self):
        with patch.object(career_roadmaps, "get_profile", return_value=self.profile), \
             patch.object(career_roadmaps, "build_career_roadmap", side_effect=InvalidCareerRoleError):
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint("unknown_role")
        self.assertEqual(raised.exception.status_code, 404)

        with patch.object(career_roadmaps, "get_profile", return_value=self.profile), \
             patch.object(career_roadmaps, "build_career_roadmap", side_effect=RuntimeError("private data")):
            with self.assertRaises(HTTPException) as raised:
                self.call_endpoint()
        self.assertEqual(raised.exception.status_code, 500)
        self.assertNotIn("private data", str(raised.exception.detail))

    def test_route_is_authenticated_read_only_and_registered(self):
        route = next(item for item in career_roadmaps.router.routes if isinstance(item, APIRoute))
        self.assertEqual(route.methods, {"GET"})
        self.assertEqual(route.path, "/career-roadmaps/{role_id}")
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        self.assertIn(get_current_user, dependencies)
        self.assertIn(get_db, dependencies)
        self.assertFalse(route.dependant.body_params)
        self.assertEqual([item.name for item in route.dependant.path_params], ["role_id"])

        app = FastAPI()
        app.include_router(api_router, prefix="/api")
        self.assertIn("/api/career-roadmaps/{role_id}", app.openapi()["paths"])


if __name__ == "__main__":
    unittest.main()
