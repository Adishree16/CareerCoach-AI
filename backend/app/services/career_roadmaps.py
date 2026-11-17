"""Build deterministic role-specific career roadmaps from saved profile evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from app.services import career_intelligence, skill_assessment
from app.services.career_knowledge_graph import load_knowledge_base


class InvalidCareerRoleError(ValueError):
    """Raised when the selected role is not in the canonical role catalogue."""


class CareerRoadmapDataError(RuntimeError):
    """Raised when catalogue or scorer output cannot safely form a roadmap."""


_CATEGORY_LABELS = {
    "programming_languages": "Programming languages",
    "core_technical_skills": "Core technical skills",
    "frameworks_and_libraries": "Frameworks and libraries",
    "databases": "Databases",
    "cloud_and_devops": "Cloud and DevOps",
    "tools_and_platforms": "Tools and platforms",
    "computer_science_fundamentals": "Computer science fundamentals",
    "domain_specific_knowledge": "Domain-specific knowledge",
    "soft_skills": "Soft skills",
}
_IMPORTANCE_ORDER = {"core": 0, "important": 1, "specialised": 2, "optional": 3}
_EVIDENCE_CATEGORIES = {
    "projects": "Projects",
    "internships": "Internships",
    "professional_experience": "Professional experience",
}


def _mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CareerRoadmapDataError(f"Roadmap data has an invalid {description}.")
    return value


def _string_list(value: Any, description: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CareerRoadmapDataError(f"Roadmap data has an invalid {description}.")
    return list(value)


def _canonical_role(knowledge_base: Mapping[str, Any], role_id: str) -> Mapping[str, Any]:
    roles = knowledge_base.get("roles")
    if not isinstance(roles, list) or knowledge_base.get("total_roles") != 22 or len(roles) != 22:
        raise CareerRoadmapDataError("Career knowledge base does not contain the canonical 22 roles.")
    roles_by_id: dict[str, Mapping[str, Any]] = {}
    for role_value in roles:
        role = _mapping(role_value, "career role")
        item_id = role.get("id")
        if not isinstance(item_id, str) or not item_id or item_id in roles_by_id:
            raise CareerRoadmapDataError("Career knowledge base contains invalid or duplicate role IDs.")
        roles_by_id[item_id] = role
    role = roles_by_id.get(role_id)
    if role is None:
        raise InvalidCareerRoleError("Career role was not found.")
    return role


def _role_skills(role: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    categories = role.get("skills")
    if not isinstance(categories, Mapping):
        raise CareerRoadmapDataError("Career role has no valid skills catalogue.")
    skills_by_id: dict[str, dict[str, Any]] = {}
    category_order: list[str] = []
    for category_id, skill_values in categories.items():
        if not isinstance(category_id, str) or not isinstance(skill_values, list):
            raise CareerRoadmapDataError("Career role contains malformed skill categories.")
        category_order.append(category_id)
        for skill_value in skill_values:
            skill = _mapping(skill_value, "role skill")
            skill_id, name = skill.get("id"), skill.get("name")
            importance, why_relevant = skill.get("importance"), skill.get("why_relevant")
            if (
                not isinstance(skill_id, str) or not skill_id or skill_id in skills_by_id
                or not isinstance(name, str) or not name.strip()
                or importance not in _IMPORTANCE_ORDER
                or not isinstance(why_relevant, str)
            ):
                raise CareerRoadmapDataError("Career role contains malformed skill metadata.")
            skills_by_id[skill_id] = {
                "skill_id": skill_id,
                "name": name,
                "category_id": category_id,
                "category_name": _CATEGORY_LABELS.get(category_id, category_id.replace("_", " ").title()),
                "importance": importance,
                "why_relevant": why_relevant,
            }
    return skills_by_id, category_order


def _skill_reference(skills_by_id: Mapping[str, Mapping[str, Any]], skill_id: Any) -> dict[str, Any]:
    if not isinstance(skill_id, str) or skill_id not in skills_by_id:
        raise CareerRoadmapDataError("A scorer result references an unknown role skill.")
    return dict(skills_by_id[skill_id])


def _matched_skill_ids(skill_result: Mapping[str, Any], skills_by_id: Mapping[str, Mapping[str, Any]]) -> list[str]:
    matched_values = skill_result.get("matched_skills")
    if not isinstance(matched_values, list):
        raise CareerRoadmapDataError("Skill Alignment returned malformed matched skills.")
    result: list[str] = []
    for item in matched_values:
        record = _mapping(item, "matched skill")
        skill_id = record.get("skill_id")
        _skill_reference(skills_by_id, skill_id)
        if skill_id not in result:
            result.append(skill_id)
    return result


def _required_groups(role: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    group_values = role.get("skill_groups", [])
    if not isinstance(group_values, list):
        raise CareerRoadmapDataError("Career role contains malformed skill groups.")
    result: dict[str, Mapping[str, Any]] = {}
    for item in group_values:
        group = _mapping(item, "skill group")
        group_id = group.get("id")
        if not isinstance(group_id, str) or not group_id or group_id in result:
            raise CareerRoadmapDataError("Career role contains invalid or duplicate skill-group IDs.")
        result[group_id] = group
    return result


def _build_skills_to_explore(
    role: Mapping[str, Any],
    skill_result: Mapping[str, Any],
    skills_by_id: Mapping[str, Mapping[str, Any]],
    category_order: list[str],
) -> dict[str, Any]:
    details = _mapping(skill_result, "skill assessment")
    unknown = details.get("unknown_unassessed_requirements")
    if not isinstance(unknown, list):
        raise CareerRoadmapDataError("Skill Alignment returned malformed unassessed requirements.")
    groups = _required_groups(role)

    individual_ids: list[str] = []
    complementary_ids: dict[str, list[str]] = defaultdict(list)
    alternative_requests: dict[str, Mapping[str, Any]] = {}
    stack_request: Mapping[str, Any] | None = None
    for item_value in unknown:
        item = _mapping(item_value, "unassessed requirement")
        kind = item.get("kind")
        if kind == "skill":
            individual_ids.append(item.get("skill_id"))
        elif kind == "complementary_skill":
            group_id = item.get("group_id")
            if isinstance(group_id, str):
                complementary_ids[group_id].append(item.get("skill_id"))
        elif kind == "alternative_group":
            group_id = item.get("group_id")
            if isinstance(group_id, str):
                alternative_requests[group_id] = item
        elif kind == "compatible_stack_selection":
            stack_request = item

    individual_by_category_importance: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for skill_id in individual_ids:
        skill = _skill_reference(skills_by_id, skill_id)
        individual_by_category_importance[(skill["category_id"], skill["importance"])].append(skill)
    individual_groups = []
    for category_id in category_order:
        for importance in _IMPORTANCE_ORDER:
            member_skills = individual_by_category_importance.get((category_id, importance), [])
            if member_skills:
                individual_groups.append({
                    "category_id": category_id,
                    "category_name": member_skills[0]["category_name"],
                    "importance": importance,
                    "skills": member_skills,
                })

    complementary_groups = []
    for group_id, skill_ids in complementary_ids.items():
        group = groups.get(group_id)
        if group is None or group.get("relationship") != "complementary" or group.get("applicability") != "required":
            raise CareerRoadmapDataError("A complementary requirement does not match the role catalogue.")
        complementary_groups.append({
            "name": group.get("name", group_id),
            "skills_to_explore": [_skill_reference(skills_by_id, skill_id) for skill_id in dict.fromkeys(skill_ids)],
        })

    alternative_results = details.get("alternative_groups", [])
    if not isinstance(alternative_results, list):
        raise CareerRoadmapDataError("Skill Alignment returned malformed alternative-group details.")
    alternatives_by_id = {
        item.get("group_id"): item
        for value in alternative_results
        if isinstance(value, Mapping)
        for item in [value]
        if isinstance(item.get("group_id"), str)
    }
    alternative_groups = []
    for group_id, request in alternative_requests.items():
        group = groups.get(group_id)
        evaluated = alternatives_by_id.get(group_id)
        if (
            group is None or group.get("relationship") != "alternatives"
            or group.get("applicability") != "required" or evaluated is None
        ):
            raise CareerRoadmapDataError("An alternative requirement does not match the role catalogue.")
        minimum = group.get("minimum_options_to_learn")
        demonstrated = request.get("assessed_options")
        option_ids = evaluated.get("unknown_alternatives")
        if not isinstance(option_ids, list) or not isinstance(minimum, int) or not isinstance(demonstrated, int):
            raise CareerRoadmapDataError("Alternative requirement details are malformed.")
        alternative_groups.append({
            "name": group.get("name", group_id),
            "minimum_options_to_learn": minimum,
            "options_already_demonstrated": demonstrated,
            "additional_options_to_explore": max(0, minimum - demonstrated),
            "options": [_skill_reference(skills_by_id, skill_id) for skill_id in option_ids],
        })

    stack_groups = []
    if stack_request is not None:
        stack_requirement = role.get("technology_stack_requirement")
        stack_details = _mapping(details.get("compatible_stacks", {}), "compatible stack results")
        options_result = stack_details.get("options")
        unassessed_ids = stack_request.get("unassessed_stack_ids")
        minimum = stack_request.get("minimum_options_to_learn")
        demonstrated_options = stack_request.get("assessed_options")
        if (
            not isinstance(stack_requirement, Mapping)
            or stack_requirement.get("applicability") != "required"
            or not isinstance(options_result, list)
            or not isinstance(unassessed_ids, list)
            or not isinstance(minimum, int)
            or not isinstance(demonstrated_options, int)
        ):
            raise CareerRoadmapDataError("Compatible-stack requirement details are malformed.")
        stacks_by_id = {
            group.get("id"): group for group in groups.values()
            if group.get("relationship") == "compatible_stack" and isinstance(group.get("id"), str)
        }
        results_by_id = {
            item.get("group_id"): item for item in options_result
            if isinstance(item, Mapping) and isinstance(item.get("group_id"), str)
        }
        options = []
        for stack_id in unassessed_ids:
            stack = stacks_by_id.get(stack_id)
            result = results_by_id.get(stack_id)
            if stack is None or result is None:
                raise CareerRoadmapDataError("A required stack option is missing from the role catalogue.")
            matched_ids = result.get("matched_skill_ids", [])
            unknown_ids = result.get("unknown_skill_ids", [])
            if not isinstance(matched_ids, list) or not isinstance(unknown_ids, list):
                raise CareerRoadmapDataError("A required stack option has malformed skill evidence.")
            options.append({
                "name": stack.get("name", stack_id),
                "skills_already_demonstrated": [_skill_reference(skills_by_id, skill_id) for skill_id in matched_ids],
                "skills_to_explore": [_skill_reference(skills_by_id, skill_id) for skill_id in unknown_ids],
            })
        stack_groups.append({
            "minimum_options_to_learn": minimum,
            "options_already_demonstrated": demonstrated_options,
            "additional_options_to_explore": max(0, minimum - demonstrated_options),
            "options": options,
        })

    excluded_conditional = details.get("excluded_conditional_requirements", [])
    if not isinstance(excluded_conditional, list):
        raise CareerRoadmapDataError("Skill Alignment returned malformed conditional exclusions.")
    notes = [
        "Only applicable requirements not demonstrated by structured profile evidence are suggested.",
        "Optional requirements are not presented as requirements to learn.",
    ]
    if excluded_conditional:
        notes.append("Conditional topics are omitted because the saved profile does not establish whether their conditions apply.")

    return {
        "individual_requirements": individual_groups,
        "complementary_requirements": complementary_groups,
        "alternative_requirements": alternative_groups,
        "compatible_stack_requirements": stack_groups,
        "note": " ".join(notes),
    }


def _existing_skills(
    role: Mapping[str, Any],
    skill_result: Mapping[str, Any],
    project_result: Mapping[str, Any],
    skills_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    conditional_skill_ids = {
        skill_id
        for group in _required_groups(role).values()
        if group.get("applicability") == "conditional"
        for skill_id in group.get("skill_ids", [])
    }
    matched_ids = [
        skill_id for skill_id in _matched_skill_ids(skill_result, skills_by_id)
        if skill_id not in conditional_skill_ids
    ]
    category_results = project_result.get("categories")
    if not isinstance(category_results, Mapping):
        raise CareerRoadmapDataError("Projects and experience scorer returned malformed categories.")

    structured = []
    for category_id, label in _EVIDENCE_CATEGORIES.items():
        category = category_results.get(category_id)
        if category is None:
            continue
        category = _mapping(category, "structured evidence category")
        values = category.get("matched_role_skills", [])
        if not isinstance(values, list):
            raise CareerRoadmapDataError("Projects and experience scorer returned malformed matched skills.")
        skill_ids = []
        for value in values:
            item = _mapping(value, "structured matched skill")
            skill_id = item.get("id")
            _skill_reference(skills_by_id, skill_id)
            if skill_id in conditional_skill_ids:
                continue
            if skill_id not in skill_ids:
                skill_ids.append(skill_id)
        if skill_ids:
            structured.append({
                "source_category": category_id,
                "source_label": label,
                "relevant_skills": [_skill_reference(skills_by_id, skill_id) for skill_id in skill_ids],
            })

    certification_category = _mapping(category_results.get("certifications", {}), "certification evidence")
    credentials = certification_category.get("eligible_credentials", [])
    if not isinstance(credentials, list):
        raise CareerRoadmapDataError("Projects and experience scorer returned malformed eligible credentials.")
    eligible_certifications = []
    for credential_value in credentials:
        credential = _mapping(credential_value, "eligible certification")
        skill_ids = credential.get("mapped_evaluated_skill_ids", [])
        if not isinstance(skill_ids, list):
            raise CareerRoadmapDataError("Eligible certification contains malformed skill mappings.")
        eligible_certifications.append({
            "name": credential.get("name", ""),
            "issuing_organization": credential.get("issuing_organization", ""),
            "credential_type": credential.get("credential_type", "unknown"),
            "relevant_skills": [_skill_reference(skills_by_id, skill_id) for skill_id in skill_ids],
        })

    return {
        "demonstrated_technical_skills": [_skill_reference(skills_by_id, skill_id) for skill_id in matched_ids],
        "structured_project_and_experience": structured,
        "eligible_certifications": eligible_certifications,
        "note": "Skills come from the profile's technical-skills field. Project and experience matches use structured technology fields; certification matches follow the existing verified, scoring-eligible catalogue rules. No proficiency level is inferred.",
    }


def _learning_guidance(skills_to_explore: Mapping[str, Any]) -> dict[str, list[str]]:
    catalogue_supported = [
        "Skill categories and importance labels follow the selected role's career catalogue.",
    ]
    if skills_to_explore["alternative_requirements"]:
        catalogue_supported.append("Alternative technologies are choices; explore at least the stated minimum rather than treating every option as required.")
    if skills_to_explore["compatible_stack_requirements"]:
        catalogue_supported.append("Keep each compatible technology stack together; do not combine incomplete parts from different stacks.")
    if "Conditional topics are omitted" in skills_to_explore["note"]:
        catalogue_supported.append("Conditional topics are not suggested when the saved profile cannot establish that they apply.")
    return {
        "catalogue_supported": catalogue_supported,
        "general_guidance": [
            "Use the suggested topics as a starting point and choose what is most relevant to your interests.",
            "The catalogue does not define prerequisites, a required learning order, courses, durations, or external resources.",
        ],
    }


def build_career_roadmap(profile: Any, role_id: str) -> dict[str, Any]:
    """Build a roadmap using canonical catalogue data and existing evidence scorers."""
    if not isinstance(role_id, str) or not role_id.strip():
        raise InvalidCareerRoleError("Career role was not found.")

    knowledge_base = load_knowledge_base()
    if not isinstance(knowledge_base, Mapping):
        raise CareerRoadmapDataError("Career knowledge base is unavailable.")
    role = _canonical_role(knowledge_base, role_id)
    skills_by_id, category_order = _role_skills(role)

    skill_output = skill_assessment.score_skill_alignment(profile, knowledge_base=knowledge_base)
    skill_roles = skill_output.get("roles")
    if not isinstance(skill_roles, list):
        raise CareerRoadmapDataError("Skill Alignment returned no role results.")
    skill_matches = [item for item in skill_roles if isinstance(item, Mapping) and item.get("role_id") == role_id]
    if len(skill_matches) != 1:
        raise CareerRoadmapDataError("Skill Alignment did not return exactly one selected role result.")
    skill_result = skill_matches[0]

    project_result = career_intelligence.score_projects_experience(profile, role)
    if not isinstance(project_result, Mapping) or project_result.get("role_id") != role_id:
        raise CareerRoadmapDataError("Projects and experience scorer returned an inconsistent role result.")

    responsibilities = _string_list(role.get("typical_responsibilities"), "career responsibilities")
    related_roles_by_id = {
        item.get("id"): item.get("name")
        for item in knowledge_base["roles"]
        if isinstance(item, Mapping)
    }
    related_ids = _string_list(role.get("related_roles", []), "related career roles")
    related_roles = []
    for related_id in related_ids:
        related_name = related_roles_by_id.get(related_id)
        if not isinstance(related_name, str):
            raise CareerRoadmapDataError("Career role references an unknown related role.")
        related_roles.append({"role_id": related_id, "name": related_name})
    progression_paths = role.get("career_progression_paths", [])
    if not isinstance(progression_paths, list) or any(
        not isinstance(path, list) or any(not isinstance(item, str) for item in path)
        for path in progression_paths
    ):
        raise CareerRoadmapDataError("Career role contains malformed progression paths.")
    project_ideas = _string_list(role.get("relevant_project_types", []), "practice project ideas")

    skills_to_explore = _build_skills_to_explore(role, skill_result, skills_by_id, category_order)
    return {
        "career_overview": {
            "role_id": role_id,
            "name": role["name"],
            "domain": role["career_domain"],
            "description": role["description"],
            "main_responsibilities": responsibilities,
        },
        "your_existing_skills": _existing_skills(role, skill_result, project_result, skills_by_id),
        "skills_to_explore": skills_to_explore,
        "learning_guidance": _learning_guidance(skills_to_explore),
        "practice_projects": {
            "ideas": project_ideas,
            "note": "These are curated project ideas to try, not projects recorded as completed in your profile. No completion criteria or timelines are provided.",
        },
        "career_growth": {
            "related_roles": related_roles,
            "progression_paths": progression_paths,
            "note": "These are possible career directions from the catalogue, not a guarantee of progression.",
        },
    }
