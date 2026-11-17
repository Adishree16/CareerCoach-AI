"""Offline tests for combined deterministic career recommendation scoring."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from app.services import career_recommendations as recommendations
from app.services.career_intelligence import load_career_roles
from app.services.interest_alignment import load_interest_catalogues


class CareerRecommendationScoringTests(unittest.TestCase):
    def setUp(self):
        self.interests, self.activities, self.knowledge_base = load_interest_catalogues()
        self.roles = load_career_roles()
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
            "career_interest": "software_development",
            "preferred_work_activity": "building_applications_and_software",
            "career_goal": "get_my_first_job",
            "career_priority": "continuous_learning_and_growth",
        }
        self.expected = {role["id"]: role["name"] for role in self.roles}

    def skill_output(self):
        return {
            "maximum_points": 43.75,
            "roles_evaluated": len(self.roles),
            "roles": [
                {
                    "role_id": role["id"], "role_name": role["name"], "maximum_points": 43.75,
                    "score": 10.0, "score_available": True, "score_status": "scored",
                    "evidence_coverage_percentage": 25.0,
                }
                for role in self.roles
            ],
        }

    def interest_output(self):
        return {
            "maximum_points": 25.0,
            "roles": [
                {
                    "role_id": role["id"], "role_name": role["name"], "score": 25.0,
                    "maximum_points": 25.0, "score_available": True,
                    "availability_status": "available", "effective_allocation": 25.0,
                    "career_interest_status": "mapped", "career_interest_available": True,
                    "career_interest_original_allocation": 15.0, "career_interest_earned_points": 15.0,
                    "career_interest_effective_allocation": 15.0,
                    "preferred_work_activity_selection_status": "selected",
                    "preferred_work_activity_status": "exact_match", "preferred_work_activity_match_status": "matched",
                    "preferred_work_activity_available": True, "preferred_work_activity_original_allocation": 10.0,
                    "preferred_work_activity_earned_points": 10.0, "preferred_work_activity_effective_allocation": 10.0,
                }
                for role in self.roles
            ],
        }

    @staticmethod
    def set_interest_unavailable(row, *, interest_status="missing", activity_selection="missing", activity_status="unavailable"):
        row.update({
            "score": None, "maximum_points": 25.0, "score_available": False,
            "availability_status": "unavailable", "effective_allocation": 0.0,
            "career_interest_status": interest_status, "career_interest_available": False,
            "career_interest_original_allocation": 15.0, "career_interest_earned_points": 0.0,
            "career_interest_effective_allocation": 0.0,
            "preferred_work_activity_selection_status": activity_selection,
            "preferred_work_activity_status": activity_status,
            "preferred_work_activity_match_status": "unavailable",
            "preferred_work_activity_available": False, "preferred_work_activity_original_allocation": 10.0,
            "preferred_work_activity_earned_points": 0.0, "preferred_work_activity_effective_allocation": 0.0,
        })

    @staticmethod
    def project_result(role, *, score=5.0, applicable=31.25, unavailable=0.0, status="complete"):
        return {
            "role_id": role["id"], "role_name": role["name"], "maximum_points": 31.25,
            "total_points": score, "evaluated_maximum_points": applicable,
            "unavailable_maximum_points": unavailable, "score_status": status,
            "categories": {"projects": {"earned_points": score}},
            "redistribution": {"applied": False, "points_redistributed": 0.0},
        }

    def run_service(self, *, skill=None, interest=None, project_factory=None):
        with (
            patch.object(recommendations.skill_assessment, "score_skill_alignment", return_value=skill or self.skill_output()) as skill_mock,
            patch.object(recommendations.interest_alignment, "score_interest_alignment", return_value=interest or self.interest_output()) as interest_mock,
            patch.object(
                recommendations.career_intelligence,
                "score_projects_experience",
                side_effect=lambda profile, role: project_factory(role) if project_factory else self.project_result(role),
            ) as project_mock,
        ):
            result = recommendations.score_career_recommendations(self.profile, self.preferences)
        return result, (skill_mock, interest_mock, project_mock)

    def test_returns_all_22_roles_once_in_canonical_order_and_calls_each_scorer_correctly(self):
        result, mocks = self.run_service()
        self.assertEqual(result["maximum_score"], 100.0)
        self.assertEqual(result["roles_evaluated"], 22)
        self.assertEqual([row["role_id"] for row in result["roles"]], [role["id"] for role in self.roles])
        self.assertEqual(len({row["role_id"] for row in result["roles"]}), 22)
        self.assertEqual([mock.call_count for mock in mocks], [1, 1, 22])

    @staticmethod
    def ranking_role(role_id, score, maximum, *, available=True, status="available"):
        return {
            "role_id": role_id,
            "final_score": score,
            "maximum_applicable_score": maximum,
            "available": available,
            "status": status,
        }

    def test_ranking_orders_by_score_then_capacity_then_canonical_position(self):
        canonical = [
            self.ranking_role("canonical_first", 10, 50),
            self.ranking_role("higher_capacity", 10, 75),
            self.ranking_role("higher_score", 11, 25),
            self.ranking_role("fourth", 9, 100),
        ]
        original = deepcopy(canonical)
        ranked = recommendations._select_top_recommendation_role_ids(canonical)
        self.assertEqual(ranked, ["higher_score", "higher_capacity", "canonical_first"])
        self.assertEqual(canonical, original)

    def test_ranking_keeps_available_zero_and_excludes_unavailable_or_zero_capacity(self):
        roles = [
            self.ranking_role("available_zero", 0, 25),
            self.ranking_role("null_score", None, 100),
            self.ranking_role("unavailable", 50, 100, available=False, status="unavailable"),
            self.ranking_role("zero_capacity", 0, 0),
            self.ranking_role("invalid_status", 40, 50, status="unavailable"),
            self.ranking_role("infinite_score", float("inf"), 100),
            self.ranking_role("nan_capacity", 10, float("nan")),
            self.ranking_role("string_score", "10", 100),
        ]
        self.assertEqual(
            recommendations._select_top_recommendation_role_ids(roles),
            ["available_zero"],
        )

    def test_ranking_returns_up_to_three_eligible_ids_and_is_repeatable(self):
        roles = [self.ranking_role(f"role_{index}", 22 - index, 100) for index in range(5)]
        original_order = [role["role_id"] for role in roles]
        first = recommendations._select_top_recommendation_role_ids(roles)
        second = recommendations._select_top_recommendation_role_ids(roles)
        self.assertEqual(first, ["role_0", "role_1", "role_2"])
        self.assertEqual(second, first)
        self.assertEqual([role["role_id"] for role in roles], original_order)
        self.assertEqual(recommendations._select_top_recommendation_role_ids(roles[:2]), ["role_0", "role_1"])
        self.assertEqual(recommendations._select_top_recommendation_role_ids(roles[:0]), [])

    def test_actual_service_exposes_top_ids_without_reordering_or_changing_scores(self):
        first = recommendations.score_career_recommendations(self.profile, self.preferences)
        second = recommendations.score_career_recommendations(self.profile, self.preferences)
        canonical_ids = [role["id"] for role in self.roles]
        self.assertEqual([row["role_id"] for row in first["roles"]], canonical_ids)
        self.assertEqual(first["top_recommendation_role_ids"], second["top_recommendation_role_ids"])
        self.assertEqual(len(first["top_recommendation_role_ids"]), 3)
        self.assertEqual(
            [row["final_score"] for row in first["roles"]],
            [row["final_score"] for row in second["roles"]],
        )

    def test_production_scorers_integrate_with_a_synthetic_profile(self):
        result = recommendations.score_career_recommendations(self.profile, self.preferences)
        self.assertEqual(result["roles_evaluated"], 22)
        self.assertEqual([row["role_id"] for row in result["roles"]], [role["id"] for role in self.roles])
        for role in result["roles"]:
            self.assertEqual(
                set(role["components"]),
                {"skill_alignment", "projects_experience", "interest_alignment"},
            )
            if role["final_score"] is not None:
                self.assertLessEqual(role["final_score"], role["maximum_applicable_score"])

    def test_shuffled_component_rows_are_joined_by_role_id(self):
        skills = self.skill_output()
        interests = self.interest_output()
        skills["roles"].reverse()
        interests["roles"] = interests["roles"][::2] + interests["roles"][1::2]
        result, _ = self.run_service(skill=skills, interest=interests)
        by_id = {row["role_id"]: row for row in result["roles"]}
        self.assertEqual(by_id["software_engineer"]["components"]["skill_alignment"]["score"], 10.0)
        self.assertEqual(by_id["software_engineer"]["components"]["interest_alignment"]["score"], 25.0)

    def test_all_available_components_sum_and_have_fixed_allocations(self):
        result, _ = self.run_service()
        row = result["roles"][0]
        self.assertEqual(row["final_score"], 40.0)
        self.assertEqual(row["maximum_applicable_score"], 100.0)
        self.assertEqual(row["status"], "available")
        self.assertEqual(
            {key: value["maximum_points"] for key, value in row["components"].items()},
            {"skill_alignment": 43.75, "projects_experience": 31.25, "interest_alignment": 25.0},
        )

    def test_each_component_can_be_unavailable_independently(self):
        cases = ("skill_alignment", "projects_experience", "interest_alignment")
        for unavailable in cases:
            with self.subTest(component=unavailable):
                skill = self.skill_output()
                interest = self.interest_output()
                project_factory = None
                if unavailable == "skill_alignment":
                    for row in skill["roles"]:
                        row.update(score=None, score_available=False, score_status="unavailable_no_applicable_requirements", evidence_coverage_percentage=None)
                elif unavailable == "interest_alignment":
                    for row in interest["roles"]:
                        self.set_interest_unavailable(row)
                else:
                    project_factory = lambda role: self.project_result(role, score=0.0, applicable=0.0, unavailable=31.25, status="partial")
                result, _ = self.run_service(skill=skill, interest=interest, project_factory=project_factory)
                row = result["roles"][0]
                component = row["components"][unavailable]
                self.assertFalse(component["available"])
                self.assertIsNone(component["score"])
                expected_maximum = 56.25 if unavailable == "skill_alignment" else 68.75 if unavailable == "projects_experience" else 75.0
                self.assertEqual(row["maximum_applicable_score"], expected_maximum)
                self.assertEqual(row["status"], "partially_available")

    def test_multiple_unavailable_components_do_not_create_points_or_capacity(self):
        skill, interest = self.skill_output(), self.interest_output()
        for row in skill["roles"]:
            row.update(score=None, score_available=False, score_status="unavailable_no_applicable_requirements", evidence_coverage_percentage=None)
        for row in interest["roles"]:
            self.set_interest_unavailable(row)
        result, _ = self.run_service(skill=skill, interest=interest)
        row = result["roles"][0]
        self.assertEqual(row["final_score"], 5.0)
        self.assertEqual(row["maximum_applicable_score"], 31.25)
        self.assertEqual(len(row["unavailable_reasons"]), 2)

    def test_all_components_unavailable_returns_null_final_score(self):
        skill, interest = self.skill_output(), self.interest_output()
        for row in skill["roles"]:
            row.update(score=None, score_available=False, score_status="unavailable_no_applicable_requirements", evidence_coverage_percentage=None)
        for row in interest["roles"]:
            self.set_interest_unavailable(row)
        result, _ = self.run_service(
            skill=skill, interest=interest,
            project_factory=lambda role: self.project_result(role, score=0.0, applicable=0.0, unavailable=31.25, status="partial"),
        )
        row = result["roles"][0]
        self.assertIsNone(row["final_score"])
        self.assertEqual(row["maximum_applicable_score"], 0.0)
        self.assertEqual(row["status"], "unavailable")

    def test_available_zero_is_not_confused_with_unavailable(self):
        skill = self.skill_output()
        for row in skill["roles"]:
            row.update(score=0.0, score_available=True, evidence_coverage_percentage=0.0)
        result, _ = self.run_service(skill=skill)
        row = result["roles"][0]
        self.assertEqual(row["components"]["skill_alignment"]["score"], 0.0)
        self.assertTrue(row["components"]["skill_alignment"]["available"])
        self.assertEqual(row["maximum_applicable_score"], 100.0)

    def test_partial_projects_capacity_and_details_are_preserved(self):
        result, _ = self.run_service(
            project_factory=lambda role: self.project_result(role, score=4.0, applicable=18.75, unavailable=12.5, status="partial")
        )
        row = result["roles"][0]
        component = row["components"]["projects_experience"]
        self.assertEqual(component["score"], 4.0)
        self.assertEqual(component["maximum_points"], 31.25)
        self.assertEqual(component["maximum_applicable_points"], 18.75)
        self.assertEqual(component["details"]["unavailable_maximum_points"], 12.5)
        self.assertEqual(component["details"]["redistribution"]["applied"], False)
        self.assertEqual(row["maximum_applicable_score"], 87.5)
        self.assertEqual(row["status"], "partially_available")

    def test_goal_and_priority_are_context_only_and_do_not_change_scores(self):
        alternate_preferences = {**self.preferences, "career_goal": "Secure an internship", "career_priority": "Work-life balance"}
        with patch.object(recommendations.goals_preferences, "score_goals_preferences", side_effect=AssertionError("goals scorer must remain contextual")):
            first = recommendations.score_career_recommendations(self.profile, self.preferences)
            second = recommendations.score_career_recommendations(self.profile, alternate_preferences)
        self.assertEqual(
            [item["final_score"] for item in first["roles"]],
            [item["final_score"] for item in second["roles"]],
        )
        self.assertNotEqual(first["contextual_preferences"], second["contextual_preferences"])
        self.assertEqual(first["top_recommendation_role_ids"], second["top_recommendation_role_ids"])
        self.assertEqual(set(first["roles"][0]["components"]), {
            "skill_alignment", "projects_experience", "interest_alignment",
        })

    def test_skill_coverage_is_preserved_and_not_used_to_change_score(self):
        result, _ = self.run_service()
        component = result["roles"][0]["components"]["skill_alignment"]
        self.assertEqual(component["score"], 10.0)
        self.assertEqual(component["details"]["evidence_coverage_percentage"], 25.0)

    def test_no_cross_component_redistribution_when_a_factor_is_unavailable(self):
        interest = self.interest_output()
        for row in interest["roles"]:
            self.set_interest_unavailable(row)
        result, _ = self.run_service(interest=interest)
        row = result["roles"][0]
        self.assertEqual(row["maximum_applicable_score"], 75.0)
        self.assertEqual(row["final_score"], 15.0)
        self.assertIsNone(row["components"]["interest_alignment"]["score"])

    def test_missing_duplicate_and_unexpected_role_ids_are_rejected(self):
        for mutation in ("missing", "duplicate", "unexpected"):
            with self.subTest(mutation=mutation):
                skill = self.skill_output()
                if mutation == "missing":
                    skill["roles"].pop()
                elif mutation == "duplicate":
                    skill["roles"][-1] = deepcopy(skill["roles"][0])
                else:
                    skill["roles"][0]["role_id"] = "unlisted_role"
                with self.assertRaises(recommendations.RecommendationDataIntegrityError):
                    self.run_service(skill=skill)

    def test_malformed_scores_and_project_role_mismatch_are_rejected(self):
        skill = self.skill_output()
        skill["roles"][0]["score"] = 44.0
        with self.assertRaises(recommendations.RecommendationOutputError):
            self.run_service(skill=skill)
        with self.assertRaises(recommendations.RecommendationDataIntegrityError):
            self.run_service(project_factory=lambda role: {**self.project_result(role), "role_id": "wrong_role"})

    def test_scorer_exceptions_are_not_swallowed(self):
        with patch.object(recommendations.skill_assessment, "score_skill_alignment", side_effect=RuntimeError("fixture failure")):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                recommendations.score_career_recommendations(self.profile, self.preferences)

    def test_invalid_supplied_preferences_are_rejected_but_missing_values_are_supported(self):
        invalid = {**self.preferences, "career_goal": "close enough"}
        with self.assertRaises(recommendations.InvalidPreferenceError):
            recommendations.score_career_recommendations(self.profile, invalid)
        with patch.object(recommendations.skill_assessment, "score_skill_alignment", return_value=self.skill_output()), \
             patch.object(recommendations.interest_alignment, "score_interest_alignment", return_value=self.interest_output()), \
             patch.object(
                 recommendations.career_intelligence,
                 "score_projects_experience",
                 side_effect=lambda profile, role: self.project_result(role),
             ):
            result = recommendations.score_career_recommendations(self.profile, {})
        self.assertEqual(result["roles_evaluated"], 22)
        self.assertEqual(set(result["roles"][0]["components"]), {"skill_alignment", "projects_experience", "interest_alignment"})
        self.assertEqual(result["contextual_preferences"]["career_goal"], {"option_id": None, "label": None})

    def test_interest_available_requires_full_effective_allocation(self):
        interest = self.interest_output()
        interest["roles"][0]["effective_allocation"] = 19.0
        with self.assertRaises(recommendations.RecommendationOutputError):
            self.run_service(interest=interest)

    def test_interest_unavailable_requires_zero_effective_allocation(self):
        interest = self.interest_output()
        self.set_interest_unavailable(interest["roles"][0])
        interest["roles"][0]["effective_allocation"] = 1.0
        with self.assertRaises(recommendations.RecommendationOutputError):
            self.run_service(interest=interest)

    def test_interest_status_must_agree_with_availability(self):
        interest = self.interest_output()
        interest["roles"][0]["availability_status"] = "unavailable"
        with self.assertRaises(recommendations.RecommendationOutputError):
            self.run_service(interest=interest)

    def test_skill_alignment_rejects_unrecognized_or_conflicting_status(self):
        for status in ("unknown", "unavailable_no_applicable_requirements"):
            with self.subTest(status=status):
                skill = self.skill_output()
                skill["roles"][0]["score_status"] = status
                with self.assertRaises(recommendations.RecommendationOutputError):
                    self.run_service(skill=skill)

    def test_projects_experience_rejects_unrecognized_or_conflicting_status(self):
        for status in ("unknown", "partial"):
            with self.subTest(status=status):
                with self.assertRaises(recommendations.RecommendationOutputError):
                    self.run_service(project_factory=lambda role: self.project_result(role, status=status))

    def test_positive_projects_score_with_zero_capacity_is_rejected(self):
        with self.assertRaises(recommendations.RecommendationOutputError):
            self.run_service(
                project_factory=lambda role: self.project_result(role, score=0.0001, applicable=0.0, unavailable=31.25, status="partial")
            )

    def test_component_score_slightly_above_maximum_is_rejected(self):
        interest = self.interest_output()
        interest["roles"][0]["score"] = 25.0001
        with self.assertRaises(recommendations.RecommendationOutputError):
            self.run_service(interest=interest)

    def test_combined_score_slightly_above_maximum_is_rejected(self):
        role = self.roles[0]
        row = {"role_id": role["id"], "role_name": role["name"]}
        over_max_component = {"score": 43.7501, "maximum_points": 43.75, "maximum_applicable_points": 43.7501, "available": True, "status": "scored", "details": {}}
        full_project_component = {"score": 31.25, "maximum_points": 31.25, "maximum_applicable_points": 31.25, "available": True, "status": "complete", "details": {}}
        full_twenty_component = {"score": 25.0, "maximum_points": 25.0, "maximum_applicable_points": 25.0, "available": True, "status": "available", "details": {}}
        with patch.object(recommendations, "_skill_component", return_value=over_max_component), \
             patch.object(recommendations, "_projects_component", return_value=full_project_component), \
             patch.object(recommendations, "_interest_component", return_value=full_twenty_component):
            with self.assertRaises(recommendations.RecommendationOutputError):
                recommendations._combine_role(role, row, self.project_result(role), row)

    def test_unavailable_reasons_distinguish_missing_and_neutral_selections(self):
        missing_result = recommendations.score_career_recommendations(self.profile, {})
        missing_reasons = " ".join(reason["reason"] for reason in missing_result["roles"][0]["unavailable_reasons"])
        self.assertIn("Career interest selection is missing", missing_reasons)
        self.assertIn("Preferred work activity selection is missing", missing_reasons)

        neutral = {"career_interest": "not_sure_yet", "preferred_work_activity": "not_sure_yet"}
        neutral_result = recommendations.score_career_recommendations(self.profile, neutral)
        neutral_reasons = " ".join(reason["reason"] for reason in neutral_result["roles"][0]["unavailable_reasons"])
        self.assertIn("Career interest selection is neutral", neutral_reasons)
        self.assertIn("Preferred work activity selection is neutral", neutral_reasons)

    def test_unmapped_interest_relationship_reason_comes_from_details(self):
        interest = self.interest_output()
        self.set_interest_unavailable(interest["roles"][0], interest_status="unmapped", activity_selection="selected")
        result, _ = self.run_service(interest=interest)
        reasons = " ".join(reason["reason"] for reason in result["roles"][0]["unavailable_reasons"])
        self.assertIn("Career interest selection is unmapped", reasons)
        self.assertIn("No approved work-activity relationship", reasons)

    def test_missing_or_unusable_profile_is_rejected(self):
        for profile in (None, {}, {"careerSkills": {"careerStatus": "unknown", "technicalSkills": []}}):
            with self.subTest(profile=profile), self.assertRaises(recommendations.ProfileRequiredError):
                recommendations.score_career_recommendations(profile, self.preferences)

    def test_result_scores_and_caps_remain_within_bounds(self):
        result, _ = self.run_service()
        self.assertEqual(result["maximum_score"], 100.0)
        for row in result["roles"]:
            self.assertGreaterEqual(row["final_score"], 0.0)
            self.assertLessEqual(row["final_score"], row["maximum_applicable_score"])
            self.assertLessEqual(row["maximum_applicable_score"], 100.0)
            for component in row["components"].values():
                self.assertLessEqual(component["maximum_applicable_points"], component["maximum_points"])
                if component["available"]:
                    self.assertGreaterEqual(component["score"], 0.0)
                    self.assertLessEqual(component["score"], component["maximum_applicable_points"])
                else:
                    self.assertIsNone(component["score"])


if __name__ == "__main__":
    unittest.main()
