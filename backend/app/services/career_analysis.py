"""Build a deterministic detailed analysis for one canonical career role."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.career_knowledge_graph import load_knowledge_base
from app.services.career_recommendations import score_career_recommendations


class InvalidCareerRoleError(ValueError):
    """Raised when the selected role ID is not in the canonical catalogue."""


class CareerAnalysisDataError(RuntimeError):
    """Raised when role catalogue or scorer output is internally inconsistent."""


_PROFILE_EVIDENCE_LIMITATION = (
    "Requirements not demonstrated in the saved profile are unassessed evidence, not confirmed deficiencies."
)
_PROJECT_EVIDENCE_LIMITATION = (
    "Projects and experience are matched through structured technology fields; descriptions, responsibilities, titles, and company names are not used to infer skills."
)
_CATALOGUE_LIMITATION = (
    "The current catalogue does not provide proficiency levels, curated learning resources, learning timelines, salary information, or job-market prospects."
)


def _string_list(value: Any, description: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CareerAnalysisDataError(f"Career catalogue has invalid {description}.")
    return list(value)


def _mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CareerAnalysisDataError(f"Career analysis contains invalid {description}.")
    return value


def _score_section(component: Any, description: str) -> dict[str, Any]:
    row = _mapping(component, description)
    details = _mapping(row.get("details"), f"{description} details")
    return {
        "score": row.get("score"),
        "maximum_points": row.get("maximum_points"),
        "maximum_applicable_points": row.get("maximum_applicable_points"),
        "available": row.get("available"),
        "status": row.get("status"),
        "details": dict(details),
    }


def _role_and_sources(role_id: str) -> tuple[Mapping[str, Any], dict[str, str], dict[str, Mapping[str, Any]]]:
    knowledge_base = load_knowledge_base()
    roles = knowledge_base.get("roles")
    sources = knowledge_base.get("sources")
    if not isinstance(roles, list) or not isinstance(sources, list):
        raise CareerAnalysisDataError("Career knowledge base is missing roles or sources.")

    roles_by_id: dict[str, Mapping[str, Any]] = {}
    for item in roles:
        record = _mapping(item, "role record")
        item_id, item_name = record.get("id"), record.get("name")
        if not isinstance(item_id, str) or not item_id or not isinstance(item_name, str) or not item_name:
            raise CareerAnalysisDataError("Career knowledge base contains an invalid role identity.")
        if item_id in roles_by_id:
            raise CareerAnalysisDataError("Career knowledge base contains duplicate role IDs.")
        roles_by_id[item_id] = record

    if knowledge_base.get("total_roles") != len(roles_by_id) or len(roles_by_id) != 22:
        raise CareerAnalysisDataError("Career knowledge base does not contain the canonical 22 roles.")
    role = roles_by_id.get(role_id)
    if role is None:
        raise InvalidCareerRoleError("Selected career role is not in the catalogue.")

    sources_by_id: dict[str, Mapping[str, Any]] = {}
    for item in sources:
        source = _mapping(item, "source record")
        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id or source_id in sources_by_id:
            raise CareerAnalysisDataError("Career knowledge base contains invalid source identities.")
        sources_by_id[source_id] = source

    return role, {item_id: item["name"] for item_id, item in roles_by_id.items()}, sources_by_id


def _role_sources(role: Mapping[str, Any], sources_by_id: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    source_ids = _string_list(role.get("source_ids", []), "role source references")
    output: list[dict[str, str]] = []
    for source_id in source_ids:
        source = sources_by_id.get(source_id)
        if source is None:
            raise CareerAnalysisDataError("Career role references an unknown source.")
        required = ("title", "publisher", "url", "use")
        if any(not isinstance(source.get(field), str) or not source[field].strip() for field in required):
            raise CareerAnalysisDataError("Career role references a malformed source.")
        output.append({"id": source_id, **{field: source[field] for field in required}})
    return output


def build_career_analysis(
    profile: Any,
    role_id: str,
    preferences: Any,
) -> dict[str, Any]:
    """Score with existing services, then return only the selected role analysis."""
    if not isinstance(role_id, str) or not role_id.strip():
        raise InvalidCareerRoleError("Selected career role ID is invalid.")

    role, role_names, sources_by_id = _role_and_sources(role_id)
    recommendation_result = score_career_recommendations(profile, preferences)
    scored_roles = recommendation_result.get("roles")
    if not isinstance(scored_roles, list):
        raise CareerAnalysisDataError("Recommendation scorer returned no role results.")
    matches = [item for item in scored_roles if isinstance(item, Mapping) and item.get("role_id") == role_id]
    if len(matches) != 1:
        raise CareerAnalysisDataError("Recommendation scorer did not return one selected role result.")

    role_result = matches[0]
    components = _mapping(role_result.get("components"), "scoring components")
    skill = _score_section(components.get("skill_alignment"), "Skill Alignment")
    projects = _score_section(components.get("projects_experience"), "Projects & Experience")
    interest = _score_section(components.get("interest_alignment"), "Interest Alignment")

    skill_details = skill["details"]
    unknown_requirements = skill_details.get("unknown_unassessed_requirements", [])
    if not isinstance(unknown_requirements, list):
        raise CareerAnalysisDataError("Skill Alignment returned malformed unassessed requirements.")
    for field in ("alternative_groups", "complementary_groups", "excluded_conditional_requirements"):
        if not isinstance(skill_details.get(field, []), list):
            raise CareerAnalysisDataError(f"Skill Alignment returned malformed {field} details.")
    compatible_stacks = skill_details.get("compatible_stacks", {})
    if not isinstance(compatible_stacks, Mapping):
        raise CareerAnalysisDataError("Skill Alignment returned malformed compatible stacks.")

    project_details = projects["details"]
    categories = project_details.get("categories", {})
    if not isinstance(categories, Mapping):
        raise CareerAnalysisDataError("Projects & Experience returned malformed category details.")
    inapplicable_categories = project_details.get("inapplicable_categories", [])
    if not isinstance(inapplicable_categories, list) or any(not isinstance(item, str) for item in inapplicable_categories):
        raise CareerAnalysisDataError("Projects & Experience returned malformed inapplicable categories.")

    responsibilities = _string_list(role.get("typical_responsibilities"), "role responsibilities")
    project_types = _string_list(role.get("relevant_project_types", []), "relevant project types")
    progression_paths = role.get("career_progression_paths", [])
    if (
        not isinstance(progression_paths, list)
        or any(not isinstance(path, list) or any(not isinstance(name, str) for name in path) for path in progression_paths)
    ):
        raise CareerAnalysisDataError("Career role contains malformed progression paths.")
    related_role_ids = _string_list(role.get("related_roles", []), "related role IDs")
    related_roles = []
    for related_id in related_role_ids:
        related_name = role_names.get(related_id)
        if related_name is None:
            raise CareerAnalysisDataError("Career role references an unknown related role.")
        related_roles.append({"role_id": related_id, "name": related_name})

    role_name = role.get("name")
    domain = role.get("career_domain")
    description = role.get("description")
    if any(not isinstance(value, str) or not value.strip() for value in (role_name, domain, description)):
        raise CareerAnalysisDataError("Career role overview is incomplete.")

    unavailable_reasons = role_result.get("unavailable_reasons", [])
    if not isinstance(unavailable_reasons, list):
        raise CareerAnalysisDataError("Recommendation scorer returned malformed availability reasons.")
    limitations = [_PROFILE_EVIDENCE_LIMITATION, _PROJECT_EVIDENCE_LIMITATION, _CATALOGUE_LIMITATION]
    if not project_types:
        limitations.append("No curated project directions are available for this role.")
    if not progression_paths:
        limitations.append("No curated career progression paths are available for this role.")

    return {
        "role_overview": {
            "role_id": role_id,
            "name": role_name,
            "domain": domain,
            "description": description,
            "typical_responsibilities": responsibilities,
            "source_references": _role_sources(role, sources_by_id),
        },
        "skill_alignment": {
            **skill,
            "evidence_coverage_percentage": skill_details.get("evidence_coverage_percentage"),
            "matched_skills": skill_details.get("matched_skills", []),
            "requirements_not_demonstrated_in_profile": unknown_requirements,
            "requirements_interpretation": _PROFILE_EVIDENCE_LIMITATION,
            "alternative_groups": skill_details.get("alternative_groups", []),
            "complementary_groups": skill_details.get("complementary_groups", []),
            "compatible_stacks": dict(compatible_stacks),
            "conditional_exclusions": skill_details.get("excluded_conditional_requirements", []),
        },
        "projects_experience": {
            **projects,
            "category_breakdown": dict(categories),
            "inapplicable_categories": inapplicable_categories,
        },
        "interest_alignment": interest,
        "contextual_preferences": recommendation_result.get("contextual_preferences", {}),
        "project_directions": {
            "source": "career_knowledge_base",
            "catalogue_suggestions": project_types,
            "distinction": "These are curated project-type suggestions, not projects recorded as completed in the saved profile.",
        },
        "career_progression": {
            "paths": progression_paths,
            "related_roles": related_roles,
            "note": "These are curated possible progression paths, not a guarantee of advancement.",
        },
        "availability": {
            "status": role_result.get("status"),
            "unavailable_reasons": unavailable_reasons,
            "limitations": limitations,
        },
    }
