"""Offline tests for reviewed Goals & Preferences scoring."""

from copy import deepcopy
import unittest

from app.services.goals_preferences import (
    CAREER_GOAL_ALLOCATION,
    CAREER_PRIORITY_ALLOCATION,
    GOALS_PREFERENCES_MAX_POINTS,
    load_goals_preferences_data,
    score_goals_preferences,
)


class GoalsPreferencesScoringTests(unittest.TestCase):
    def setUp(self):
        self.goals, self.priorities, self.knowledge_base = load_goals_preferences_data()

    def score(self, goal=None, priority=None, *, goals=None, priorities=None, knowledge_base=None, extra=None):
        preferences = {"career_goal": goal, "career_priority": priority}
        if extra:
            preferences.update(extra)
        return score_goals_preferences(
            preferences,
            career_goal_catalogue=goals if goals is not None else self.goals,
            career_priority_catalogue=priorities if priorities is not None else self.priorities,
            knowledge_base=knowledge_base if knowledge_base is not None else self.knowledge_base,
        )

    def approve(self, catalogue, option_id, role_id, status, weight=None):
        option = next(item for item in catalogue["options"] if item["id"] == option_id)
        relationship = next(item for item in option["relationships"] if item["role_id"] == role_id)
        relationship.update({
            "status": status,
            "review_status": "approved",
            "partial_match_weight": weight,
            "evidence_references": [{
                "source_id": "synthetic-test-evidence",
                "reference": "https://example.test/reviewed-evidence",
                "description": "Synthetic fixture used only to exercise reviewed scoring.",
            }],
            "reviewer_rationale": "Synthetic test-only approval rationale.",
            "reviewer_identity": "test-reviewer",
            "reviewed_at": "2026-11-16T12:00:00+00:00",
        })
        return relationship

    def role_result(self, result, role_id="software_engineer"):
        return next(item for item in result["roles"] if item["role_id"] == role_id)

    def test_production_catalogues_have_exact_options_and_unavailable_role_rows(self):
        self.assertEqual(
            [item["label"] for item in self.goals["options"]],
            [
                "Get my first job",
                "Secure an internship",
                "Prepare for campus placements",
                "Transition to a different career",
                "Grow in my current career",
                "Specialise in a particular technology",
                "Move into a leadership role",
                "Explore suitable career options",
                "Return to the workforce",
            ],
        )
        self.assertEqual(
            [item["label"] for item in self.priorities["options"]],
            [
                "Continuous learning and growth",
                "Career stability and job security",
                "Work-life balance",
                "Innovation and creativity",
                "Independence and flexibility",
                "Leadership and recognition",
                "Meaningful impact",
                "Competitive compensation",
                "Collaboration and teamwork",
                "Not Sure Yet",
            ],
        )
        self.assertEqual(self.goals["component"]["original_allocation"], 12)
        self.assertEqual(self.priorities["component"]["original_allocation"], 8)
        self.assertEqual(len(self.goals["options"]), 9)
        self.assertEqual(len(self.priorities["options"]), 10)
        self.assertTrue(all(len(item["relationships"]) == 22 for item in self.goals["options"]))
        self.assertTrue(all(len(item["relationships"]) == 22 for item in self.priorities["options"]))
        self.assertTrue(all(
            row["status"] == ("neutral" if option["neutral"] else "unavailable")
            for catalogue in (self.goals, self.priorities)
            for option in catalogue["options"]
            for row in option["relationships"]
        ))

    def test_both_components_unavailable_for_all_twenty_two_roles(self):
        result = self.score("Get my first job", "Continuous learning and growth")
        self.assertEqual(result["roles_evaluated"], 22)
        self.assertTrue(all(not role["factor_available"] for role in result["roles"]))
        self.assertTrue(all(role["score"] is None for role in result["roles"]))

    def test_neutral_career_goal_is_unavailable_not_a_mismatch(self):
        result = self.score("Explore suitable career options", None)
        role = self.role_result(result)
        self.assertEqual(role["components"]["career_goal"]["relationship_status"], "neutral")
        self.assertFalse(role["factor_available"])
        self.assertIsNone(role["score"])

    def test_neutral_career_priority_is_unavailable_not_a_mismatch(self):
        result = self.score(None, "Not Sure Yet")
        role = self.role_result(result)
        self.assertEqual(role["components"]["career_priority"]["relationship_status"], "neutral")
        self.assertFalse(role["factor_available"])
        self.assertIsNone(role["score"])

    def test_missing_career_goal_is_unavailable(self):
        role = self.role_result(self.score(None, "Not Sure Yet"))
        self.assertEqual(role["components"]["career_goal"]["catalogue_relationship_status"], "missing")
        self.assertIsNone(role["components"]["career_goal"]["score"])

    def test_missing_career_priority_is_unavailable(self):
        role = self.role_result(self.score("Explore suitable career options", None))
        self.assertEqual(role["components"]["career_priority"]["catalogue_relationship_status"], "missing")
        self.assertIsNone(role["components"]["career_priority"]["score"])

    def test_exact_career_goal_match_uses_twenty_when_priority_is_unavailable(self):
        goals = deepcopy(self.goals)
        self.approve(goals, "get_my_first_job", "software_engineer", "exact_match")
        role = self.role_result(self.score("get_my_first_job", None, goals=goals))
        goal = role["components"]["career_goal"]
        self.assertEqual(goal["score"], 20)
        self.assertEqual(goal["effective_allocation"], 20)
        self.assertEqual(role["score"], 20)

    def test_exact_career_priority_match_uses_twenty_when_goal_is_unavailable(self):
        priorities = deepcopy(self.priorities)
        self.approve(priorities, "continuous_learning_and_growth", "software_engineer", "exact_match")
        role = self.role_result(self.score(None, "continuous_learning_and_growth", priorities=priorities))
        priority = role["components"]["career_priority"]
        self.assertEqual(priority["score"], 20)
        self.assertEqual(priority["effective_allocation"], 20)
        self.assertEqual(role["score"], 20)

    def test_partial_career_goal_match_uses_approved_weight(self):
        goals = deepcopy(self.goals)
        self.approve(goals, "get_my_first_job", "software_engineer", "partial_match", 0.4)
        role = self.role_result(self.score("get_my_first_job", None, goals=goals))
        self.assertEqual(role["components"]["career_goal"]["score"], 8)
        self.assertEqual(role["score"], 8)

    def test_partial_career_priority_match_uses_approved_weight(self):
        priorities = deepcopy(self.priorities)
        self.approve(priorities, "continuous_learning_and_growth", "software_engineer", "partial_match", 0.25)
        role = self.role_result(self.score(None, "continuous_learning_and_growth", priorities=priorities))
        self.assertEqual(role["components"]["career_priority"]["score"], 5)
        self.assertEqual(role["score"], 5)

    def test_invalid_or_missing_partial_weights_are_rejected(self):
        for weight in (None, 0, 1, -0.1, "0.5", True, float("inf"), float("nan")):
            with self.subTest(weight=weight):
                goals = deepcopy(self.goals)
                self.approve(goals, "get_my_first_job", "software_engineer", "partial_match", weight)
                with self.assertRaises(ValueError):
                    self.score("get_my_first_job", None, goals=goals)

    def test_unreviewed_partial_relationship_does_not_award_points(self):
        goals = deepcopy(self.goals)
        relationship = next(
            row for row in next(item for item in goals["options"] if item["id"] == "get_my_first_job")["relationships"]
            if row["role_id"] == "software_engineer"
        )
        relationship.update({"status": "partial_match", "partial_match_weight": 0.5})
        role = self.role_result(self.score("get_my_first_job", None, goals=goals))
        component = role["components"]["career_goal"]
        self.assertEqual(component["catalogue_relationship_status"], "partial_match")
        self.assertEqual(component["relationship_status"], "unavailable")
        self.assertIsNone(component["score"])
        self.assertFalse(role["factor_available"])

    def test_confirmed_mismatch_is_available_zero_and_does_not_trigger_redistribution(self):
        goals, priorities = deepcopy(self.goals), deepcopy(self.priorities)
        self.approve(goals, "get_my_first_job", "software_engineer", "confirmed_mismatch")
        self.approve(priorities, "continuous_learning_and_growth", "software_engineer", "exact_match")
        role = self.role_result(self.score("get_my_first_job", "continuous_learning_and_growth", goals=goals, priorities=priorities))
        self.assertTrue(role["factor_available"])
        self.assertEqual(role["components"]["career_goal"]["score"], 0)
        self.assertEqual(role["components"]["career_goal"]["effective_allocation"], 12)
        self.assertEqual(role["score"], 8)
        self.assertFalse(role["redistribution"]["applied"])

    def test_unavailable_priority_redistributes_eight_points_to_goal_once(self):
        goals = deepcopy(self.goals)
        self.approve(goals, "get_my_first_job", "software_engineer", "exact_match")
        role = self.role_result(self.score("get_my_first_job", "Continuous learning and growth", goals=goals))
        self.assertEqual(role["effective_allocations"], {"career_goal": 20, "career_priority": 0})
        self.assertEqual(role["redistributed_points"], {"career_goal": 8, "career_priority": 0})
        self.assertEqual(role["redistribution"]["points_redistributed"], 8)
        self.assertEqual(role["score"], 20)

    def test_unavailable_goal_redistributes_twelve_points_to_priority_once(self):
        priorities = deepcopy(self.priorities)
        self.approve(priorities, "continuous_learning_and_growth", "software_engineer", "exact_match")
        role = self.role_result(self.score("Get my first job", "continuous_learning_and_growth", priorities=priorities))
        self.assertEqual(role["effective_allocations"], {"career_goal": 0, "career_priority": 20})
        self.assertEqual(role["redistributed_points"], {"career_goal": 0, "career_priority": 12})
        self.assertEqual(role["redistribution"]["points_redistributed"], 12)
        self.assertEqual(role["score"], 20)

    def test_both_available_components_keep_original_twelve_eight_allocations(self):
        goals, priorities = deepcopy(self.goals), deepcopy(self.priorities)
        self.approve(goals, "get_my_first_job", "software_engineer", "exact_match")
        self.approve(priorities, "continuous_learning_and_growth", "software_engineer", "partial_match", 0.5)
        role = self.role_result(self.score("get_my_first_job", "continuous_learning_and_growth", goals=goals, priorities=priorities))
        self.assertEqual(role["effective_allocations"], {"career_goal": 12, "career_priority": 8})
        self.assertEqual(role["redistribution"]["points_redistributed"], 0)
        self.assertEqual(role["score"], 16)

    def test_both_unavailable_components_return_null_factor_score(self):
        role = self.role_result(self.score("Get my first job", "Continuous learning and growth"))
        self.assertFalse(role["factor_available"])
        self.assertIsNone(role["score"])
        self.assertEqual(role["effective_allocations"], {"career_goal": 0, "career_priority": 0})

    def test_redistribution_occurs_once_and_never_exceeds_twenty(self):
        goals = deepcopy(self.goals)
        self.approve(goals, "get_my_first_job", "software_engineer", "exact_match")
        role = self.role_result(self.score("Get my first job", None, goals=goals))
        self.assertEqual(sum(role["effective_allocations"].values()), 20)
        self.assertEqual(role["score"], 20)
        self.assertLessEqual(role["score"], GOALS_PREFERENCES_MAX_POINTS)

    def test_unknown_preference_labels_are_rejected_without_fuzzy_matching(self):
        with self.assertRaises(ValueError):
            self.score("Get my first jbo", "Not Sure Yet")
        with self.assertRaises(ValueError):
            self.score("Explore suitable career options", "Better compensation")

    def test_stable_ids_and_exact_labels_resolve_consistently(self):
        by_label = self.score("Get my first job", "Not Sure Yet")
        by_id = self.score("get_my_first_job", "not_sure_yet")
        self.assertEqual(by_label, by_id)

    def test_duplicate_or_conflicting_relationships_are_rejected(self):
        for conflicting in (False, True):
            with self.subTest(conflicting=conflicting):
                goals = deepcopy(self.goals)
                option = next(item for item in goals["options"] if item["id"] == "get_my_first_job")
                duplicate = deepcopy(option["relationships"][0])
                if conflicting:
                    duplicate["status"] = "confirmed_mismatch"
                option["relationships"].append(duplicate)
                with self.assertRaises(ValueError):
                    self.score("Get my first job", None, goals=goals)

    def test_duplicate_option_ids_or_labels_are_rejected(self):
        for duplicate_field in ("id", "label"):
            with self.subTest(duplicate_field=duplicate_field):
                goals = deepcopy(self.goals)
                duplicate = deepcopy(goals["options"][0])
                duplicate["id"] = "different_id" if duplicate_field == "label" else duplicate["id"]
                duplicate["label"] = "Different label" if duplicate_field == "id" else duplicate["label"]
                goals["options"].append(duplicate)
                with self.assertRaises(ValueError):
                    self.score("Get my first job", None, goals=goals)

    def test_all_twenty_two_roles_are_returned_in_stable_order(self):
        first = self.score("Get my first job", "Continuous learning and growth")
        second = self.score("Get my first job", "Continuous learning and growth")
        self.assertEqual(first, second)
        self.assertEqual(first["roles_evaluated"], 22)
        self.assertEqual(
            [(item["role_id"], item["role_name"]) for item in first["roles"]],
            [(item["id"], item["name"]) for item in self.knowledge_base["roles"]],
        )

    def test_contextual_preferences_are_separate_and_do_not_add_points(self):
        goals = deepcopy(self.goals)
        self.approve(goals, "get_my_first_job", "software_engineer", "exact_match")
        baseline = self.score("Get my first job", None, goals=goals)
        with_context = self.score(
            "Get my first job", None, goals=goals,
            extra={"career_status": "Student", "career_priority": None, "unrelated_context": "anything"},
        )
        first = self.role_result(baseline)
        second = self.role_result(with_context)
        self.assertEqual(first["score"], second["score"])
        self.assertEqual(second["contextual_preferences"]["career_goal"]["label"], "Get my first job")
        self.assertEqual(second["contextual_preferences"]["career_priority"]["label"], None)

    def test_unreviewed_exact_mapping_never_awards_points(self):
        goals = deepcopy(self.goals)
        relationship = next(
            row for row in next(item for item in goals["options"] if item["id"] == "get_my_first_job")["relationships"]
            if row["role_id"] == "software_engineer"
        )
        relationship["status"] = "exact_match"
        role = self.role_result(self.score("Get my first job", None, goals=goals))
        self.assertEqual(role["components"]["career_goal"]["relationship_status"], "unavailable")
        self.assertIsNone(role["components"]["career_goal"]["score"])

    def test_missing_relationship_record_resolves_to_unavailable(self):
        goals = deepcopy(self.goals)
        option = next(item for item in goals["options"] if item["id"] == "get_my_first_job")
        option["relationships"] = [row for row in option["relationships"] if row["role_id"] != "software_engineer"]
        role = self.role_result(self.score("Get my first job", None, goals=goals))
        self.assertEqual(role["components"]["career_goal"]["relationship_status"], "unavailable")
        self.assertEqual(role["components"]["career_goal"]["catalogue_relationship_status"], "missing")

    def test_approved_relationship_without_evidence_or_review_metadata_is_rejected(self):
        goals = deepcopy(self.goals)
        relationship = self.approve(goals, "get_my_first_job", "software_engineer", "exact_match")
        relationship["evidence_references"] = []
        with self.assertRaises(ValueError):
            self.score("Get my first job", None, goals=goals)

    def test_contextual_inputs_do_not_change_the_fixed_original_allocations(self):
        result = self.score("Get my first job", "Not Sure Yet")
        self.assertEqual(result["original_allocations"], {
            "career_goal": CAREER_GOAL_ALLOCATION,
            "career_priority": CAREER_PRIORITY_ALLOCATION,
            "total": GOALS_PREFERENCES_MAX_POINTS,
        })


if __name__ == "__main__":
    unittest.main()
