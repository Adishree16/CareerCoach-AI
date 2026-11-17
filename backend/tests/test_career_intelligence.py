"""Focused offline tests for the Projects / Experience score component."""

import unittest
from copy import deepcopy
import json
from datetime import datetime
from unittest.mock import patch

from app.services import career_intelligence as career_intelligence
from app.services.career_intelligence import (
    _redistribute_inapplicable_allocations,
    score_projects_experience,
)
from app.services.skill_assessment import score_skill_alignment
from app.services.interest_alignment import (
    load_interest_catalogues,
    score_interest_alignment,
)


REAL_LOAD_CERTIFICATION_CATALOGUE = career_intelligence.load_certification_catalogue


def skill(skill_id, name, importance="core"):
    return {"id": skill_id, "name": name, "importance": importance, "why_relevant": "fixture"}


def simple_role():
    return {
        "id": "fixture_role",
        "name": "Fixture Role",
        "skills": {
            "programming_languages": [skill("python", "Python"), skill("java", "Java")],
            "databases": [skill("postgresql", "PostgreSQL")],
            "tools_and_platforms": [skill("terraform", "Terraform", "optional")],
            "computer_science_fundamentals": [skill("algorithms", "Algorithms")],
            "soft_skills": [skill("communication", "Communication")],
        },
        "skill_groups": [],
    }


def profile(status="Student", *, internships=None, professional=None, projects=None, certifications=None):
    return {
        "careerSkills": {
            "careerStatus": status,
            "technicalSkills": [],
            "internshipExperience": internships or [],
            "professionalExperience": professional or [],
        },
        "additionalInformation": {
            "projects": projects or [],
            "certifications": certifications or [],
            "achievements": None,
        },
    }


def project(technologies="", description=""):
    return {"name": "Example", "technologies": technologies, "description": description}


def experience(technologies="", *, role="Intern", responsibilities=""):
    return {
        "company": "Example Co",
        "role": role,
        "duration": "",
        "responsibilities": responsibilities,
        "technologies": technologies,
    }


def catalogue_record(
    name,
    organization,
    role_mappings,
    *,
    credential_type="industry_certification",
    platform=None,
    exam_code=None,
    version=None,
    mapping_status="verified",
    scoring_eligible=True,
    record_id="fixture_credential",
):
    return {
        "id": record_id,
        "name": name,
        "issuing_organization": organization,
        "credential_type": credential_type,
        "platform": platform,
        "exam_code": exam_code,
        "version": version,
        "profile_match": {"name": name, "organization": organization},
        "mapping_status": mapping_status,
        "scoring_eligible": scoring_eligible,
        "role_mappings": [
            {
                "role_id": role_id,
                "skill_ids": skill_ids,
                "source_url": "https://issuer.example/official-objectives",
                "source_section": "Official learning objectives",
                "objective_summary": "Fixture verified objective mapping.",
                "verification_date": "2026-11-15",
            }
            for role_id, skill_ids in role_mappings.items()
        ],
    }


def catalogue(*records):
    return {
        "catalogue_name": "Test certification mappings",
        "version": "test",
        "certifications": list(records),
    }


DEFAULT_TEST_CATALOGUE = catalogue(
    catalogue_record(
        "Fixture Role Certificate",
        "Fixture Org",
        {
            "fixture_role": ["python", "java", "postgresql"],
            "alternative_role": ["python", "java", "testing"],
            "stack_role": ["python", "fastapi"],
        },
        record_id="fixture_role_certificate",
    )
)


class ProjectsExperienceScoringTests(unittest.TestCase):
    def setUp(self):
        self.catalogue_patch = patch(
            "app.services.career_intelligence.load_certification_catalogue",
            return_value=DEFAULT_TEST_CATALOGUE,
        )
        self.catalogue_patch.start()
        self.addCleanup(self.catalogue_patch.stop)

    def score_with_catalogue(self, profile_data, role, test_catalogue):
        with patch(
            "app.services.career_intelligence.load_certification_catalogue",
            return_value=test_catalogue,
        ):
            return score_projects_experience(profile_data, role)

    def test_fresher_allocation_and_inapplicable_professional_experience(self):
        result = score_projects_experience(profile("Fresher"), simple_role())
        self.assertEqual(result["candidate_category"], "student_fresher")
        self.assertEqual(result["categories"]["internships"]["maximum_points"], 15.625)
        self.assertEqual(result["categories"]["projects"]["maximum_points"], 10.9375)
        self.assertEqual(result["categories"]["certifications"]["maximum_points"], 4.6875)
        self.assertEqual(result["categories"]["professional_experience"]["status"], "inapplicable")
        self.assertEqual(result["inapplicable_categories"], ["professional_experience"])
        self.assertFalse(result["redistribution"]["applied"])
        self.assertEqual(result["maximum_points"], 31.25)

    def test_professional_allocation(self):
        result = score_projects_experience(profile("Experienced"), simple_role())
        categories = result["categories"]
        self.assertEqual(result["candidate_category"], "professional")
        self.assertEqual(categories["professional_experience"]["maximum_points"], 15.625)
        self.assertEqual(categories["internships"]["maximum_points"], 6.25)
        self.assertEqual(categories["projects"]["maximum_points"], 6.25)
        self.assertEqual(categories["certifications"]["maximum_points"], 3.125)
        self.assertEqual(sum(item["maximum_points"] for item in categories.values()), 31.25)

    def test_zero_partial_and_full_project_matching(self):
        role = simple_role()
        no_match = score_projects_experience(profile(projects=[project("Unlisted tool")]), role)
        partial = score_projects_experience(profile(projects=[project(" python ")]), role)
        full = score_projects_experience(profile(projects=[project("Python, PostgreSQL, Java")]), role)
        self.assertEqual(no_match["categories"]["projects"]["earned_points"], 0)
        self.assertAlmostEqual(partial["categories"]["projects"]["earned_points"], 10.9375 / 3, places=3)
        self.assertEqual(full["categories"]["projects"]["earned_points"], 10.9375)
        self.assertEqual(full["categories"]["projects"]["matched_role_skills"], [
            {"id": "java", "name": "Java"},
            {"id": "postgresql", "name": "PostgreSQL"},
            {"id": "python", "name": "Python"},
        ])

    def test_duplicate_technologies_do_not_inflate_credit(self):
        result = score_projects_experience(
            profile(projects=[project("Python, python, PYTHON, PostgreSQL, PostgreSQL")]),
            simple_role(),
        )
        self.assertAlmostEqual(result["categories"]["projects"]["earned_points"], 10.9375 * 2 / 3, places=3)
        self.assertEqual(len(result["categories"]["projects"]["matched_role_skills"]), 2)

    def test_unrelated_technologies_receive_no_credit(self):
        result = score_projects_experience(profile(projects=[project("React, Kubernetes")]), simple_role())
        category = result["categories"]["projects"]
        self.assertEqual(category["earned_points"], 0)
        self.assertEqual(category["matched_role_skills"], [])
        self.assertEqual(category["unsupported_technologies"], ["Kubernetes", "React"])

    def test_descriptions_responsibilities_titles_and_company_are_not_skill_evidence(self):
        result = score_projects_experience(
            profile(
                internships=[experience("", role="Python PostgreSQL Engineer", responsibilities="Used Python and PostgreSQL")],
                projects=[project("", description="Built with Python, Java, and PostgreSQL")],
            ),
            simple_role(),
        )
        self.assertEqual(result["categories"]["internships"]["earned_points"], 0)
        self.assertEqual(result["categories"]["projects"]["earned_points"], 0)

    def test_evidence_categories_are_scored_separately(self):
        result = score_projects_experience(
            profile(
                "Experienced",
                professional=[experience("Java", role="Software Engineer")],
                internships=[experience("Python")],
                projects=[project("PostgreSQL")],
            ),
            simple_role(),
        )
        self.assertEqual(result["categories"]["professional_experience"]["matched_role_skills"], [
            {"id": "java", "name": "Java"}
        ])
        self.assertEqual(result["categories"]["internships"]["matched_role_skills"], [
            {"id": "python", "name": "Python"}
        ])
        self.assertEqual(result["categories"]["projects"]["matched_role_skills"], [
            {"id": "postgresql", "name": "PostgreSQL"}
        ])
        self.assertAlmostEqual(result["categories"]["professional_experience"]["earned_points"], 15.625 / 3, places=3)
        self.assertAlmostEqual(result["categories"]["internships"]["earned_points"], 6.25 / 3, places=3)
        self.assertAlmostEqual(result["categories"]["projects"]["earned_points"], 6.25 / 3, places=3)

    def test_required_alternatives_are_one_choice_not_all_mandatory(self):
        role = {
            "id": "alternative_role",
            "name": "Alternative Role",
            "skills": {"programming_languages": [skill("python", "Python"), skill("java", "Java")],
                       "core_technical_skills": [skill("testing", "Testing")]},
            "skill_groups": [{
                "id": "language_choice", "relationship": "alternatives", "applicability": "required",
                "minimum_options_to_learn": 1, "skill_ids": ["python", "java"],
            }],
        }
        result = score_projects_experience(profile(projects=[project("Python")]), role)
        self.assertEqual(result["categories"]["projects"]["evaluated_requirement_units"], 2)
        self.assertAlmostEqual(result["categories"]["projects"]["earned_points"], 10.9375 / 2, places=3)

    def test_required_compatible_stack_must_be_together_in_one_evidence_item(self):
        role = {
            "id": "stack_role",
            "name": "Stack Role",
            "skills": {"programming_languages": [skill("python", "Python")],
                       "frameworks_and_libraries": [skill("fastapi", "FastAPI")]},
            "skill_groups": [{
                "id": "python_fastapi", "relationship": "compatible_stack", "applicability": "optional",
                "skill_ids": ["python", "fastapi"],
            }],
            "technology_stack_requirement": {
                "applicability": "required", "relationship": "alternatives", "minimum_options_to_learn": 1,
                "stack_group_ids": ["python_fastapi"],
            },
        }
        split = score_projects_experience(profile(projects=[project("Python"), project("FastAPI")]), role)
        together = score_projects_experience(profile(projects=[project("Python, FastAPI")]), role)
        self.assertEqual(split["categories"]["projects"]["earned_points"], 0)
        self.assertEqual(together["categories"]["projects"]["earned_points"], 10.9375)
        self.assertEqual(together["categories"]["projects"]["evaluated_requirement_units"], 1)

    def test_certification_title_alone_does_not_establish_relevance(self):
        result = score_projects_experience(
            profile(certifications=[{"name": "Python Professional Certificate", "organization": "Example"}]),
            simple_role(),
        )
        certs = result["categories"]["certifications"]
        self.assertEqual(certs["earned_points"], 0)
        self.assertEqual(certs["status"], "no_eligible_credentials")
        self.assertEqual(certs["unmatched_credentials"][0]["reason"], "no_exact_catalogue_identity")
        self.assertTrue(result["certification_mapping_available"])

    def test_explicit_verified_certification_mapping_earns_proportional_credit(self):
        test_catalogue = catalogue(catalogue_record(
            "Verified Python Certificate", "Example", {"fixture_role": ["python"]}
        ))
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Verified Python Certificate", "organization": "Example"}]),
            simple_role(),
            test_catalogue,
        )
        self.assertEqual(result["categories"]["certifications"]["earned_points"], 4.6875 / 3)
        self.assertTrue(result["certification_mapping_available"])

    def test_empty_categories_score_zero_without_redistribution(self):
        result = score_projects_experience(profile(), simple_role())
        self.assertEqual(result["categories"]["internships"]["earned_points"], 0)
        self.assertEqual(result["categories"]["projects"]["earned_points"], 0)
        self.assertEqual(result["categories"]["internships"]["status"], "empty")
        self.assertEqual(result["redistribution"]["points_redistributed"], 0)

    def test_inapplicable_allocations_are_redistributed_once_proportionally(self):
        redistributed = _redistribute_inapplicable_allocations(
            {"inapplicable": 6.0, "projects": 4.0, "internships": 5.0},
            ["inapplicable", "inapplicable"],
        )
        self.assertAlmostEqual(redistributed["projects"], 4 + 6 * 4 / 9)
        self.assertAlmostEqual(redistributed["internships"], 5 + 6 * 5 / 9)
        self.assertAlmostEqual(sum(redistributed.values()), 15)

    def test_score_never_exceeds_31_25_points(self):
        test_catalogue = catalogue(catalogue_record(
            "Mapped", "Example", {"fixture_role": ["python", "java", "postgresql"]}
        ))
        result = self.score_with_catalogue(
            profile(
                "Experienced",
                professional=[experience("Python, Java, PostgreSQL")],
                internships=[experience("Python, Java, PostgreSQL")],
                projects=[project("Python, Java, PostgreSQL")],
                certifications=[{"name": "Mapped", "organization": "Example"}],
            ),
            simple_role(),
            test_catalogue,
        )
        self.assertLessEqual(result["total_points"], 31.25)
        self.assertEqual(result["total_points"], 31.25)

    def test_uses_curated_role_skills_and_exact_normalized_names(self):
        result = score_projects_experience(
            profile(projects=[project("  PYTHON  ")]), simple_role()
        )
        category = result["categories"]["projects"]
        self.assertEqual(category["matched_role_skills"], [{"id": "python", "name": "Python"}])
        self.assertAlmostEqual(category["earned_points"], 10.9375 / 3, places=3)

    def test_non_technology_skill_categories_are_not_mis_scored_as_missing_evidence(self):
        result = score_projects_experience(profile(projects=[project("Python")]), simple_role())
        category = result["categories"]["projects"]
        self.assertEqual(category["evaluated_requirement_units"], 3)
        self.assertNotIn("soft_skills", result["evaluated_role_skill_categories"])
        self.assertNotIn("computer_science_fundamentals", result["evaluated_role_skill_categories"])

    def test_real_catalogue_loads_and_validates_against_existing_role_skills(self):
        self.catalogue_patch.stop()
        self.addCleanup(self.catalogue_patch.start)
        loaded = REAL_LOAD_CERTIFICATION_CATALOGUE()
        records = {item["id"]: item for item in loaded["certifications"]}
        self.assertTrue({
            "aws_solutions_architect_associate_saa_c03",
            "google_cloud_associate_cloud_engineer",
            "iiba_certified_business_analysis_professional_cbap",
            "google_cloud_digital_leader",
            "harvardx_computer_science_for_python_programming_professional_certificate",
        }.issubset(records))
        self.assertEqual(
            {item["mapping_status"] for item in loaded["certifications"]},
            {"verified", "needs_review"},
        )
        for item in loaded["certifications"]:
            self.assertTrue(item["name"].strip())
            self.assertTrue(item["issuing_organization"].strip())
            self.assertEqual(item["profile_match"]["name"], item["name"])
            self.assertEqual(
                item["scoring_eligible"], item["mapping_status"] == "verified"
            )
            self.assertIsInstance(item["role_mappings"], list)

        harvard = records[
            "harvardx_computer_science_for_python_programming_professional_certificate"
        ]
        self.assertEqual(
            harvard["name"], "Computer Science for Python Programming Professional Certificate"
        )
        self.assertEqual(harvard["mapping_status"], "needs_review")
        self.assertFalse(harvard["scoring_eligible"])
        google_ace = records["google_cloud_associate_cloud_engineer"]
        self.assertEqual(
            {item["role_id"] for item in google_ace["role_mappings"]},
            {"cloud_engineer"},
        )

    def test_invalid_catalogue_structure_fails_safely(self):
        from io import StringIO

        class MemoryPath:
            def open(self, **kwargs):
                return StringIO('{"certifications": []}')

        self.catalogue_patch.stop()
        self.addCleanup(self.catalogue_patch.start)
        with patch.object(career_intelligence, "_CERTIFICATION_CATALOGUE_PATH", MemoryPath()):
            with self.assertRaises(ValueError):
                REAL_LOAD_CERTIFICATION_CATALOGUE()

    def test_exact_normalized_name_and_issuer_match(self):
        test_catalogue = catalogue(catalogue_record(
            "Cloud Certificate", "Example Issuer", {"fixture_role": ["python"]}
        ))
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "  CLOUD   CERTIFICATE ", "organization": " example issuer "}]),
            simple_role(),
            test_catalogue,
        )
        certs = result["categories"]["certifications"]
        self.assertEqual(len(certs["eligible_credentials"]), 1)
        self.assertAlmostEqual(certs["earned_points"], 4.6875 / 3)

    def test_same_name_from_different_issuer_does_not_match(self):
        test_catalogue = catalogue(catalogue_record(
            "Shared Certificate", "Official Issuer", {"fixture_role": ["python"]}
        ))
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Shared Certificate", "organization": "Different Issuer"}]),
            simple_role(),
            test_catalogue,
        )
        certs = result["categories"]["certifications"]
        self.assertEqual(certs["earned_points"], 0)
        self.assertEqual(certs["unmatched_credentials"][0]["reason"], "no_exact_catalogue_identity")

    def test_different_credential_types_are_not_conflated(self):
        industry = catalogue_record(
            "Same Name", "Same Issuer", {"fixture_role": ["java"]},
            credential_type="industry_certification", record_id="industry_record",
        )
        course = catalogue_record(
            "Same Name", "Same Issuer", {"fixture_role": ["python"]},
            credential_type="course_completion", platform="OpenLearn",
            record_id="course_record",
        )
        result = self.score_with_catalogue(
            profile(certifications=[{
                "name": "Same Name", "organization": "Same Issuer",
                "credentialType": "course_completion", "platform": "OpenLearn",
            }]),
            simple_role(),
            catalogue(industry, course),
        )
        certs = result["categories"]["certifications"]
        self.assertEqual([item["catalogue_id"] for item in certs["eligible_credentials"]], ["course_record"])
        self.assertEqual(certs["matched_skill_ids"], ["python"])

    def test_exam_code_and_version_disambiguate_catalogue_records(self):
        older = catalogue_record(
            "Versioned Credential", "Versioned Issuer", {"fixture_role": ["python"]},
            exam_code="EX-01", version="v1", record_id="version_one",
        )
        current = catalogue_record(
            "Versioned Credential", "Versioned Issuer", {"fixture_role": ["java"]},
            exam_code="EX-02", version="v2", record_id="version_two",
        )
        result = self.score_with_catalogue(
            profile(certifications=[{
                "name": "Versioned Credential", "organization": "Versioned Issuer",
                "examCode": "EX-02", "version": "v2",
            }]),
            simple_role(),
            catalogue(older, current),
        )
        certs = result["categories"]["certifications"]
        self.assertEqual([item["catalogue_id"] for item in certs["eligible_credentials"]], ["version_two"])
        self.assertEqual(certs["matched_skill_ids"], ["java"])

    def test_legacy_name_and_issuer_match_when_catalogue_identity_is_unambiguous(self):
        test_catalogue = catalogue(catalogue_record(
            "Legacy Certificate", "Example Issuer", {"fixture_role": ["python"]},
            credential_type="professional_certificate", platform="Learning Site",
        ))
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Legacy Certificate", "organization": "Example Issuer"}]),
            simple_role(),
            test_catalogue,
        )
        self.assertEqual(len(result["categories"]["certifications"]["eligible_credentials"]), 1)

    def test_ambiguous_legacy_identity_does_not_receive_points(self):
        test_catalogue = catalogue(
            catalogue_record(
                "Ambiguous Certificate", "Same Issuer", {"fixture_role": ["python"]},
                credential_type="professional_certificate", record_id="professional_record",
            ),
            catalogue_record(
                "Ambiguous Certificate", "Same Issuer", {"fixture_role": ["java"]},
                credential_type="course_completion", platform="Course Site", record_id="course_record",
            ),
        )
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Ambiguous Certificate", "organization": "Same Issuer"}]),
            simple_role(),
            test_catalogue,
        )
        certs = result["categories"]["certifications"]
        self.assertEqual(certs["earned_points"], 0)
        self.assertEqual(len(certs["ambiguous_credentials"]), 1)
        self.assertEqual(certs["ambiguous_credentials"][0]["catalogue_ids"], ["course_record", "professional_record"])

    def test_needs_review_mapping_never_contributes_points(self):
        record = catalogue_record(
            "Pending Certificate", "Example Issuer", {"fixture_role": ["python"]},
            mapping_status="needs_review", scoring_eligible=False,
        )
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Pending Certificate", "organization": "Example Issuer"}]),
            simple_role(),
            catalogue(record),
        )
        certs = result["categories"]["certifications"]
        self.assertIsNone(certs["earned_points"])
        self.assertEqual(certs["eligible_credentials"], [])
        self.assertEqual(len(certs["unverified_credentials"]), 1)

    def test_caller_cannot_bypass_catalogue_verification_with_mapping_argument(self):
        with self.assertRaises(TypeError):
            score_projects_experience(
                profile(certifications=[{"name": "Arbitrary", "organization": "Issuer"}]),
                simple_role(),
                verified_certification_skill_mappings={"Arbitrary": ["python"]},
            )

    def test_role_specific_mapping_is_required(self):
        record = catalogue_record(
            "Other Role Certificate", "Example Issuer", {"different_role": ["python"]}
        )
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Other Role Certificate", "organization": "Example Issuer"}]),
            simple_role(),
            catalogue(record),
        )
        certs = result["categories"]["certifications"]
        self.assertFalse(result["certification_mapping_available"])
        self.assertIsNone(certs["earned_points"])
        self.assertEqual(len(certs["no_role_mapping_credentials"]), 1)

    def test_duplicate_skill_mappings_across_credentials_count_once(self):
        records = catalogue(
            catalogue_record("Certificate A", "Issuer A", {"fixture_role": ["python"]}, record_id="cert_a"),
            catalogue_record("Certificate B", "Issuer B", {"fixture_role": ["python"]}, record_id="cert_b"),
        )
        result = self.score_with_catalogue(
            profile(certifications=[
                {"name": "Certificate A", "organization": "Issuer A"},
                {"name": "Certificate B", "organization": "Issuer B"},
            ]),
            simple_role(),
            records,
        )
        certs = result["categories"]["certifications"]
        self.assertEqual(certs["matched_skill_ids"], ["python"])
        self.assertEqual(len(certs["matched_requirement_units"]), 1)
        self.assertAlmostEqual(certs["earned_points"], 4.6875 / 3)

    def test_all_credential_types_use_the_same_scoring_formula(self):
        credential_types = (
            "industry_certification",
            "professional_certificate",
            "course_completion",
            "academic_certificate",
        )
        scores = []
        for credential_type in credential_types:
            record = catalogue_record(
                "Typed Credential", "Typed Issuer", {"fixture_role": ["python"]},
                credential_type=credential_type,
                platform="Unlisted Platform" if credential_type in {"course_completion", "professional_certificate"} else None,
            )
            credential = {"name": "Typed Credential", "organization": "Typed Issuer", "credential_type": credential_type}
            if record["platform"]:
                credential["platform"] = record["platform"]
            result = self.score_with_catalogue(
                profile(certifications=[credential]), simple_role(), catalogue(record)
            )
            scores.append(result["categories"]["certifications"]["earned_points"])
        self.assertEqual(scores, [4.6875 / 3] * 4)

    def test_verified_course_completion_can_earn_points(self):
        record = catalogue_record(
            "Course Completion Award", "University", {"fixture_role": ["python"]},
            credential_type="course_completion", platform="Any Course Platform",
        )
        result = self.score_with_catalogue(
            profile(certifications=[{
                "name": "Course Completion Award", "organization": "University",
                "credential_type": "course_completion", "platform": "Any Course Platform",
            }]),
            simple_role(),
            catalogue(record),
        )
        self.assertAlmostEqual(result["categories"]["certifications"]["earned_points"], 4.6875 / 3)

    def test_missing_verified_mappings_redistribute_fresher_allocation(self):
        result = self.score_with_catalogue(profile(), simple_role(), catalogue())
        categories = result["categories"]
        self.assertAlmostEqual(categories["internships"]["maximum_points"], 18.3824, places=4)
        self.assertAlmostEqual(categories["projects"]["maximum_points"], 12.8676, places=4)
        self.assertEqual(categories["certifications"]["maximum_points"], 0.0)
        self.assertIsNone(categories["certifications"]["earned_points"])
        self.assertEqual(result["redistribution"]["points_redistributed"], 4.6875)
        self.assertEqual(sum(result["redistribution"]["effective_category_allocations"].values()), 31.25)

    def test_missing_verified_mappings_redistribute_professional_allocation(self):
        result = self.score_with_catalogue(profile("Experienced"), simple_role(), catalogue())
        effective = result["redistribution"]["effective_category_allocations"]
        self.assertAlmostEqual(effective["professional_experience"], 17.3611, places=4)
        self.assertAlmostEqual(effective["internships"], 6.9444, places=4)
        self.assertAlmostEqual(effective["projects"], 6.9444, places=4)
        self.assertEqual(result["redistribution"]["points_redistributed"], 3.125)
        self.assertEqual(sum(effective.values()), 31.25)

    def test_ambiguous_and_unverified_credentials_are_not_confirmed_skill_gaps(self):
        record = catalogue_record(
            "Review Credential", "Issuer", {"fixture_role": ["python"]},
            mapping_status="needs_review", scoring_eligible=False,
        )
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Review Credential", "organization": "Issuer"}]),
            simple_role(),
            catalogue(record),
        )
        certs = result["categories"]["certifications"]
        self.assertEqual(certs["status"], "unavailable_no_score_eligible_verified_mappings")
        self.assertEqual(certs["reason"], "No supplied credential has a verified, scoring-eligible mapping to evaluated requirements for this role; this is unavailable evidence, not a confirmed skill gap.")

    def test_certification_alternative_groups_count_only_configured_choices(self):
        role = {
            "id": "alternative_role",
            "name": "Alternative Role",
            "skills": {
                "programming_languages": [skill("python", "Python"), skill("java", "Java")],
                "core_technical_skills": [skill("testing", "Testing")],
            },
            "skill_groups": [{
                "id": "language_choice", "relationship": "alternatives", "applicability": "required",
                "minimum_options_to_learn": 1, "skill_ids": ["python", "java"],
            }],
        }
        record = catalogue_record(
            "Alternative Certificate", "Issuer", {"alternative_role": ["python", "java"]}
        )
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Alternative Certificate", "organization": "Issuer"}]),
            role,
            catalogue(record),
        )
        certs = result["categories"]["certifications"]
        self.assertEqual(certs["evaluated_requirement_units"], 2)
        self.assertEqual(certs["matched_count"], 1)
        self.assertEqual(len(certs["matched_requirement_units"]), 1)

    def test_certification_compatible_stack_must_be_mapped_by_one_credential(self):
        role = {
            "id": "stack_role",
            "name": "Stack Role",
            "skills": {
                "programming_languages": [skill("python", "Python")],
                "frameworks_and_libraries": [skill("fastapi", "FastAPI")],
            },
            "skill_groups": [{
                "id": "python_fastapi", "relationship": "compatible_stack", "applicability": "optional",
                "skill_ids": ["python", "fastapi"],
            }],
            "technology_stack_requirement": {
                "applicability": "required", "relationship": "alternatives",
                "minimum_options_to_learn": 1, "stack_group_ids": ["python_fastapi"],
            },
        }
        split_catalogue = catalogue(
            catalogue_record("Python Credential", "Issuer A", {"stack_role": ["python"]}, record_id="python_cert"),
            catalogue_record("FastAPI Credential", "Issuer B", {"stack_role": ["fastapi"]}, record_id="fastapi_cert"),
        )
        split_result = self.score_with_catalogue(
            profile(certifications=[
                {"name": "Python Credential", "organization": "Issuer A"},
                {"name": "FastAPI Credential", "organization": "Issuer B"},
            ]),
            role,
            split_catalogue,
        )
        self.assertEqual(split_result["categories"]["certifications"]["matched_count"], 0)
        together_record = catalogue_record(
            "Stack Credential", "Issuer", {"stack_role": ["python", "fastapi"]}
        )
        together_result = self.score_with_catalogue(
            profile(certifications=[{"name": "Stack Credential", "organization": "Issuer"}]),
            role,
            catalogue(together_record),
        )
        self.assertEqual(together_result["categories"]["certifications"]["matched_count"], 1)
        self.assertEqual(together_result["categories"]["certifications"]["matched_skill_ids"], ["fastapi", "python"])

    def load_catalogue_fixture(self, catalogue_data):
        from io import StringIO

        class MemoryPath:
            def open(self, **kwargs):
                return StringIO(json.dumps(catalogue_data))

        with patch.object(career_intelligence, "_CERTIFICATION_CATALOGUE_PATH", MemoryPath()):
            return REAL_LOAD_CERTIFICATION_CATALOGUE()

    def test_verified_but_non_eligible_credential_never_earns_points(self):
        record = catalogue_record(
            "Verified but Disabled", "Issuer", {"fixture_role": ["python"]},
            scoring_eligible=False,
        )
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Verified but Disabled", "organization": "Issuer"}]),
            simple_role(),
            catalogue(record),
        )
        certs = result["categories"]["certifications"]
        self.assertFalse(result["certification_mapping_available"])
        self.assertEqual(certs["eligible_credentials"], [])
        self.assertIn(certs["earned_points"], (None, 0))

    def test_production_loader_rejects_duplicate_catalogue_credential_ids(self):
        source = deepcopy(REAL_LOAD_CERTIFICATION_CATALOGUE())
        duplicate = deepcopy(source["certifications"][0])
        source["certifications"].append(duplicate)
        with self.assertRaises(ValueError):
            self.load_catalogue_fixture(source)

    def test_repeating_one_profile_credential_does_not_duplicate_points(self):
        record = catalogue_record(
            "Repeatable Credential", "Issuer", {"fixture_role": ["python"]}
        )
        credential = {"name": "Repeatable Credential", "organization": "Issuer"}
        one = self.score_with_catalogue(
            profile(certifications=[credential]), simple_role(), catalogue(record)
        )["categories"]["certifications"]
        repeated = self.score_with_catalogue(
            profile(certifications=[credential, credential]), simple_role(), catalogue(record)
        )["categories"]["certifications"]
        self.assertEqual(repeated["earned_points"], one["earned_points"])
        self.assertEqual(repeated["matched_count"], one["matched_count"])
        self.assertEqual(len(repeated["eligible_credentials"]), 1)

    def test_production_loader_rejects_duplicate_skill_ids_in_role_mapping(self):
        source = deepcopy(REAL_LOAD_CERTIFICATION_CATALOGUE())
        eligible = next(item for item in source["certifications"] if item["role_mappings"])
        mapping = eligible["role_mappings"][0]
        mapping["skill_ids"].append(mapping["skill_ids"][0])
        with self.assertRaises(ValueError):
            self.load_catalogue_fixture(source)

    def test_supplied_optional_identity_metadata_mismatch_is_rejected(self):
        metadata = {
            "credential_type": ("industry_certification", "professional_certificate"),
            "platform": ("Official Platform", "Other Platform"),
            "exam_code": ("EX-101", "EX-202"),
            "version": ("v1", "v2"),
        }
        for field, (canonical, supplied) in metadata.items():
            with self.subTest(field=field):
                record = catalogue_record(
                    "Metadata Credential", "Issuer", {"fixture_role": ["python"]},
                    record_id=f"metadata_{field}", **{field: canonical},
                )
                result = self.score_with_catalogue(
                    profile(certifications=[{
                        "name": "Metadata Credential",
                        "organization": "Issuer",
                        field: supplied,
                    }]),
                    simple_role(),
                    catalogue(record),
                )
                certs = result["categories"]["certifications"]
                self.assertEqual(certs["eligible_credentials"], [])
                self.assertEqual(certs["unmatched_credentials"][0]["reason"], "no_exact_catalogue_identity")
                self.assertEqual(certs["earned_points"], 0)

    def test_missing_or_blank_credential_identity_fields_do_not_match(self):
        record = catalogue_record(
            "Identity Credential", "Identity Issuer", {"fixture_role": ["python"]}
        )
        credentials = (
            {"organization": "Identity Issuer"},
            {"name": "   ", "organization": "Identity Issuer"},
            {"name": "Identity Credential"},
            {"name": "Identity Credential", "organization": "  "},
        )
        for credential in credentials:
            with self.subTest(credential=credential):
                result = self.score_with_catalogue(
                    profile(certifications=[credential]), simple_role(), catalogue(record)
                )
                certs = result["categories"]["certifications"]
                self.assertEqual(certs["eligible_credentials"], [])
                self.assertEqual(len(certs["unmatched_credentials"]), 1)

    def test_optional_and_conditional_only_certification_mappings_earn_no_points(self):
        role = {
            "id": "limited_requirements_role",
            "name": "Limited Requirements Role",
            "skills": {
                "programming_languages": [skill("python", "Python")],
                "tools_and_platforms": [skill("terraform", "Terraform", "optional")],
                "domain_specific_knowledge": [skill("compliance", "Compliance")],
            },
            "skill_groups": [{
                "id": "conditional_compliance",
                "relationship": "complementary",
                "applicability": "conditional",
                "skill_ids": ["compliance"],
            }],
        }
        record = catalogue_record(
            "Nonrequired Credential", "Issuer",
            {"limited_requirements_role": ["terraform", "compliance"]},
        )
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Nonrequired Credential", "organization": "Issuer"}]),
            role,
            catalogue(record),
        )
        certs = result["categories"]["certifications"]
        self.assertEqual(certs["matched_count"], 0)
        self.assertEqual(certs["matched_skill_ids"], [])
        self.assertFalse(certs["eligible_credentials"])
        self.assertIn(certs["earned_points"], (None, 0))

    def test_alternative_minimum_greater_than_one_is_respected_without_overcredit(self):
        role = {
            "id": "multi_alternative_role",
            "name": "Multi Alternative Role",
            "skills": {
                "programming_languages": [
                    skill("python", "Python"), skill("java", "Java"), skill("go", "Go"),
                ],
                "core_technical_skills": [skill("testing", "Testing")],
            },
            "skill_groups": [{
                "id": "language_choices",
                "relationship": "alternatives",
                "applicability": "required",
                "minimum_options_to_learn": 2,
                "skill_ids": ["python", "java", "go"],
            }],
        }
        record = catalogue_record(
            "Multi Choice Credential", "Issuer",
            {"multi_alternative_role": ["python", "java", "go", "testing"]},
        )
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Multi Choice Credential", "organization": "Issuer"}]),
            role,
            catalogue(record),
        )
        certs = result["categories"]["certifications"]
        alternative_units = [item for item in certs["matched_requirement_units"] if item.startswith("alternative:")]
        self.assertEqual(certs["evaluated_requirement_units"], 3)
        self.assertEqual(len(alternative_units), 2)
        self.assertEqual(certs["matched_count"], 3)
        self.assertEqual(len(certs["matched_skill_ids"]), 3)

    def test_multiple_compatible_stack_minimum_requires_separate_complete_stacks(self):
        role = {
            "id": "two_stack_role",
            "name": "Two Stack Role",
            "skills": {
                "programming_languages": [skill("python", "Python"), skill("java", "Java")],
                "frameworks_and_libraries": [skill("fastapi", "FastAPI"), skill("spring", "Spring")],
            },
            "skill_groups": [
                {"id": "python_fastapi", "relationship": "compatible_stack", "applicability": "optional", "skill_ids": ["python", "fastapi"]},
                {"id": "java_spring", "relationship": "compatible_stack", "applicability": "optional", "skill_ids": ["java", "spring"]},
            ],
            "technology_stack_requirement": {
                "applicability": "required",
                "relationship": "alternatives",
                "minimum_options_to_learn": 2,
                "stack_group_ids": ["python_fastapi", "java_spring"],
            },
        }
        split_catalogue = catalogue(
            catalogue_record("Python Only", "Issuer A", {"two_stack_role": ["python"]}, record_id="python_only"),
            catalogue_record("FastAPI Only", "Issuer B", {"two_stack_role": ["fastapi"]}, record_id="fastapi_only"),
            catalogue_record("Java Only", "Issuer C", {"two_stack_role": ["java"]}, record_id="java_only"),
            catalogue_record("Spring Only", "Issuer D", {"two_stack_role": ["spring"]}, record_id="spring_only"),
        )
        split = self.score_with_catalogue(
            profile(certifications=[
                {"name": "Python Only", "organization": "Issuer A"},
                {"name": "FastAPI Only", "organization": "Issuer B"},
                {"name": "Java Only", "organization": "Issuer C"},
                {"name": "Spring Only", "organization": "Issuer D"},
            ]), role, split_catalogue,
        )["categories"]["certifications"]
        self.assertEqual(split["matched_count"], 0)
        self.assertEqual(split["matched_requirement_units"], [])

        one_complete = catalogue_record(
            "One Complete Stack", "Issuer", {"two_stack_role": ["python", "fastapi"]}
        )
        one = self.score_with_catalogue(
            profile(certifications=[{"name": "One Complete Stack", "organization": "Issuer"}]),
            role, catalogue(one_complete),
        )["categories"]["certifications"]
        self.assertEqual(one["matched_count"], 1)

        both_complete = catalogue(
            one_complete,
            catalogue_record("Other Complete Stack", "Other Issuer", {"two_stack_role": ["java", "spring"]}, record_id="other_stack"),
        )
        both = self.score_with_catalogue(
            profile(certifications=[
                {"name": "One Complete Stack", "organization": "Issuer"},
                {"name": "Other Complete Stack", "organization": "Other Issuer"},
            ]), role, both_complete,
        )["categories"]["certifications"]
        self.assertEqual(both["matched_count"], 2)
        self.assertEqual(len(both["matched_requirement_units"]), 2)

    def test_unmatched_credential_does_not_redistribute_applicable_mapping_allocation(self):
        record = catalogue_record(
            "Applicable Credential", "Issuer", {"fixture_role": ["python"]}
        )
        result = self.score_with_catalogue(
            profile(certifications=[{"name": "Unknown Credential", "organization": "Other Issuer"}]),
            simple_role(), catalogue(record),
        )
        certs = result["categories"]["certifications"]
        self.assertTrue(result["certification_mapping_available"])
        self.assertEqual(certs["earned_points"], 0)
        self.assertEqual(result["redistribution"]["points_redistributed"], 0)

    def test_linkedin_learning_and_udemy_completion_records_remain_non_scorable(self):
        source = REAL_LOAD_CERTIFICATION_CATALOGUE()
        records = {record["id"]: record for record in source["certifications"]}
        for credential_id in (
            "linkedin_learning_python_essential_training_fundamentals",
            "udemy_learn_python_programming_masterclass",
        ):
            with self.subTest(credential_id=credential_id):
                record = records[credential_id]
                self.assertEqual(record["credential_type"], "course_completion")
                self.assertEqual(record["mapping_status"], "needs_review")
                self.assertFalse(record["scoring_eligible"])


def assessment_skill(skill_id, name, importance="core", aliases=None):
    result = {"id": skill_id, "name": name, "importance": importance, "why_relevant": "fixture"}
    if aliases is not None:
        result["aliases"] = aliases
    return result


def assessment_role(skills, groups=None, stack_requirement=None, role_id="fixture_role"):
    role = {
        "id": role_id,
        "name": "Fixture Role",
        "career_domain": "Fixture Domain",
        "skills": skills,
        "skill_groups": groups or [],
    }
    if stack_requirement is not None:
        role["technology_stack_requirement"] = stack_requirement
    return role


def assessment_knowledge_base(*roles):
    role_ids = [role["id"] for role in roles]
    return {
        "knowledge_base_name": "Skill assessment fixture",
        "version": "test",
        "total_roles": len(roles),
        "domains": [{"id": "fixture_domain", "name": "Fixture Domain", "role_ids": role_ids}],
        "roles": list(roles),
    }


class SkillAlignmentScoringTests(unittest.TestCase):
    def score(self, role, submitted, *, extra_profile=None):
        profile_data = {"careerSkills": {"technicalSkills": submitted}}
        if extra_profile:
            profile_data["careerSkills"].update(extra_profile)
        knowledge_base = assessment_knowledge_base(role)
        return score_skill_alignment(profile_data, knowledge_base=knowledge_base)["roles"][0]

    def test_one_matching_skill_is_scored_against_remaining_applicable_requirements(self):
        role = assessment_role({"core_technical_skills": [
            assessment_skill("core", "Core skill", "core"),
            assessment_skill("important", "Important skill", "important"),
            assessment_skill("specialised", "Specialised skill", "specialised"),
        ]})
        total = 9
        for skill_name, points in (("Core skill", 4), ("Important skill", 3), ("Specialised skill", 2)):
            with self.subTest(skill=skill_name):
                result = self.score(role, [skill_name])
                self.assertEqual(result["earned_evaluated_points"], points)
                self.assertEqual(result["available_evaluated_points"], total)
                self.assertEqual(result["score"], round(43.75 * points / total, 4))
                self.assertEqual(result["evidence_coverage_percentage"], round(100 * points / total, 4))
                self.assertLess(result["score"], 43.75)
                self.assertGreaterEqual(result["score"], 0)
                self.assertLessEqual(result["score"], 43.75)

    def test_multiple_matching_skills_sum_their_importance_weights(self):
        role = assessment_role({"core_technical_skills": [
            assessment_skill("core", "Core skill", "core"),
            assessment_skill("important", "Important skill", "important"),
            assessment_skill("specialised", "Specialised skill", "specialised"),
        ]})
        result = self.score(role, ["Core skill", "Important skill"])
        self.assertEqual(result["earned_evaluated_points"], 7)
        self.assertEqual(result["available_evaluated_points"], 9)
        self.assertEqual(result["score"], round(43.75 * 7 / 9, 4))
        self.assertEqual(result["evidence_coverage_percentage"], round(100 * 7 / 9, 4))

    def test_no_matching_skills_scores_available_zero_when_requirements_apply(self):
        role = assessment_role({"core_technical_skills": [
            assessment_skill("core", "Core skill", "core"),
            assessment_skill("important", "Important skill", "important"),
        ]})
        result = self.score(role, [])
        self.assertTrue(result["score_available"])
        self.assertEqual(result["score_status"], "scored")
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["earned_evaluated_points"], 0)
        self.assertEqual(result["available_evaluated_points"], 7)
        self.assertEqual(result["evidence_coverage_percentage"], 0.0)
        self.assertEqual(len(result["unknown_unassessed_requirements"]), 2)

    def test_optional_skill_is_reported_but_never_scored_or_penalized(self):
        role = assessment_role({"tools_and_platforms": [assessment_skill("optional", "Optional Tool", "optional")]})
        result = self.score(role, ["Optional Tool"])
        self.assertFalse(result["score_available"])
        self.assertIsNone(result["score"])
        self.assertIsNone(result["available_evaluated_points"])
        self.assertEqual(result["score_status"], "unavailable_no_applicable_requirements")
        self.assertEqual(result["matched_skill_ids"], ["optional"])
        self.assertEqual(result["credited_skill_ids"], [])

    def test_no_applicable_weighted_requirements_are_unavailable(self):
        role = assessment_role({"core_technical_skills": [
            assessment_skill("optional", "Optional", "optional"),
        ]})
        result = self.score(role, ["Python"])
        self.assertFalse(result["score_available"])
        self.assertIsNone(result["score"])
        self.assertIsNone(result["evidence_coverage_percentage"])
        self.assertIsNone(result["available_evaluated_points"])
        self.assertIsNone(result["total_applicable_requirement_points"])

    def test_unlisted_skill_is_unknown_not_explicitly_not_known(self):
        role = assessment_role({"core_technical_skills": [assessment_skill("python", "Python")]})
        result = self.score(role, [], extra_profile={"notKnownSkills": ["Python"]})
        self.assertTrue(result["score_available"])
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["evidence_coverage_percentage"], 0.0)
        self.assertEqual(result["available_evaluated_points"], 4)
        self.assertEqual(result["explicitly_not_known_requirements"], [])
        self.assertEqual(result["unknown_unassessed_requirements"][0]["skill_id"], "python")

    def test_matching_normalizes_case_whitespace_and_approved_aliases(self):
        role = assessment_role({
            "programming_languages": [assessment_skill("python", "Python", aliases=["Py 3"])],
        })
        result = self.score(role, ["  PYTHON   ", "py 3"])
        self.assertEqual(result["matched_skill_ids"], ["python"])
        self.assertEqual(result["available_evaluated_points"], 4)

    def test_ambiguous_alias_is_not_used(self):
        role = assessment_role({
            "programming_languages": [
                assessment_skill("one", "Language One", aliases=["Common Alias"]),
                assessment_skill("two", "Language Two", aliases=["common alias"]),
            ],
        })
        result = self.score(role, ["Common Alias"])
        self.assertEqual(result["matched_skill_ids"], [])
        self.assertTrue(result["score_available"])
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["available_evaluated_points"], 8)

    def test_unknown_or_near_match_name_is_not_fuzzy_matched(self):
        role = assessment_role({"programming_languages": [assessment_skill("python", "Python")]})
        result = self.score(role, ["Pythn"])
        self.assertEqual(result["matched_skill_ids"], [])
        self.assertTrue(result["score_available"])
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["available_evaluated_points"], 4)

    def test_duplicate_submissions_and_repeated_canonical_names_count_once(self):
        role = assessment_role({"programming_languages": [assessment_skill("python", "Python")]})
        result = self.score(role, ["Python", " python ", "PYTHON"])
        self.assertEqual(result["matched_skill_ids"], ["python"])
        self.assertEqual(result["available_evaluated_points"], 4)

    def test_minimum_one_alternative_primary_and_secondary_are_scored(self):
        role = assessment_role({
            "programming_languages": [
                assessment_skill("java", "Java", "core"),
                assessment_skill("python", "Python", "important"),
                assessment_skill("go", "Go", "specialised"),
            ],
        }, groups=[{
            "id": "language_options", "name": "Languages", "relationship": "alternatives",
            "applicability": "required", "minimum_options_to_learn": 1,
            "skill_ids": ["java", "python", "go"], "explanation": "Choose one.",
        }])
        primary = self.score(role, ["Java"])
        secondary = self.score(role, ["Go"])
        self.assertEqual(primary["alternative_groups"][0]["assessed_capacity_points"], 4)
        self.assertEqual(primary["alternative_groups"][0]["earned_points"], 4)
        self.assertEqual(primary["available_evaluated_points"], 4)
        self.assertEqual(primary["score"], 43.75)
        self.assertEqual(secondary["alternative_groups"][0]["assessed_capacity_points"], 2)
        self.assertEqual(secondary["alternative_groups"][0]["earned_points"], 2)
        self.assertEqual(secondary["available_evaluated_points"], 4)
        self.assertEqual(secondary["score"], 21.875)
        self.assertEqual(secondary["evidence_coverage_percentage"], 50.0)

    def test_minimum_one_multiple_alternatives_use_highest_weight_and_remain_visible(self):
        role = assessment_role({
            "programming_languages": [
                assessment_skill("java", "Java", "core"),
                assessment_skill("python", "Python", "important"),
                assessment_skill("csharp", "C#", "important"),
                assessment_skill("go", "Go", "specialised"),
            ],
        }, groups=[{
            "id": "language_options", "name": "Languages", "relationship": "alternatives",
            "applicability": "required", "minimum_options_to_learn": 1,
            "skill_ids": ["java", "python", "csharp", "go"], "explanation": "Choose one.",
        }])
        result = self.score(role, ["Python", "C#", "Java"])
        group = result["alternative_groups"][0]
        self.assertEqual(len(group["assessed_alternatives"]), 3)
        self.assertEqual(group["assessed_capacity_points"], 4)
        self.assertEqual(group["earned_points"], 4)
        self.assertEqual(result["matched_skill_ids"], ["csharp", "java", "python"])
        self.assertEqual(result["credited_skill_ids"], ["java"])

    def test_minimum_greater_than_one_uses_top_assessed_weights(self):
        role = assessment_role({
            "programming_languages": [
                assessment_skill("java", "Java", "core"),
                assessment_skill("python", "Python", "important"),
                assessment_skill("go", "Go", "specialised"),
            ],
        }, groups=[{
            "id": "language_options", "name": "Languages", "relationship": "alternatives",
            "applicability": "required", "minimum_options_to_learn": 2,
            "skill_ids": ["java", "python", "go"], "explanation": "Choose two.",
        }])
        result = self.score(role, ["Java", "Go"])
        group = result["alternative_groups"][0]
        self.assertEqual(group["assessed_capacity_points"], 6)
        self.assertEqual(group["earned_points"], 6)
        self.assertEqual(group["total_possible_capacity_points"], 7)
        self.assertEqual(result["available_evaluated_points"], 7)
        self.assertEqual(result["score"], 37.5)
        self.assertEqual(result["evidence_coverage_percentage"], round(6 / 7 * 100, 4))

    def test_unknown_alternatives_do_not_add_assessed_capacity_or_penalty(self):
        role = assessment_role({
            "frameworks_and_libraries": [
                assessment_skill("react", "React", "core"),
                assessment_skill("vue", "Vue", "important"),
                assessment_skill("svelte", "Svelte", "specialised"),
            ],
        }, groups=[{
            "id": "framework_options", "name": "Frameworks", "relationship": "alternatives",
            "applicability": "required", "minimum_options_to_learn": 1,
            "skill_ids": ["react", "vue", "svelte"], "explanation": "Choose one.",
        }])
        result = self.score(role, ["Vue"])
        group = result["alternative_groups"][0]
        self.assertEqual(group["assessed_capacity_points"], 3)
        self.assertEqual(group["earned_points"], 3)
        self.assertEqual(group["unknown_alternatives"], ["react", "svelte"])
        self.assertEqual(result["available_evaluated_points"], 4)
        self.assertEqual(result["score"], 32.8125)
        self.assertEqual(result["evidence_coverage_percentage"], 75.0)

    def test_complementary_members_are_assessed_individually(self):
        role = assessment_role({
            "core_technical_skills": [
                assessment_skill("analysis", "Analysis", "core"),
                assessment_skill("design", "Design", "important"),
            ],
        }, groups=[{
            "id": "analysis_design", "name": "Analysis and Design", "relationship": "complementary",
            "applicability": "required", "skill_ids": ["analysis", "design"],
            "explanation": "Used together.",
        }])
        result = self.score(role, ["Analysis"])
        self.assertEqual(result["available_evaluated_points"], 7)
        self.assertEqual(result["earned_evaluated_points"], 4)
        self.assertEqual(result["score"], 25.0)
        self.assertEqual(result["complementary_groups"][0]["total_possible_capacity_points"], 7)
        self.assertEqual(result["complementary_groups"][0]["unknown_skill_ids"], ["design"])
        self.assertEqual(result["evidence_coverage_percentage"], round(4 / 7 * 100, 4))

    def test_required_stack_is_intact_and_partial_cross_stack_match_is_unassessed(self):
        role = assessment_role({
            "programming_languages": [assessment_skill("python", "Python", "core"), assessment_skill("java", "Java", "core")],
            "frameworks_and_libraries": [assessment_skill("fastapi", "FastAPI", "important"), assessment_skill("spring", "Spring", "important")],
        }, groups=[
            {"id": "python_fastapi", "name": "Python FastAPI", "relationship": "compatible_stack", "applicability": "optional", "skill_ids": ["python", "fastapi"], "explanation": "Stack."},
            {"id": "java_spring", "name": "Java Spring", "relationship": "compatible_stack", "applicability": "optional", "skill_ids": ["java", "spring"], "explanation": "Stack."},
        ], stack_requirement={
            "applicability": "required", "relationship": "alternatives", "minimum_options_to_learn": 1,
            "stack_group_ids": ["python_fastapi", "java_spring"], "explanation": "Choose a valid stack.",
        })
        split = self.score(role, ["Python", "Spring"])
        self.assertTrue(split["score_available"])
        self.assertEqual(split["score"], 0.0)
        self.assertEqual(split["available_evaluated_points"], 7)
        self.assertEqual(split["evidence_coverage_percentage"], 0.0)
        self.assertEqual(split["compatible_stacks"]["assessed_stack_ids"], [])
        complete = self.score(role, ["Python", "FastAPI"])
        self.assertEqual(complete["compatible_stacks"]["assessed_stack_ids"], ["python_fastapi"])
        self.assertEqual(complete["available_evaluated_points"], 7)
        self.assertEqual(complete["credited_skill_ids"], ["fastapi", "python"])

    def test_overlapping_stack_members_are_counted_once(self):
        role = assessment_role({
            "programming_languages": [assessment_skill("python", "Python", "core")],
            "frameworks_and_libraries": [
                assessment_skill("fastapi", "FastAPI", "important"),
                assessment_skill("django", "Django", "important"),
            ],
        }, groups=[
            {"id": "python_fastapi", "name": "Python FastAPI", "relationship": "compatible_stack", "applicability": "optional", "skill_ids": ["python", "fastapi"], "explanation": "Stack."},
            {"id": "python_django", "name": "Python Django", "relationship": "compatible_stack", "applicability": "optional", "skill_ids": ["python", "django"], "explanation": "Stack."},
        ], stack_requirement={
            "applicability": "required", "relationship": "alternatives", "minimum_options_to_learn": 1,
            "stack_group_ids": ["python_fastapi", "python_django"], "explanation": "Choose a valid stack.",
        })
        result = self.score(role, ["Python", "FastAPI", "Django"])
        self.assertEqual(result["available_evaluated_points"], 7)
        self.assertEqual(len(result["credited_skill_ids"]), 2)
        self.assertEqual(result["credited_skill_ids"].count("python"), 1)
        self.assertEqual(len(result["compatible_stacks"]["assessed_stack_ids"]), 1)

    def test_role_level_stack_minimum_is_respected_without_penalizing_unselected_options(self):
        role = assessment_role({
            "programming_languages": [
                assessment_skill("java", "Java", "core"),
                assessment_skill("python", "Python", "core"),
                assessment_skill("go", "Go", "core"),
            ],
            "frameworks_and_libraries": [
                assessment_skill("spring", "Spring", "important"),
                assessment_skill("fastapi", "FastAPI", "important"),
                assessment_skill("gin", "Gin", "important"),
            ],
        }, groups=[
            {"id": "java_spring", "name": "Java Spring", "relationship": "compatible_stack", "applicability": "optional", "skill_ids": ["java", "spring"], "explanation": "Stack."},
            {"id": "python_fastapi", "name": "Python FastAPI", "relationship": "compatible_stack", "applicability": "optional", "skill_ids": ["python", "fastapi"], "explanation": "Stack."},
            {"id": "go_gin", "name": "Go Gin", "relationship": "compatible_stack", "applicability": "optional", "skill_ids": ["go", "gin"], "explanation": "Stack."},
        ], stack_requirement={
            "applicability": "required", "relationship": "alternatives", "minimum_options_to_learn": 2,
            "stack_group_ids": ["java_spring", "python_fastapi", "go_gin"], "explanation": "Choose two valid stacks.",
        })
        result = self.score(role, ["Java", "Spring", "Python", "FastAPI"])
        self.assertEqual(result["compatible_stacks"]["minimum_options_to_learn"], 2)
        self.assertEqual(set(result["compatible_stacks"]["assessed_stack_ids"]), {"java_spring", "python_fastapi"})
        self.assertEqual(result["available_evaluated_points"], 14)
        self.assertEqual(result["earned_evaluated_points"], 14)
        self.assertEqual(result["evidence_coverage_percentage"], 100.0)

    def test_unknown_conditional_requirements_are_excluded(self):
        role = assessment_role({"domain_specific_knowledge": [assessment_skill("vault", "Vault", "core")]}, groups=[{
            "id": "conditional_vault", "name": "Vault requirement", "relationship": "complementary",
            "applicability": "conditional", "condition": "Only when the organization uses Vault.",
            "skill_ids": ["vault"], "explanation": "Conditional.",
        }])
        result = self.score(role, ["Vault"])
        self.assertFalse(result["score_available"])
        self.assertEqual(result["excluded_conditional_requirements"][0]["skill_ids"], ["vault"])
        self.assertIsNone(result["total_applicable_requirement_points"])

    def test_overlapping_required_groups_do_not_double_count_a_skill(self):
        role = assessment_role({
            "core_technical_skills": [assessment_skill("python", "Python", "core"), assessment_skill("go", "Go", "important")],
        }, groups=[
            {"id": "a_languages", "name": "A languages", "relationship": "alternatives", "applicability": "required", "minimum_options_to_learn": 1, "skill_ids": ["python"], "explanation": "A."},
            {"id": "b_languages", "name": "B languages", "relationship": "alternatives", "applicability": "required", "minimum_options_to_learn": 1, "skill_ids": ["python", "go"], "explanation": "B."},
        ])
        result = self.score(role, ["Python"])
        self.assertEqual(result["available_evaluated_points"], 7)
        self.assertEqual(result["credited_skill_ids"], ["python"])
        self.assertTrue(result["overlap_exclusions"])

    def test_all_22_roles_are_evaluated_deterministically(self):
        first = score_skill_alignment({"careerSkills": {"technicalSkills": [" Python ", "JavaScript"]}})
        second = score_skill_alignment({"careerSkills": {"technicalSkills": [" Python ", "JavaScript"]}})
        self.assertEqual(first, second)
        self.assertEqual(first["roles_evaluated"], 22)
        self.assertEqual(len(first["roles"]), 22)
        self.assertEqual(len({item["role_id"] for item in first["roles"]}), 22)
        role_names = [item["role_name"] for item in first["roles"]]
        self.assertEqual(role_names, sorted(role_names, key=str.casefold))
        for result in first["roles"]:
            with self.subTest(role_id=result["role_id"]):
                if result["score_available"]:
                    self.assertGreaterEqual(result["score"], 0)
                    self.assertLessEqual(result["score"], 43.75)
                    self.assertEqual(
                        result["score"],
                        round(43.75 * result["earned_evaluated_points"] / result["available_evaluated_points"], 4),
                    )
                    self.assertEqual(
                        result["evidence_coverage_percentage"],
                        round(100 * result["earned_evaluated_points"] / result["available_evaluated_points"], 4),
                    )
                else:
                    self.assertIsNone(result["score"])

    def test_profile_projects_and_certifications_are_not_used(self):
        role = assessment_role({"programming_languages": [assessment_skill("python", "Python", "core")]})
        result = self.score(role, [], extra_profile={"projects": [{"technologies": "Python"}], "certifications": [{"name": "Python"}]})
        self.assertTrue(result["score_available"])
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["matched_skill_ids"], [])

    def test_role_specific_importance_is_used_for_same_skill_id(self):
        roles = [
            assessment_role({"core_technical_skills": [assessment_skill("python", "Python", "core")]}, role_id="role_core"),
            assessment_role({"core_technical_skills": [assessment_skill("python", "Python", "specialised")]}, role_id="role_specialised"),
        ]
        result = score_skill_alignment({"careerSkills": {"technicalSkills": ["Python"]}}, knowledge_base=assessment_knowledge_base(*roles))
        by_id = {item["role_id"]: item for item in result["roles"]}
        self.assertEqual(by_id["role_core"]["available_evaluated_points"], 4)
        self.assertEqual(by_id["role_specialised"]["available_evaluated_points"], 2)


class InterestAlignmentScoringTests(unittest.TestCase):
    def setUp(self):
        self.interests, self.catalogue_activities, self.knowledge_base = load_interest_catalogues()
        self.activities = deepcopy(self.catalogue_activities)
        for activity in self.activities["activities"]:
            activity["role_mappings"] = []

    def score(self, career_interest=None, activity="Not Sure Yet", **overrides):
        preferences = {
            "career_interest": career_interest,
            "preferred_work_activity": activity,
        }
        preferences.update(overrides.pop("preferences", {}))
        return score_interest_alignment(
            preferences,
            career_interest_catalogue=overrides.get("career_interest_catalogue", self.interests),
            role_activity_catalogue=overrides.get("role_activity_catalogue", self.activities),
            knowledge_base=overrides.get("knowledge_base", self.knowledge_base),
        )

    def relationship(self, role_id="software_engineer", *, status="exact_match", review_status="needs_review"):
        role = next(item for item in self.knowledge_base["roles"] if item["id"] == role_id)
        return {
            "role_id": role_id,
            "role_name": role["name"],
            "status": status,
            "review_status": review_status,
            "partial_match_weight": None,
            "evidence_references": [],
            "rationale": "Synthetic test fixture only.",
            "reviewer_rationale": None,
            "reviewer_identity": None,
            "reviewed_at": None,
        }

    def approve_relationship(self, relationship):
        relationship.update({
            "evidence_references": [{
                "source_id": "test-source",
                "reference": "https://example.test/evidence",
                "description": "Synthetic test-only evidence.",
            }],
            "reviewer_rationale": "Synthetic test-only approval.",
            "reviewer_identity": "test-reviewer",
            "reviewed_at": "2026-11-16T12:00:00+00:00",
            "review_status": "approved",
        })
        return relationship

    def add_relationship(self, catalogue, relationship, activity_id="building_applications_and_software"):
        activity = next(item for item in catalogue["activities"] if item["id"] == activity_id)
        activity["role_mappings"].append(relationship)
        return activity

    def test_all_ten_approved_mappings_resolve_to_their_domain(self):
        approved = {
            "software_development": "software_development",
            "artificial_intelligence_machine_learning": "ai_and_machine_learning",
            "data_science_analytics": "data_science_and_analytics",
            "cloud_computing_devops": "cloud_and_devops",
            "cybersecurity": "cybersecurity",
            "it_infrastructure_networking": "it_infrastructure_and_networking",
            "software_testing_quality_assurance": "software_testing_and_quality_assurance",
            "ui_ux_product_design": "ui_ux_and_product_design",
            "product_project_management": "product_and_project_management",
            "business_technology_consulting": "business_and_technology_consulting",
        }
        records = {item["id"]: item for item in self.interests["options"]}
        for option_id, domain_id in approved.items():
            with self.subTest(option_id=option_id):
                self.assertEqual(records[option_id]["mapping_status"], "mapped")
                self.assertEqual(records[option_id]["domain_ids"], [domain_id])
                result = self.score(records[option_id]["label"])
                matching = [role for role in result["roles"] if role["role_domain_id"] == domain_id]
                self.assertTrue(matching)
                self.assertTrue(all(role["score"] == 25 for role in matching))

    def test_only_mapped_status_is_score_eligible(self):
        catalogue = deepcopy(self.interests)
        selected = next(item for item in catalogue["options"] if item["id"] == "software_development")
        selected["mapping_status"] = "needs_review"
        result = self.score(selected["label"], career_interest_catalogue=catalogue)
        self.assertFalse(result["score_available"])
        self.assertTrue(all(role["match_status"] == "unavailable" for role in result["roles"]))

    def test_frontend_label_and_stable_id_resolve_exactly(self):
        by_label = self.score("Software Development")
        by_id = self.score("software_development")
        self.assertEqual(by_label, by_id)
        self.assertEqual(by_label["roles"][0]["career_interest_option_id"], "software_development")

    def test_unknown_and_missing_interest_are_unavailable_not_mismatches(self):
        for value, expected in (("made up option", "unknown"), (None, "missing"), ("", "missing")):
            with self.subTest(value=value):
                result = self.score(value)
                self.assertFalse(result["score_available"])
                self.assertEqual(result["availability_status"], "unavailable")
                self.assertTrue(all(role["career_interest_status"] == expected for role in result["roles"]))
                self.assertTrue(all(role["match_status"] == "unavailable" for role in result["roles"]))

    def test_not_sure_yet_is_neutral_and_unavailable(self):
        result = self.score("Not Sure Yet")
        self.assertFalse(result["score_available"])
        self.assertEqual(result["effective_allocation"], 0)
        self.assertEqual(result["redistributed_points"], 0)
        self.assertTrue(all(role["match_status"] == "neutral" for role in result["roles"]))
        self.assertTrue(all(role["score"] is None for role in result["roles"]))

    def test_unmapped_and_unreviewed_options_are_unavailable(self):
        catalogue = deepcopy(self.interests)
        catalogue["options"].extend([
            {"id": "test_unmapped", "label": "Unmapped", "mapping_status": "unmapped", "domain_ids": [], "partial_match_weight": None},
            {"id": "test_review", "label": "Review", "mapping_status": "needs_review", "domain_ids": [], "partial_match_weight": None},
        ])
        for value, status in (("Unmapped", "unmapped"), ("Review", "unreviewed")):
            with self.subTest(value=value):
                result = self.score(value, career_interest_catalogue=catalogue)
                self.assertFalse(result["score_available"])
                self.assertTrue(all(role["career_interest_status"] == status for role in result["roles"]))

    def test_all_twenty_two_roles_are_evaluated_once_and_deterministically(self):
        first = self.score("Software Development")
        second = self.score("Software Development")
        role_ids = [role["id"] for role in self.knowledge_base["roles"]]
        self.assertEqual(first, second)
        self.assertEqual(first["roles_evaluated"], 22)
        self.assertEqual({item["role_id"] for item in first["roles"]}, set(role_ids))
        self.assertEqual(len(first["roles"]), 22)

    def test_matching_and_mismatching_role_points_use_actual_domain_membership(self):
        result = self.score("Software Development")
        for role in result["roles"]:
            if role["role_id"] in next(
                domain["role_ids"] for domain in self.knowledge_base["domains"]
                if domain["id"] == "software_development"
            ):
                self.assertEqual(role["career_interest_earned_points"], 15)
                self.assertEqual(role["match_status"], "matched")
            else:
                self.assertEqual(role["career_interest_earned_points"], 0)
                self.assertEqual(role["score"], 0)
                self.assertEqual(role["match_status"], "mismatched")

    def test_role_domain_is_taken_from_knowledge_base_membership(self):
        result = self.score("Cybersecurity")
        memberships = {
            role_id: domain["id"]
            for domain in self.knowledge_base["domains"]
            for role_id in domain["role_ids"]
        }
        self.assertEqual(
            {item["role_id"]: item["role_domain_id"] for item in result["roles"]},
            memberships,
        )

    def test_no_cross_domain_mapping_is_added(self):
        result = self.score("Artificial Intelligence & Machine Learning")
        self.assertEqual(result["roles"][0]["mapped_domain_ids"], ["ai_and_machine_learning"])
        self.assertTrue(all(len(role["mapped_domain_ids"]) == 1 for role in result["roles"]))

    def test_interest_allocation_is_redistributed_once_when_activity_unavailable(self):
        result = self.score("Software Development", activity="Building applications and software")
        self.assertEqual(result["original_allocation"], {
            "career_interest": 15.0, "preferred_work_activity": 10.0, "total": 25.0,
        })
        self.assertEqual(result["effective_allocation"], 25)
        self.assertEqual(result["redistributed_points"], 10)
        self.assertEqual(result["roles"][0]["career_interest_effective_allocation"], 25)
        self.assertEqual(result["roles"][0]["score"], 25)

    def test_a_matching_role_receives_twenty_five_final_points(self):
        result = self.score("Software Development")
        matched = next(role for role in result["roles"] if role["role_id"] == "software_engineer")
        self.assertEqual(matched["score"], 25)
        self.assertLessEqual(matched["score"], matched["maximum_points"])

    def test_a_valid_mismatch_receives_zero_final_points(self):
        result = self.score("Software Development")
        mismatch = next(role for role in result["roles"] if role["role_id"] == "ai_engineer")
        self.assertEqual(mismatch["score"], 0)
        self.assertEqual(mismatch["availability_status"], "available")

    def test_original_allocations_do_not_change_and_redistribution_is_not_repeated(self):
        result = self.score("Software Development")
        for role in result["roles"]:
            self.assertEqual(role["career_interest_original_allocation"], 15)
            self.assertEqual(role["preferred_work_activity_original_allocation"], 10)
            self.assertEqual(role["redistributed_points"], 10)
            self.assertEqual(role["career_interest_effective_allocation"], 25)

    def test_neutral_interest_and_unavailable_activity_make_factor_unavailable(self):
        result = self.score("Not Sure Yet", activity="Not Sure Yet")
        self.assertFalse(result["score_available"])
        self.assertEqual(result["availability_status"], "unavailable")
        self.assertTrue(all(role["score"] is None for role in result["roles"]))

    def test_four_unmapped_activity_options_remain_unavailable(self):
        eligible = [
            item for item in self.catalogue_activities["activities"]
            if item["id"] in {
                "working_with_ai_and_automation", "managing_cloud_infrastructure",
                "researching_and_exploring_new_technologies", "communicating_with_clients_and_teams",
            }
        ]
        self.assertEqual(len(eligible), 4)
        self.assertTrue(all(item["mapping_status"] == "unmapped" for item in eligible))
        for activity in eligible:
            with self.subTest(activity=activity["id"]):
                result = self.score("Software Development", activity=activity["label"])
                self.assertTrue(all(not row["preferred_work_activity_available"] for row in result["roles"]))
                self.assertTrue(all(row["preferred_work_activity_status"] == "unavailable" for row in result["roles"]))
                self.assertTrue(all(row["preferred_work_activity_earned_points"] == 0 for row in result["roles"]))
                self.assertTrue(all(row["redistributed_points"] == 10 for row in result["roles"]))

    def test_production_activity_catalogue_contains_nine_approved_relationships(self):
        expected_role_ids = {
            "building_applications_and_software": {"software_engineer"},
            "solving_technical_problems": {
                "software_engineer", "network_engineer", "cybersecurity_analyst",
            },
            "working_with_data": {"data_scientist"},
            "working_with_ai_and_automation": set(),
            "designing_user_experiences": {"ui_ux_designer"},
            "managing_cloud_infrastructure": set(),
            "testing_software_and_ensuring_quality": {
                "qa_automation_engineer_sdet", "manual_qa_engineer",
            },
            "planning_and_coordinating_projects": {"project_manager"},
            "researching_and_exploring_new_technologies": set(),
            "communicating_with_clients_and_teams": set(),
            "not_sure_yet": set(),
        }
        approved_candidates = {
            "building_applications_and_software": {"software_engineer"},
            "solving_technical_problems": {
                "software_engineer", "network_engineer", "cybersecurity_analyst",
            },
            "working_with_data": {"data_scientist"},
            "designing_user_experiences": {"ui_ux_designer"},
            "testing_software_and_ensuring_quality": {
                "qa_automation_engineer_sdet", "manual_qa_engineer",
            },
            "planning_and_coordinating_projects": {"project_manager"},
        }
        approved_relationships = {
            ("building_applications_and_software", "software_engineer"),
            ("solving_technical_problems", "software_engineer"),
            ("solving_technical_problems", "network_engineer"),
            ("solving_technical_problems", "cybersecurity_analyst"),
            ("working_with_data", "data_scientist"),
            ("designing_user_experiences", "ui_ux_designer"),
            ("testing_software_and_ensuring_quality", "qa_automation_engineer_sdet"),
            ("testing_software_and_ensuring_quality", "manual_qa_engineer"),
            ("planning_and_coordinating_projects", "project_manager"),
        }
        needs_review_relationships = set()
        expected_activity_options = [
            ("building_applications_and_software", "Building applications and software"),
            ("solving_technical_problems", "Solving technical problems"),
            ("working_with_data", "Working with data"),
            ("working_with_ai_and_automation", "Working with AI and automation"),
            ("designing_user_experiences", "Designing user experiences"),
            ("managing_cloud_infrastructure", "Managing cloud infrastructure"),
            ("testing_software_and_ensuring_quality", "Testing software and ensuring quality"),
            ("planning_and_coordinating_projects", "Planning and coordinating projects"),
            ("researching_and_exploring_new_technologies", "Researching and exploring new technologies"),
            ("communicating_with_clients_and_teams", "Communicating with clients and teams"),
            ("not_sure_yet", "Not Sure Yet"),
        ]
        catalogue_sources = {
            source["id"] for source in self.knowledge_base.get("sources", [])
        }
        catalogue_activities = {
            item["id"]: item for item in self.catalogue_activities["activities"]
        }
        self.assertEqual(set(catalogue_activities), set(expected_role_ids))
        self.assertEqual(
            [(item["id"], item["label"]) for item in self.catalogue_activities["activities"]],
            expected_activity_options,
        )
        self.assertEqual(
            sum(len(item["role_mappings"]) for item in self.catalogue_activities["activities"]),
            9,
        )

        candidate_activity_ids = set()
        for activity_id, expected_ids in expected_role_ids.items():
            with self.subTest(activity_id=activity_id):
                activity = catalogue_activities[activity_id]
                relationships = activity["role_mappings"]
                self.assertEqual({item["role_id"] for item in relationships}, expected_ids)
                self.assertEqual(len(relationships), len(expected_ids))
                self.assertEqual(activity["role_ids"], [])
                self.assertEqual(activity["partial_match_weight"], None)
                self.assertEqual(activity["source_references"], [])
                self.assertEqual(
                    activity["mapping_status"],
                    "neutral" if activity_id == "not_sure_yet" else "unmapped",
                )

                if expected_ids:
                    candidate_activity_ids.add(activity_id)
                for relationship in relationships:
                    pair = (activity_id, relationship["role_id"])
                    is_approved = pair in approved_relationships
                    self.assertEqual(relationship["status"], "exact_match" if is_approved else "unavailable")
                    self.assertEqual(relationship["review_status"], "approved" if is_approved else "needs_review")
                    self.assertIsNone(relationship["partial_match_weight"])
                    self.assertTrue(relationship["rationale"].strip())
                    self.assertTrue(relationship["evidence_references"])
                    if is_approved:
                        self.assertNotRegex(
                            relationship["rationale"].lower(),
                            r"\b(unapproved|pending (human )?review|awaiting approval|approval has not been recorded)\b",
                        )
                        self.assertEqual(relationship["reviewer_identity"], "Project Owner")
                        self.assertTrue(relationship["reviewer_rationale"].strip())
                        parsed_timestamp = datetime.fromisoformat(
                            relationship["reviewed_at"].replace("Z", "+00:00")
                        )
                        self.assertIsNotNone(parsed_timestamp.tzinfo)
                        self.assertIsNotNone(parsed_timestamp.utcoffset())
                    else:
                        self.assertIn("candidate", relationship["rationale"].lower())
                        self.assertIsNone(relationship["reviewer_rationale"])
                        self.assertIsNone(relationship["reviewer_identity"])
                        self.assertIsNone(relationship["reviewed_at"])
                    for reference in relationship["evidence_references"]:
                        is_catalogue_source = reference["source_id"] in catalogue_sources
                        is_explicit_bls_source = reference == {
                            "source_id": "bls",
                            "reference": "https://www.bls.gov/ooh/computer-and-information-technology/computer-network-architects.htm",
                        }
                        self.assertTrue(is_catalogue_source or is_explicit_bls_source)
                        self.assertTrue(reference["reference"].startswith("https://"))

        network_problem_solving = next(
            item
            for item in catalogue_activities["solving_technical_problems"]["role_mappings"]
            if item["role_id"] == "network_engineer"
        )
        self.assertEqual(
            network_problem_solving["evidence_references"],
            [
                {
                    "source_id": "onet",
                    "reference": "https://www.onetonline.org/link/summary/15-1241.00",
                },
                {
                    "source_id": "bls",
                    "reference": "https://www.bls.gov/ooh/computer-and-information-technology/computer-network-architects.htm",
                },
            ],
        )
        self.assertNotIn(
            "15-1244.00",
            " ".join(reference["reference"] for reference in network_problem_solving["evidence_references"]),
        )
        self.assertIn("Direct external task evidence", network_problem_solving["rationale"])
        self.assertIn("CareerCoach knowledge-base evidence", network_problem_solving["rationale"])
        self.assertIn("not a formal taxonomy crosswalk", network_problem_solving["rationale"])
        self.assertEqual(network_problem_solving["status"], "exact_match")
        self.assertEqual(network_problem_solving["review_status"], "approved")

        approved_activity_pairs = {
            (activity_id, relationship["role_id"]): relationship
            for activity_id, activity in catalogue_activities.items()
            for relationship in activity["role_mappings"]
        }
        software_problem_solving = approved_activity_pairs[
            ("solving_technical_problems", "software_engineer")
        ]
        self.assertIn("Debugging", software_problem_solving["reviewer_rationale"])
        self.assertIn("Building applications and software", software_problem_solving["reviewer_rationale"])
        self.assertIn("existing technical problems", software_problem_solving["reviewer_rationale"])

        cybersecurity_problem_solving = approved_activity_pairs[
            ("solving_technical_problems", "cybersecurity_analyst")
        ]
        for phrase in (
            "Security alert investigation", "vulnerability assessment", "incident response",
            "security remediation", "not a formal taxonomy crosswalk",
        ):
            self.assertIn(phrase, cybersecurity_problem_solving["reviewer_rationale"])

        manual_qa_activity = approved_activity_pairs[
            ("testing_software_and_ensuring_quality", "manual_qa_engineer")
        ]
        for phrase in (
            "Exploratory", "functional", "regression", "acceptance testing",
            "defect documentation", "fix verification", "not a preference for manual testing over automation",
        ):
            self.assertIn(phrase, manual_qa_activity["reviewer_rationale"])

        self.assertEqual(catalogue_activities["not_sure_yet"]["neutral"], True)
        self.assertEqual(catalogue_activities["not_sure_yet"]["scoring_availability"], "unavailable")
        self.assertEqual(len(candidate_activity_ids), 6)
        actual_relationships = {
            (activity["id"], relationship["role_id"])
            for activity in self.catalogue_activities["activities"]
            for relationship in activity["role_mappings"]
        }
        actual_approved = {
            (activity["id"], relationship["role_id"])
            for activity in self.catalogue_activities["activities"]
            for relationship in activity["role_mappings"]
            if relationship["review_status"] == "approved"
        }
        actual_needs_review = {
            (activity["id"], relationship["role_id"])
            for activity in self.catalogue_activities["activities"]
            for relationship in activity["role_mappings"]
            if relationship["review_status"] == "needs_review"
        }
        self.assertEqual(len(actual_relationships), 9)
        self.assertEqual(len(actual_approved), 9)
        self.assertEqual(len(actual_needs_review), 0)
        self.assertEqual(actual_approved, approved_relationships)
        self.assertEqual(actual_needs_review, needs_review_relationships)
        approved_rationales = {
            relationship["reviewer_rationale"]
            for activity in self.catalogue_activities["activities"]
            for relationship in activity["role_mappings"]
            if relationship["review_status"] == "approved"
        }
        self.assertEqual(len(approved_rationales), 9)
        for activity_id, role_ids in approved_candidates.items():
            with self.subTest(approved_relationship=activity_id):
                relationships = catalogue_activities[activity_id]["role_mappings"]
                matches = [item for item in relationships if item["role_id"] in role_ids]
                self.assertEqual({item["role_id"] for item in matches}, role_ids)
                self.assertTrue(all(item["review_status"] == "approved" for item in matches))
        self.assertNotIn(
            "project_manager",
            {item["role_id"] for item in catalogue_activities["communicating_with_clients_and_teams"]["role_mappings"]},
        )

        for activity_id in candidate_activity_ids:
            with self.subTest(scoring_activity=activity_id):
                activity = catalogue_activities[activity_id]
                result = self.score(
                    "Not Sure Yet",
                    activity=activity["label"],
                    role_activity_catalogue=self.catalogue_activities,
                )
                self.assertEqual(result["roles_evaluated"], 22)
                self.assertEqual(len({row["role_id"] for row in result["roles"]}), 22)
                expected = {item["role_id"] for item in activity["role_mappings"]}
                available = {
                    row["role_id"] for row in result["roles"]
                    if row["preferred_work_activity_available"]
                }
                self.assertEqual(available, expected)
                for row in result["roles"]:
                    if row["role_id"] in expected:
                        self.assertEqual(row["preferred_work_activity_status"], "exact_match")
                        self.assertEqual(row["preferred_work_activity_earned_points"], 10)
                        self.assertEqual(row["effective_allocation"], 25)
                        self.assertEqual(row["score"], 25)
                    else:
                        self.assertEqual(row["preferred_work_activity_status"], "unavailable")
                        self.assertIsNone(row["score"])

    def test_production_catalogue_scores_all_nine_approved_pairs(self):
        pairs = {
            (activity["id"], relationship["role_id"])
            for activity in self.catalogue_activities["activities"]
            for relationship in activity["role_mappings"]
            if relationship["review_status"] == "approved"
        }
        self.assertEqual(len(pairs), 9)
        for activity_id, role_id in pairs:
            activity = next(item for item in self.catalogue_activities["activities"] if item["id"] == activity_id)
            with self.subTest(activity=activity_id, role=role_id):
                result = self.score(
                    "Not Sure Yet", activity=activity["id"],
                    role_activity_catalogue=self.catalogue_activities,
                )
                self.assertEqual(len(result["roles"]), 22)
                self.assertEqual(len({item["role_id"] for item in result["roles"]}), 22)
                row = next(item for item in result["roles"] if item["role_id"] == role_id)
                self.assertEqual(row["preferred_work_activity_earned_points"], 10)
                self.assertEqual(row["preferred_work_activity_effective_allocation"], 25)
                self.assertEqual(row["score"], 25)
                self.assertEqual(row["redistributed_points"], 15)

    def test_missing_neutral_and_unknown_activity_selections_are_unavailable(self):
        for activity, selection_status in ((None, "missing"), ("Not Sure Yet", "neutral"), ("not-a-real-activity", "unknown")):
            with self.subTest(activity=activity):
                result = self.score("Software Development", activity=activity)
                self.assertTrue(all(not row["preferred_work_activity_available"] for row in result["roles"]))
                self.assertTrue(all(row["preferred_work_activity_selection_status"] == selection_status for row in result["roles"]))
                self.assertTrue(all(row["redistributed_points"] == 10 for row in result["roles"]))

    def test_approved_confirmed_mismatch_is_available_with_zero_and_no_redistribution(self):
        catalogue = deepcopy(self.activities)
        relationship = self.approve_relationship(
            self.relationship(status="confirmed_mismatch", review_status="approved")
        )
        self.add_relationship(catalogue, relationship)
        result = self.score("Not Sure Yet", activity="Building applications and software", role_activity_catalogue=catalogue)
        row = next(item for item in result["roles"] if item["role_id"] == "software_engineer")
        self.assertTrue(row["preferred_work_activity_available"])
        self.assertEqual(row["preferred_work_activity_earned_points"], 0)
        self.assertEqual(row["redistributed_points"], 15)
        self.assertEqual(row["score"], 0)
        self.assertEqual(row["availability_status"], "available")

    def test_approved_partial_match_earns_configured_fraction(self):
        catalogue = deepcopy(self.activities)
        relationship = self.approve_relationship(
            self.relationship(status="partial_match", review_status="approved")
        )
        relationship["partial_match_weight"] = 0.25
        self.add_relationship(catalogue, relationship)
        result = self.score("Not Sure Yet", activity="Building applications and software", role_activity_catalogue=catalogue)
        row = next(item for item in result["roles"] if item["role_id"] == "software_engineer")
        self.assertEqual(row["preferred_work_activity_earned_points"], 2.5)
        self.assertEqual(row["preferred_work_activity_effective_allocation"], 25)
        self.assertEqual(row["score"], 6.25)

    def test_mixed_role_availability_reports_role_specific_redistribution(self):
        result = self.score(
            "Software Development", activity="Building applications and software",
            role_activity_catalogue=self.catalogue_activities,
        )
        software = next(row for row in result["roles"] if row["role_id"] == "software_engineer")
        backend = next(row for row in result["roles"] if row["role_id"] == "ai_engineer")
        self.assertEqual(software["redistributed_points"], 0)
        self.assertEqual(software["score"], 25)
        self.assertEqual(backend["redistributed_points"], 10)
        self.assertEqual(backend["score"], 0)
        self.assertTrue(result["role_specific_allocations"])
        self.assertIsNone(result["redistributed_points"])
        self.assertEqual(result["role_availability_summary"]["career_interest_and_activity_available"], 1)
        self.assertEqual(result["role_availability_summary"]["career_interest_only_available"], 21)

    def test_activity_mapping_does_not_infer_from_role_descriptions(self):
        modified = deepcopy(self.activities)
        self.add_relationship(modified, self.relationship())
        result = self.score("Software Development", activity=modified["activities"][0]["label"], role_activity_catalogue=modified)
        self.assertEqual(result["roles"][0]["preferred_work_activity_status"], "unavailable")
        self.assertEqual(result["roles"][0]["score"], 25)

    def test_empty_relationship_collections_load_and_score_unavailable(self):
        result = self.score("Software Development", activity="Building applications and software")
        self.assertEqual(result["roles"][0]["preferred_work_activity_status"], "unavailable")
        self.assertEqual(result["roles"][0]["score"], 25)

    def test_missing_relationship_collection_is_rejected(self):
        catalogue = deepcopy(self.activities)
        del catalogue["activities"][0]["role_mappings"]
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=catalogue)

    def test_duplicate_activity_labels_are_rejected(self):
        catalogue = deepcopy(self.activities)
        catalogue["activities"][1]["label"] = catalogue["activities"][0]["label"]
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=catalogue)

    def test_unknown_activity_ids_and_changed_labels_are_rejected(self):
        unknown_id = deepcopy(self.activities)
        unknown_id["activities"][0]["id"] = "new_activity"
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=unknown_id)

        changed_label = deepcopy(self.activities)
        changed_label["activities"][0]["label"] = "Renamed activity"
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=changed_label)

    def test_unknown_activity_role_id_is_rejected(self):
        catalogue = deepcopy(self.activities)
        relationship = self.relationship()
        relationship.update({"role_id": "unknown_role", "role_name": "Unknown Role"})
        self.add_relationship(catalogue, relationship)
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=catalogue)

    def test_duplicate_activity_role_relationship_is_rejected(self):
        catalogue = deepcopy(self.activities)
        relationship = self.relationship()
        self.add_relationship(catalogue, relationship)
        self.add_relationship(catalogue, deepcopy(relationship))
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=catalogue)

    def test_activity_relationship_name_must_match_canonical_role_name(self):
        catalogue = deepcopy(self.activities)
        relationship = self.relationship()
        relationship["role_name"] = "Different Role"
        self.add_relationship(catalogue, relationship)
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=catalogue)

    def test_invalid_activity_relationship_and_review_statuses_are_rejected(self):
        for field, invalid in (("status", "pending"), ("review_status", "not_reviewed")):
            with self.subTest(field=field):
                catalogue = deepcopy(self.activities)
                relationship = self.relationship()
                relationship[field] = invalid
                self.add_relationship(catalogue, relationship)
                with self.assertRaises(ValueError):
                    self.score("Software Development", role_activity_catalogue=catalogue)

    def test_activity_partial_weights_follow_goals_preferences_rules(self):
        catalogue = deepcopy(self.activities)
        relationship = self.relationship(status="partial_match")
        relationship["partial_match_weight"] = 0.5
        self.add_relationship(catalogue, relationship)
        self.score("Software Development", role_activity_catalogue=catalogue)

        for invalid_weight in (None, 0, 1, True, float("nan"), float("inf")):
            with self.subTest(weight=invalid_weight):
                catalogue = deepcopy(self.activities)
                relationship = self.relationship(status="partial_match")
                relationship["partial_match_weight"] = invalid_weight
                self.add_relationship(catalogue, relationship)
                with self.assertRaises(ValueError):
                    self.score("Software Development", role_activity_catalogue=catalogue)

    def test_non_partial_activity_relationship_cannot_have_weight(self):
        catalogue = deepcopy(self.activities)
        relationship = self.relationship()
        relationship["partial_match_weight"] = 0.5
        self.add_relationship(catalogue, relationship)
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=catalogue)

    def test_approved_activity_relationship_requires_complete_evidence_and_review_metadata(self):
        catalogue = deepcopy(self.activities)
        relationship = self.relationship(status="exact_match", review_status="approved")
        relationship.update({
            "evidence_references": [{
                "source_id": "test-source",
                "reference": "https://example.test/evidence",
                "description": "Synthetic test-only evidence.",
            }],
            "reviewer_rationale": "Synthetic test-only approval.",
            "reviewer_identity": "test-reviewer",
            "reviewed_at": "2026-11-16T12:00:00+00:00",
        })
        self.add_relationship(catalogue, relationship)
        self.score("Software Development", role_activity_catalogue=catalogue)

        incomplete = deepcopy(self.activities)
        relationship = self.relationship(status="confirmed_mismatch", review_status="approved")
        relationship.update({
            "reviewer_rationale": "Synthetic test-only approval.",
            "reviewer_identity": "test-reviewer",
            "reviewed_at": "2026-11-16T12:00:00+00:00",
        })
        self.add_relationship(incomplete, relationship)
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=incomplete)

    def test_needs_review_activity_relationship_remains_unavailable(self):
        catalogue = deepcopy(self.activities)
        relationship = self.relationship(status="exact_match", review_status="needs_review")
        self.add_relationship(catalogue, relationship)
        result = self.score("Software Development", activity="Building applications and software", role_activity_catalogue=catalogue)
        self.assertEqual(result["roles"][0]["preferred_work_activity_status"], "unavailable")
        self.assertEqual(result["roles"][0]["score"], 25)

    def test_relationship_review_states_are_independent_and_only_approved_relationship_scores(self):
        catalogue = deepcopy(self.activities)
        approved = self.relationship("software_engineer", status="exact_match", review_status="approved")
        approved.update({
            "evidence_references": [{"source_id": "test-source", "reference": "https://example.test/evidence"}],
            "reviewer_rationale": "Synthetic test-only approval.",
            "reviewer_identity": "test-reviewer",
            "reviewed_at": "2026-11-16T12:00:00+00:00",
        })
        unreviewed = self.relationship("backend_developer", status="exact_match", review_status="needs_review")
        self.add_relationship(catalogue, approved)
        self.add_relationship(catalogue, unreviewed)
        result = self.score("Software Development", activity="Building applications and software", role_activity_catalogue=catalogue)
        baseline = self.score("Software Development", activity="Building applications and software")
        self.assertEqual([row["review_status"] for row in catalogue["activities"][0]["role_mappings"]], ["approved", "needs_review"])
        by_id = {row["role_id"]: row for row in result["roles"]}
        self.assertEqual(by_id["software_engineer"]["preferred_work_activity_status"], "exact_match")
        self.assertEqual(by_id["software_engineer"]["preferred_work_activity_earned_points"], 10)
        self.assertEqual(by_id["backend_developer"]["preferred_work_activity_status"], "unavailable")
        self.assertEqual(by_id["backend_developer"]["preferred_work_activity_earned_points"], 0)
        self.assertEqual(
            [(row["role_id"], row["score"]) for row in result["roles"]],
            [(row["role_id"], row["score"]) for row in baseline["roles"]],
        )

    def test_missing_activity_relationship_remains_unavailable_not_a_mismatch(self):
        catalogue = deepcopy(self.activities)
        result = self.score("Software Development", activity="Building applications and software", role_activity_catalogue=catalogue)
        software_engineer = next(role for role in result["roles"] if role["role_id"] == "software_engineer")
        self.assertEqual(software_engineer["preferred_work_activity_status"], "unavailable")
        self.assertEqual(software_engineer["match_status"], "matched")
        self.assertEqual(software_engineer["score"], 25)

    def test_explicit_unavailable_relationship_is_not_a_mismatch(self):
        catalogue = deepcopy(self.activities)
        self.add_relationship(catalogue, self.relationship("software_engineer", status="unavailable"))
        result = self.score("Software Development", activity="Building applications and software", role_activity_catalogue=catalogue)
        software_engineer = next(role for role in result["roles"] if role["role_id"] == "software_engineer")
        self.assertEqual(software_engineer["preferred_work_activity_status"], "unavailable")
        self.assertEqual(software_engineer["match_status"], "matched")
        self.assertEqual(software_engineer["score"], 25)

    def test_legacy_fields_cannot_override_relationship_level_mappings(self):
        for change in (
            {"role_ids": ["software_engineer"]},
            {"mapping_status": "mapped"},
            {"partial_match_weight": 0.5},
            {"source_references": [{"source_id": "test-source", "reference": "https://example.test"}]},
        ):
            with self.subTest(change=change):
                catalogue = deepcopy(self.activities)
                self.add_relationship(catalogue, self.relationship())
                activity = catalogue["activities"][0]
                activity.update(change)
                with self.assertRaises(ValueError):
                    self.score("Software Development", role_activity_catalogue=catalogue)

    def test_neutral_activity_cannot_contain_role_relationships(self):
        catalogue = deepcopy(self.activities)
        self.add_relationship(catalogue, self.relationship(), activity_id="not_sure_yet")
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=catalogue)

    def test_neutral_activity_metadata_cannot_be_changed_to_scoring_state(self):
        catalogue = deepcopy(self.activities)
        neutral = next(item for item in catalogue["activities"] if item["id"] == "not_sure_yet")
        neutral["scoring_availability"] = "available"
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=catalogue)

    def test_invalid_catalogue_domain_reference_fails_safely(self):
        catalogue = deepcopy(self.interests)
        next(item for item in catalogue["options"] if item["id"] == "software_development")["domain_ids"] = ["not_a_domain"]
        with self.assertRaises(ValueError):
            self.score("Software Development", career_interest_catalogue=catalogue)

    def test_invalid_activity_role_reference_fails_safely(self):
        catalogue = deepcopy(self.activities)
        catalogue["activities"][0]["role_ids"] = ["not_a_role"]
        with self.assertRaises(ValueError):
            self.score("Software Development", role_activity_catalogue=catalogue)

    def test_scores_are_bounded_and_return_none_when_unavailable(self):
        available = self.score("Software Development")
        unavailable = self.score("Not Sure Yet")
        self.assertTrue(all(0 <= role["score"] <= 25 for role in available["roles"]))
        self.assertTrue(all(role["score"] is None for role in unavailable["roles"]))

    def test_snake_and_camel_preference_names_are_supported(self):
        snake = self.score("Software Development")
        camel = score_interest_alignment(
            {
                "careerInterest": "Software Development",
                "preferredWorkActivity": "Not Sure Yet",
            },
            career_interest_catalogue=self.interests,
            role_activity_catalogue=self.activities,
            knowledge_base=self.knowledge_base,
        )
        self.assertEqual(snake, camel)


if __name__ == "__main__":
    unittest.main()
