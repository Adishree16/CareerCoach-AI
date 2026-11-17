"""Deterministic career-role Skill Alignment scoring."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any

from app.services.career_knowledge_graph import (
    build_career_graph,
    load_knowledge_base,
    validate_career_graph,
)
from app.services.career_knowledge_queries import (
    get_compatible_stacks,
    get_role_skill_groups,
    get_role_skills,
)


SKILL_ALIGNMENT_MAX_POINTS = 43.75
_IMPORTANCE_POINTS = {
    "core": 4,
    "important": 3,
    "specialised": 2,
    "optional": 0,
}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        result = model_dump(by_alias=False)
        if isinstance(result, Mapping):
            return result
    return {}


def _field(mapping: Mapping[str, Any], snake: str, camel: str, default: Any = None) -> Any:
    if snake in mapping:
        return mapping[snake]
    return mapping.get(camel, default)


def _normalize_skill_name(value: str) -> str:
    """Normalize only Unicode, case and whitespace; do not fuzzy-match."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _submitted_skills(profile: Any) -> list[str]:
    profile_data = _as_mapping(profile)
    career_skills = _as_mapping(
        _field(profile_data, "career_skills", "careerSkills", profile_data)
    )
    skills = _field(career_skills, "technical_skills", "technicalSkills", [])
    if skills is None:
        return []
    if not isinstance(skills, Sequence) or isinstance(skills, (str, bytes)):
        raise ValueError("Profile technicalSkills must be a list of skill names.")
    if any(not isinstance(skill, str) for skill in skills):
        raise ValueError("Profile technicalSkills must contain only skill names.")
    # Empty values are not valid profile entries; ignore them defensively here.
    return list(dict.fromkeys(skill for skill in skills if _normalize_skill_name(skill)))


def _build_name_lookup(knowledge_base: Mapping[str, Any]) -> dict[str, str]:
    """Build an exact canonical-name/approved-alias lookup, dropping ambiguities."""
    candidates: dict[str, set[str]] = {}
    roles = knowledge_base.get("roles")
    if not isinstance(roles, list):
        raise ValueError("Career knowledge base does not contain a roles list.")

    for role in roles:
        if not isinstance(role, Mapping):
            raise ValueError("Career knowledge base contains a malformed role.")
        skill_categories = role.get("skills", {})
        if not isinstance(skill_categories, Mapping):
            raise ValueError("Career role skills must be organized by category.")
        for entries in skill_categories.values():
            if not isinstance(entries, list):
                raise ValueError("Career role skill category must contain a list.")
            for skill in entries:
                if not isinstance(skill, Mapping):
                    raise ValueError("Career knowledge base contains a malformed skill.")
                skill_id = skill.get("id")
                name = skill.get("name")
                if not isinstance(skill_id, str) or not skill_id or not isinstance(name, str) or not name:
                    raise ValueError("Career knowledge base skill is missing its ID or name.")
                names = [name]
                for alias_field in ("aliases", "synonyms"):
                    aliases = skill.get(alias_field, [])
                    if aliases is None:
                        continue
                    if not isinstance(aliases, list) or any(not isinstance(item, str) for item in aliases):
                        raise ValueError(f"Skill {skill_id!r} has malformed {alias_field} metadata.")
                    names.extend(aliases)
                for candidate in names:
                    normalized = _normalize_skill_name(candidate)
                    if normalized:
                        candidates.setdefault(normalized, set()).add(skill_id)

    # Do not award credit when a name or alias maps to multiple canonical IDs.
    return {
        normalized: next(iter(skill_ids))
        for normalized, skill_ids in candidates.items()
        if len(skill_ids) == 1
    }


def _importance_points(skill: Mapping[str, Any]) -> int:
    importance = skill.get("importance")
    if importance not in _IMPORTANCE_POINTS:
        raise ValueError(f"Career skill {skill.get('skill_id')!r} has unsupported importance.")
    return _IMPORTANCE_POINTS[importance]


def _top_points(skill_ids: Sequence[str], weights: Mapping[str, int], count: int) -> int:
    return sum(sorted((weights[skill_id] for skill_id in set(skill_ids)), reverse=True)[:count])


def _group_skill_ids(group: Mapping[str, Any]) -> list[str]:
    return [member["skill_id"] for member in group.get("skills", [])]


def _best_stack_union(
    stack_options: Sequence[Mapping[str, Any]],
    weights: Mapping[str, int],
    maximum_options: int,
    *,
    exact_options: bool = False,
) -> tuple[list[str], set[str], int]:
    """Choose intact stack options with the greatest distinct weighted union."""
    eligible = [option for option in stack_options if option["skill_ids"]]
    if not eligible or maximum_options < 1:
        return [], set(), 0
    sizes = [maximum_options] if exact_options else range(1, min(maximum_options, len(eligible)) + 1)
    best_ids: list[str] = []
    best_union: set[str] = set()
    best_points = -1
    for size in sizes:
        if size > len(eligible):
            continue
        for selected in combinations(eligible, size):
            union = set().union(*(option["skill_ids"] for option in selected))
            points = sum(weights[skill_id] for skill_id in union)
            ids = sorted(option["group_id"] for option in selected)
            if points > best_points or (points == best_points and ids < best_ids):
                best_ids, best_union, best_points = ids, union, points
    if best_points < 0:
        return [], set(), 0
    return best_ids, best_union, best_points


def _evaluate_role(
    profile_skill_ids: set[str],
    graph: Any,
    role_id: str,
    role_node: Mapping[str, Any],
) -> dict[str, Any]:
    role_skills = get_role_skills(graph, role_id)
    skills = {item["skill_id"]: item for item in role_skills}
    weights = {skill_id: _importance_points(skill) for skill_id, skill in skills.items()}
    matched_ids = profile_skill_ids.intersection(skills)
    groups = get_role_skill_groups(graph, role_id)
    groups_by_id = {group["group_id"]: group for group in groups}

    earned_points = 0
    total_possible_points = 0
    counted_skill_ids: set[str] = set()
    grouped_skill_ids = {
        skill_id for group in groups for skill_id in _group_skill_ids(group)
    }
    unknown_requirements: list[dict[str, Any]] = []
    alternative_results: list[dict[str, Any]] = []
    complementary_results: list[dict[str, Any]] = []
    stack_results: list[dict[str, Any]] = []
    excluded_conditional: list[dict[str, Any]] = []
    excluded_optional: list[dict[str, Any]] = []
    overlap_exclusions: list[dict[str, str]] = []
    handled_ids: set[str] = set()

    # The role-level selection requirement controls which compatible stacks count.
    stack_data = get_compatible_stacks(graph, role_id)
    stack_requirement = stack_data["selection_requirement"]
    if isinstance(stack_requirement, Mapping) and stack_requirement.get("applicability") == "required":
        required_stack_ids = stack_requirement.get("stack_group_ids", [])
        minimum_stacks = stack_requirement.get("minimum_options_to_learn")
        if (
            not isinstance(required_stack_ids, list)
            or not isinstance(minimum_stacks, int)
            or minimum_stacks < 1
            or minimum_stacks > len(required_stack_ids)
        ):
            raise ValueError(f"Role {role_id!r} has an invalid compatible-stack requirement.")
        all_stack_groups = {item["group_id"]: item for item in stack_data["stacks"]}
        options: list[dict[str, Any]] = []
        possible_options: list[dict[str, Any]] = []
        for group_id in required_stack_ids:
            group = all_stack_groups.get(group_id)
            if group is None:
                raise ValueError(f"Role {role_id!r} references unknown stack {group_id!r}.")
            member_ids = list(dict.fromkeys(_group_skill_ids(group)))
            if any(skill_id not in skills for skill_id in member_ids):
                raise ValueError(f"Role {role_id!r} stack {group_id!r} references an unknown skill.")
            possible_options.append({"group_id": group_id, "skill_ids": set(member_ids)})
            matched_members = set(member_ids).intersection(matched_ids)
            complete = bool(member_ids) and len(matched_members) == len(member_ids)
            stack_results.append({
                "group_id": group_id,
                "skill_ids": member_ids,
                "matched_skill_ids": sorted(matched_members),
                "unknown_skill_ids": sorted(set(member_ids) - matched_ids),
                "status": "assessed_complete" if complete else "unassessed_incomplete",
            })
            if complete:
                options.append({"group_id": group_id, "skill_ids": set(member_ids)})
            # Stack members are only evaluated through their intact stack option.
            handled_ids.update(member_ids)

        _, possible_union, possible_capacity = _best_stack_union(
            possible_options, weights, minimum_stacks, exact_options=True
        )
        selected_stack_ids, assessed_union, assessed_capacity = _best_stack_union(
            options, weights, minimum_stacks
        )
        total_possible_points += possible_capacity
        earned_points += assessed_capacity
        counted_skill_ids.update(assessed_union)
        stack_results_summary = {
            "applicability": "required",
            "minimum_options_to_learn": minimum_stacks,
            "assessed_stack_ids": selected_stack_ids,
            "assessed_capacity_points": assessed_capacity,
            "earned_points": assessed_capacity,
            "total_possible_capacity_points": possible_capacity,
            "unknown_or_incomplete_stack_ids": sorted(
                set(required_stack_ids) - {option["group_id"] for option in options}
            ),
        }
        if options and len(options) < minimum_stacks:
            unknown_requirements.append({
                "kind": "compatible_stack_selection",
                "minimum_options_to_learn": minimum_stacks,
                "assessed_options": len(options),
                "unassessed_stack_ids": stack_results_summary["unknown_or_incomplete_stack_ids"],
            })
        elif not options:
            unknown_requirements.append({
                "kind": "compatible_stack_selection",
                "minimum_options_to_learn": minimum_stacks,
                "assessed_options": 0,
                "unassessed_stack_ids": stack_results_summary["unknown_or_incomplete_stack_ids"],
            })
    else:
        stack_results_summary = {
            "applicability": (stack_requirement or {}).get("applicability", "not_required"),
            "minimum_options_to_learn": (stack_requirement or {}).get("minimum_options_to_learn"),
            "assessed_stack_ids": [],
            "assessed_capacity_points": 0,
            "earned_points": 0,
            "total_possible_capacity_points": 0,
            "unknown_or_incomplete_stack_ids": [],
        }
        for group in stack_data["stacks"]:
            handled_ids.update(_group_skill_ids(group))

    # Conditional requirements are excluded because the current profile has no
    # structured data that can establish their catalogue conditions.
    for group in groups:
        if group["applicability"] == "conditional":
            excluded_conditional.append({
                "group_id": group["group_id"],
                "relationship": group["relationship"],
                "condition": group.get("condition"),
                "skill_ids": _group_skill_ids(group),
                "reason": "Applicability cannot be established from technicalSkills.",
            })
            handled_ids.update(_group_skill_ids(group))

    # Required alternative groups are scored collectively, with the explicit
    # capacity rule from the approved specification.
    for group in groups:
        if group["relationship"] != "alternatives":
            continue
        member_ids = list(dict.fromkeys(_group_skill_ids(group)))
        if group["applicability"] != "required":
            excluded_optional.append({
                "group_id": group["group_id"],
                "relationship": group["relationship"],
                "skill_ids": member_ids,
                "reason": "Optional alternative groups do not create required gaps.",
            })
            handled_ids.update(member_ids)
            continue
        minimum = group.get("minimum_options_to_learn")
        if not isinstance(minimum, int) or minimum < 1 or minimum > len(member_ids):
            raise ValueError(f"Alternative group {group['group_id']!r} has an invalid minimum.")
        eligible = [skill_id for skill_id in member_ids if skill_id not in handled_ids]
        duplicates = [skill_id for skill_id in member_ids if skill_id in handled_ids]
        overlap_exclusions.extend(
            {"group_id": group["group_id"], "skill_id": skill_id} for skill_id in duplicates
        )
        known = [skill_id for skill_id in eligible if skill_id in matched_ids]
        group_capacity = _top_points(known, weights, minimum)
        full_capacity = _top_points(eligible, weights, minimum)
        earned_points += _top_points(known, weights, minimum)
        total_possible_points += full_capacity
        chosen_for_points = sorted(known, key=lambda item: (-weights[item], item))[:minimum]
        counted_skill_ids.update(chosen_for_points)
        alternative_results.append({
            "group_id": group["group_id"],
            "group_name": group["group_name"],
            "minimum_options_to_learn": minimum,
            "assessed_alternatives": [
                {"skill_id": item, "skill_name": skills[item]["skill_name"], "importance": skills[item]["importance"], "points": weights[item]}
                for item in known
            ],
            "unknown_alternatives": sorted(set(eligible) - set(known)),
            "credited_alternative_ids": chosen_for_points,
            "assessed_capacity_points": group_capacity,
            "earned_points": _top_points(known, weights, minimum),
            "total_possible_capacity_points": full_capacity,
            "status": "assessed" if known else "unassessed_unknown",
        })
        handled_ids.update(member_ids)
        if len(known) < minimum:
            unknown_requirements.append({
                "kind": "alternative_group",
                "group_id": group["group_id"],
                "minimum_options_to_learn": minimum,
                "assessed_options": len(known),
                "unknown_alternatives": sorted(set(eligible) - set(known)),
            })

    # Complementary requirements remain separate member skills.
    for group in groups:
        if group["relationship"] != "complementary":
            continue
        member_ids = list(dict.fromkeys(_group_skill_ids(group)))
        if group["applicability"] != "required":
            excluded_optional.append({
                "group_id": group["group_id"],
                "relationship": group["relationship"],
                "skill_ids": member_ids,
                "reason": "Complementary group is not applicable as a universal requirement.",
            })
            handled_ids.update(member_ids)
            continue
        unique_members = [skill_id for skill_id in member_ids if skill_id not in handled_ids]
        duplicates = [skill_id for skill_id in member_ids if skill_id in handled_ids]
        overlap_exclusions.extend(
            {"group_id": group["group_id"], "skill_id": skill_id} for skill_id in duplicates
        )
        known = [skill_id for skill_id in unique_members if skill_id in matched_ids]
        group_capacity = sum(weights[item] for item in known)
        full_capacity = sum(weights[item] for item in unique_members)
        earned_points += group_capacity
        total_possible_points += full_capacity
        counted_skill_ids.update(known)
        complementary_results.append({
            "group_id": group["group_id"],
            "group_name": group["group_name"],
            "applicability": group["applicability"],
            "assessed_skill_ids": known,
            "unknown_skill_ids": sorted(set(unique_members) - set(known)),
            "assessed_capacity_points": group_capacity,
            "earned_points": group_capacity,
            "total_possible_capacity_points": full_capacity,
        })
        handled_ids.update(member_ids)
        for skill_id in sorted(set(unique_members) - set(known)):
            unknown_requirements.append({
                "kind": "complementary_skill",
                "group_id": group["group_id"],
                "skill_id": skill_id,
                "skill_name": skills[skill_id]["skill_name"],
            })

    # All remaining group members are optional or otherwise non-universal.
    for group in groups:
        if group["group_id"] in {item["group_id"] for item in excluded_conditional}:
            continue
        if group["relationship"] in {"alternatives", "complementary"} and group["applicability"] == "required":
            continue
        handled_ids.update(_group_skill_ids(group))

    # Ungrouped non-optional role skills are individual requirements.
    for skill_id, skill in skills.items():
        if skill_id in grouped_skill_ids or skill_id in handled_ids or weights[skill_id] == 0:
            continue
        weight = weights[skill_id]
        total_possible_points += weight
        if skill_id in matched_ids:
            earned_points += weight
            counted_skill_ids.add(skill_id)
        else:
            unknown_requirements.append({
                "kind": "skill",
                "skill_id": skill_id,
                "skill_name": skill["skill_name"],
                "importance": skill["importance"],
            })

    for group in groups:
        if group["relationship"] == "complementary" and group["applicability"] == "required":
            continue
        if group["relationship"] == "alternatives" and group["applicability"] == "required":
            continue
        if group["relationship"] == "compatible_stack":
            continue
        if group["applicability"] == "conditional":
            continue
        if group["applicability"] == "optional":
            if group["relationship"] not in {"alternatives", "complementary"}:
                excluded_optional.append({
                    "group_id": group["group_id"],
                    "relationship": group["relationship"],
                    "skill_ids": _group_skill_ids(group),
                    "reason": "Optional groups do not create required gaps.",
                })

    score_available = total_possible_points > 0
    score = (
        round(min(SKILL_ALIGNMENT_MAX_POINTS, max(0.0, SKILL_ALIGNMENT_MAX_POINTS * earned_points / total_possible_points)), 4)
        if score_available else None
    )
    coverage = (
        round(min(100.0, max(0.0, 100.0 * earned_points / total_possible_points)), 4)
        if score_available and total_possible_points > 0 else None
    )
    matched_skills = [
        {
            "skill_id": skill_id,
            "skill_name": skills[skill_id]["skill_name"],
            "category": skills[skill_id]["category"],
            "importance": skills[skill_id]["importance"],
            "importance_points": weights[skill_id],
            "contributes_to_score": skill_id in counted_skill_ids,
        }
        for skill_id in sorted(matched_ids, key=lambda item: (skills[item]["skill_name"].casefold(), item))
    ]

    return {
        "role_id": role_id,
        "role_name": role_node["role_name"],
        "career_domain": role_node["domain"],
        "maximum_points": SKILL_ALIGNMENT_MAX_POINTS,
        "score": score,
        "alignment_percentage": round(score / SKILL_ALIGNMENT_MAX_POINTS * 100, 4) if score is not None else None,
        "evidence_coverage_percentage": coverage,
        "score_available": score_available,
        "score_status": "scored" if score_available else "unavailable_no_applicable_requirements",
        "earned_evaluated_points": earned_points if score_available else None,
        "available_evaluated_points": total_possible_points if score_available else None,
        "total_applicable_requirement_points": total_possible_points if total_possible_points > 0 else None,
        "matched_skills": matched_skills,
        "matched_skill_ids": sorted(matched_ids),
        "credited_skill_ids": sorted(counted_skill_ids),
        "unknown_unassessed_requirements": unknown_requirements,
        "explicitly_not_known_requirements": [],
        "alternative_groups": alternative_results,
        "complementary_groups": complementary_results,
        "compatible_stacks": {
            **stack_results_summary,
            "options": stack_results,
        },
        "excluded_conditional_requirements": excluded_conditional,
        "excluded_optional_requirements": excluded_optional,
        "overlap_exclusions": overlap_exclusions,
    }


def score_skill_alignment(
    profile: Any,
    *,
    knowledge_base: Mapping[str, Any] | None = None,
    graph: Any = None,
) -> dict[str, Any]:
    """Evaluate structured technical skills against every catalogued career role."""
    source = knowledge_base if knowledge_base is not None else load_knowledge_base()
    if not isinstance(source, Mapping):
        raise ValueError("Career knowledge base must be an object.")
    career_graph = graph if graph is not None else build_career_graph(dict(source))
    validation = validate_career_graph(career_graph, dict(source))
    if not validation.get("valid"):
        raise ValueError("Career knowledge graph failed validation.")

    roles = source.get("roles")
    if not isinstance(roles, list):
        raise ValueError("Career knowledge base does not contain a roles list.")
    expected_count = source.get("total_roles", len(roles))
    role_nodes = sorted(
        (
            (node_id, attrs)
            for node_id, attrs in career_graph.nodes(data=True)
            if attrs.get("node_type") == "career_role"
        ),
        key=lambda item: (item[1].get("role_name", "").casefold(), item[1].get("role_id", "")),
    )
    if len(role_nodes) != len(roles) or len(role_nodes) != expected_count:
        raise ValueError("Career knowledge graph does not contain every catalogued role.")

    name_lookup = _build_name_lookup(source)
    known_skill_ids: set[str] = set()
    for submitted in _submitted_skills(profile):
        skill_id = name_lookup.get(_normalize_skill_name(submitted))
        if skill_id is not None:
            known_skill_ids.add(skill_id)

    role_results = [
        _evaluate_role(known_skill_ids, career_graph, attrs["role_id"], attrs)
        for _, attrs in role_nodes
    ]
    return {
        "maximum_points": SKILL_ALIGNMENT_MAX_POINTS,
        "formula": "43.75 × matched applicable weighted points / total applicable weighted requirement points",
        "skill_state_policy": {
            "listed_skills": "self_reported_known",
            "unlisted_skills": "unknown_unassessed",
            "explicit_not_known_supported": False,
            "proficiency_assessed": False,
        },
        "roles_evaluated": len(role_results),
        "roles": role_results,
    }
