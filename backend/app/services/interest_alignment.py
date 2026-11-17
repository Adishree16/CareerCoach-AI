"""Deterministic Interest Alignment scoring for curated career interests."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any


_DATA_DIRECTORY = Path(__file__).resolve().parents[1] / "data"
_CAREER_INTEREST_PATH = _DATA_DIRECTORY / "career_interest_mappings.json"
_ROLE_ACTIVITY_PATH = _DATA_DIRECTORY / "role_activity_mappings.json"
_KNOWLEDGE_BASE_PATH = _DATA_DIRECTORY / "career_knowledge_base.json"

CAREER_INTEREST_ORIGINAL_ALLOCATION = 15.0
PREFERRED_WORK_ACTIVITY_ORIGINAL_ALLOCATION = 10.0
INTEREST_ALIGNMENT_MAX_POINTS = 25.0
_MAPPING_STATUSES = {"mapped", "partial", "needs_review", "unmapped", "neutral"}
_ACTIVITY_RELATIONSHIP_STATUSES = {
    "exact_match",
    "partial_match",
    "confirmed_mismatch",
    "neutral",
    "unavailable",
}
_ACTIVITY_REVIEW_STATUSES = {"needs_review", "approved", "neutral"}
_ACTIVITY_SCORABLE_STATUSES = {"exact_match", "partial_match", "confirmed_mismatch"}
_PREFERRED_ACTIVITY_LABELS = {
    "building_applications_and_software": "Building applications and software",
    "solving_technical_problems": "Solving technical problems",
    "working_with_data": "Working with data",
    "working_with_ai_and_automation": "Working with AI and automation",
    "designing_user_experiences": "Designing user experiences",
    "managing_cloud_infrastructure": "Managing cloud infrastructure",
    "testing_software_and_ensuring_quality": "Testing software and ensuring quality",
    "planning_and_coordinating_projects": "Planning and coordinating projects",
    "researching_and_exploring_new_technologies": "Researching and exploring new technologies",
    "communicating_with_clients_and_teams": "Communicating with clients and teams",
    "not_sure_yet": "Not Sure Yet",
}


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as source:
            result = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} could not be loaded.") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{description} has an invalid structure.")
    return result


def load_interest_catalogues() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load the interest mappings, activity mappings, and career knowledge base."""
    return (
        _read_json(_CAREER_INTEREST_PATH, "Career interest mapping catalogue"),
        _read_json(_ROLE_ACTIVITY_PATH, "Preferred work activity mapping catalogue"),
        _read_json(_KNOWLEDGE_BASE_PATH, "Career knowledge base"),
    )


def _validate_knowledge_base(knowledge_base: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], dict[str, str]]:
    domains = knowledge_base.get("domains")
    roles = knowledge_base.get("roles")
    if not isinstance(domains, list) or not isinstance(roles, list) or not roles:
        raise ValueError("Career knowledge base must contain domains and roles lists.")

    domain_names: dict[str, str] = {}
    role_domains: dict[str, str] = {}
    for domain in domains:
        if not isinstance(domain, Mapping):
            raise ValueError("Career knowledge base contains a malformed domain.")
        domain_id = domain.get("id")
        role_ids = domain.get("role_ids")
        if not isinstance(domain_id, str) or not domain_id or domain_id in domain_names:
            raise ValueError("Career knowledge base contains an invalid or duplicate domain ID.")
        if not isinstance(domain.get("name"), str) or not domain["name"]:
            raise ValueError(f"Career domain {domain_id!r} has no valid name.")
        if not isinstance(role_ids, list) or any(not isinstance(item, str) or not item for item in role_ids):
            raise ValueError(f"Career domain {domain_id!r} has malformed role membership.")
        domain_names[domain_id] = domain["name"]
        for role_id in role_ids:
            if role_id in role_domains:
                raise ValueError(f"Career role {role_id!r} belongs to multiple domains.")
            role_domains[role_id] = domain_id

    seen_role_ids: set[str] = set()
    for role in roles:
        if not isinstance(role, Mapping):
            raise ValueError("Career knowledge base contains a malformed role.")
        role_id = role.get("id")
        if not isinstance(role_id, str) or not role_id or role_id in seen_role_ids:
            raise ValueError("Career knowledge base contains an invalid or duplicate role ID.")
        seen_role_ids.add(role_id)
        domain_id = role_domains.get(role_id)
        if domain_id is None:
            raise ValueError(f"Career role {role_id!r} has no domain membership.")
        if role.get("career_domain") != domain_names[domain_id]:
            raise ValueError(f"Career role {role_id!r} conflicts with its domain membership.")

    unknown_members = set(role_domains) - seen_role_ids
    if unknown_members:
        raise ValueError("Career domain membership references an unknown role.")
    return roles, role_domains


def _validate_catalogue(catalogue: Mapping[str, Any], field: str, description: str) -> list[Mapping[str, Any]]:
    entries = catalogue.get(field)
    if not isinstance(entries, list):
        raise ValueError(f"{description} must contain a {field} list.")
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError(f"{description} contains a malformed entry.")
        entry_id = entry.get("id")
        label = entry.get("label")
        status = entry.get("mapping_status")
        if not isinstance(entry_id, str) or not entry_id or entry_id in seen_ids:
            raise ValueError(f"{description} contains an invalid or duplicate entry ID.")
        if (
            not isinstance(label, str)
            or not label.strip()
            or not isinstance(status, str)
            or status not in _MAPPING_STATUSES
        ):
            raise ValueError(f"{description} contains an entry with an invalid label or status.")
        seen_ids.add(entry_id)
        weight = entry.get("partial_match_weight")
        if weight is not None and (
            isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 < weight <= 1
        ):
            raise ValueError(f"{description} contains an invalid partial-match weight.")
    return entries


def _preference_value(preferences: Any, snake_name: str, camel_name: str) -> Any:
    if not isinstance(preferences, Mapping):
        return None
    if snake_name in preferences:
        return preferences[snake_name]
    return preferences.get(camel_name)


def _resolve_interest(value: Any, options: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any] | None, str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "missing"
    if not isinstance(value, str):
        return None, "unknown"
    matches = [option for option in options if value == option.get("id") or value == option.get("label")]
    if len(matches) != 1:
        return None, "unknown"
    option = matches[0]
    status = option["mapping_status"]
    if status == "neutral":
        return option, "neutral"
    if status == "mapped":
        return option, "mapped"
    return option, "unmapped" if status == "unmapped" else "unreviewed"


def _resolve_activity(value: Any, activities: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any] | None, str]:
    """Resolve a stable activity ID or exact UI label without guessing."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "missing"
    if not isinstance(value, str):
        return None, "unknown"
    matches = [activity for activity in activities if value == activity.get("id") or value == activity.get("label")]
    if len(matches) != 1:
        return None, "unknown"
    activity = matches[0]
    if activity.get("mapping_status") == "neutral":
        return activity, "neutral"
    return activity, "selected"


def _approved_activity_relationship(
    activity: Mapping[str, Any] | None, role_id: str
) -> Mapping[str, Any] | None:
    """Return only a reviewed, scoring-eligible relationship for this role."""
    if activity is None:
        return None
    for relationship in activity["role_mappings"]:
        if (
            relationship["role_id"] == role_id
            and relationship["review_status"] == "approved"
            and relationship["status"] in _ACTIVITY_SCORABLE_STATUSES
        ):
            return relationship
    return None


def _validate_interest_domains(options: list[Mapping[str, Any]], domain_ids: set[str]) -> None:
    for option in options:
        mapped_domains = option.get("domain_ids")
        if not isinstance(mapped_domains, list) or any(not isinstance(item, str) for item in mapped_domains):
            raise ValueError(f"Career interest {option['id']!r} has malformed domain mappings.")
        if option["mapping_status"] == "mapped" and len(mapped_domains) != 1:
            raise ValueError(f"Mapped career interest {option['id']!r} must map to exactly one domain.")
        if any(item not in domain_ids for item in mapped_domains):
            raise ValueError(f"Career interest {option['id']!r} references an unknown domain.")


def _approved_activity_review_is_complete(relationship: Mapping[str, Any]) -> bool:
    references = relationship.get("evidence_references")
    if not isinstance(references, list) or not references:
        return False
    for reference in references:
        if not isinstance(reference, Mapping) or not all(
            isinstance(reference.get(field), str) and reference[field].strip()
            for field in ("source_id", "reference")
        ):
            return False

    reviewer = relationship.get("reviewer_identity")
    rationale = relationship.get("reviewer_rationale")
    reviewed_at = relationship.get("reviewed_at")
    if not isinstance(reviewer, str) or not reviewer.strip():
        return False
    if not isinstance(rationale, str) or not rationale.strip():
        return False
    if not isinstance(reviewed_at, str) or not reviewed_at.strip():
        return False
    try:
        timestamp = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return timestamp.tzinfo is not None and timestamp.utcoffset() is not None


def _validate_activity_partial_weight(relationship: Mapping[str, Any]) -> None:
    weight = relationship.get("partial_match_weight")
    if (
        isinstance(weight, bool)
        or not isinstance(weight, (int, float))
        or not math.isfinite(weight)
        or not 0 < weight < 1
    ):
        raise ValueError("Partial-match activity relationships require a numeric weight greater than 0 and less than 1.")


def _validate_activity_catalogue(activities: list[Mapping[str, Any]], role_names: Mapping[str, str]) -> None:
    activity_ids = {activity.get("id") for activity in activities}
    if activity_ids != set(_PREFERRED_ACTIVITY_LABELS):
        raise ValueError("Preferred work activity catalogue does not contain the approved activity IDs.")

    seen_labels: set[str] = set()
    for activity in activities:
        label = activity.get("label")
        if label != _PREFERRED_ACTIVITY_LABELS.get(activity.get("id")):
            raise ValueError(f"Work activity {activity.get('id')!r} has a non-canonical label.")
        if not isinstance(label, str) or not label.strip() or label in seen_labels:
            raise ValueError("Preferred work activity catalogue contains an invalid or duplicate label.")
        seen_labels.add(label)

        mapped_roles = activity.get("role_ids")
        if not isinstance(mapped_roles, list) or any(not isinstance(item, str) for item in mapped_roles):
            raise ValueError(f"Work activity {activity['id']!r} has malformed role mappings.")
        if mapped_roles:
            raise ValueError(f"Work activity {activity['id']!r} cannot use legacy role mappings.")

        is_neutral = activity.get("mapping_status") == "neutral"
        expected_legacy_status = "neutral" if is_neutral else "unmapped"
        if activity.get("mapping_status") != expected_legacy_status:
            raise ValueError(f"Work activity {activity['id']!r} has conflicting legacy mapping status.")
        if is_neutral:
            if activity.get("neutral") is not True or activity.get("scoring_availability") != "unavailable":
                raise ValueError(f"Neutral work activity {activity['id']!r} has inconsistent neutral metadata.")
        elif activity.get("neutral") is True:
            raise ValueError(f"Non-neutral work activity {activity['id']!r} has conflicting neutral metadata.")
        if activity.get("partial_match_weight") is not None:
            raise ValueError(f"Work activity {activity['id']!r} cannot use a legacy partial-match weight.")
        if activity.get("source_references") != []:
            raise ValueError(f"Work activity {activity['id']!r} cannot use legacy evidence references.")

        relationships = activity.get("role_mappings")
        if not isinstance(relationships, list):
            raise ValueError(f"Work activity {activity['id']!r} must contain a role_mappings list.")
        if is_neutral and relationships:
            raise ValueError(f"Neutral work activity {activity['id']!r} cannot contain role relationships.")

        seen_role_ids: set[str] = set()
        for relationship in relationships:
            if not isinstance(relationship, Mapping):
                raise ValueError(f"Work activity {activity['id']!r} contains a malformed role relationship.")
            role_id = relationship.get("role_id")
            status = relationship.get("status")
            review_status = relationship.get("review_status")
            if (
                not isinstance(role_id, str)
                or role_id not in role_names
                or role_id in seen_role_ids
                or relationship.get("role_name") != role_names.get(role_id)
                or not isinstance(status, str)
                or status not in _ACTIVITY_RELATIONSHIP_STATUSES
                or not isinstance(review_status, str)
                or review_status not in _ACTIVITY_REVIEW_STATUSES
            ):
                raise ValueError(f"Work activity {activity['id']!r} contains an invalid or duplicate role relationship.")
            seen_role_ids.add(role_id)

            rationale = relationship.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError(f"Work activity {activity['id']!r}/{role_id!r} requires a rationale.")

            if status == "partial_match":
                _validate_activity_partial_weight(relationship)
            elif relationship.get("partial_match_weight") is not None:
                raise ValueError("Only partial-match activity relationships may specify a partial-match weight.")

            if is_neutral or status == "neutral" or review_status == "neutral":
                raise ValueError("Neutral relationship metadata is only valid for the neutral activity, which has no role mappings.")

            evidence_references = relationship.get("evidence_references")
            if not isinstance(evidence_references, list) or any(
                not isinstance(reference, Mapping)
                or not isinstance(reference.get("source_id"), str)
                or not reference["source_id"].strip()
                or not isinstance(reference.get("reference"), str)
                or not reference["reference"].strip()
                for reference in evidence_references
            ):
                raise ValueError(f"Work activity {activity['id']!r}/{role_id!r} has malformed evidence references.")

            for metadata_field in ("reviewer_rationale", "reviewer_identity", "reviewed_at"):
                metadata_value = relationship.get(metadata_field)
                if metadata_value is not None and not isinstance(metadata_value, str):
                    raise ValueError(f"Work activity {activity['id']!r}/{role_id!r} has malformed review metadata.")

            if status in _ACTIVITY_SCORABLE_STATUSES and review_status == "approved":
                if not isinstance(relationship.get("rationale"), str) or not relationship["rationale"].strip():
                    raise ValueError(f"Approved relationship {activity['id']!r}/{role_id!r} lacks a rationale.")
                if not _approved_activity_review_is_complete(relationship):
                    raise ValueError(f"Approved relationship {activity['id']!r}/{role_id!r} lacks complete review evidence.")


def score_interest_alignment(
    preferences: Any,
    *,
    career_interest_catalogue: Mapping[str, Any] | None = None,
    role_activity_catalogue: Mapping[str, Any] | None = None,
    knowledge_base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score roles from reviewed career-interest and activity mappings only."""
    if career_interest_catalogue is None or role_activity_catalogue is None or knowledge_base is None:
        loaded_interest, loaded_activity, loaded_knowledge_base = load_interest_catalogues()
        if career_interest_catalogue is None:
            career_interest_catalogue = loaded_interest
        if role_activity_catalogue is None:
            role_activity_catalogue = loaded_activity
        if knowledge_base is None:
            knowledge_base = loaded_knowledge_base

    roles, role_domains = _validate_knowledge_base(knowledge_base)
    interest_options = _validate_catalogue(
        career_interest_catalogue, "options", "Career interest mapping catalogue"
    )
    activities = _validate_catalogue(
        role_activity_catalogue, "activities", "Preferred work activity mapping catalogue"
    )
    domain_ids = {domain["id"] for domain in knowledge_base["domains"]}
    _validate_interest_domains(interest_options, domain_ids)
    role_names = {role["id"]: role["name"] for role in roles}
    _validate_activity_catalogue(activities, role_names)

    selected_interest, interest_status = _resolve_interest(
        _preference_value(preferences, "career_interest", "careerInterest"), interest_options
    )
    mapped_domain_ids = list(selected_interest.get("domain_ids", [])) if interest_status == "mapped" else []
    interest_available = interest_status == "mapped"
    activity_value = _preference_value(preferences, "preferred_work_activity", "preferredWorkActivity")
    selected_activity, activity_selection_status = _resolve_activity(activity_value, activities)

    results: list[dict[str, Any]] = []

    for role in roles:
        role_id = role["id"]
        role_domain_id = role_domains[role_id]
        if interest_status == "neutral":
            match_status = "neutral"
        elif not interest_available:
            match_status = "unavailable"
        else:
            match_status = "matched" if role_domain_id in mapped_domain_ids else "mismatched"

        career_interest_earned = (
            CAREER_INTEREST_ORIGINAL_ALLOCATION
            if interest_available and role_domain_id in mapped_domain_ids else 0.0
        )
        career_activity = _approved_activity_relationship(selected_activity, role_id)
        activity_available = career_activity is not None
        activity_status = career_activity["status"] if activity_available else "unavailable"
        if activity_status == "exact_match":
            activity_earned = PREFERRED_WORK_ACTIVITY_ORIGINAL_ALLOCATION
            activity_match_status = "matched"
        elif activity_status == "partial_match":
            activity_earned = PREFERRED_WORK_ACTIVITY_ORIGINAL_ALLOCATION * career_activity["partial_match_weight"]
            activity_match_status = "partial"
        elif activity_status == "confirmed_mismatch":
            activity_earned = 0.0
            activity_match_status = "mismatched"
        else:
            activity_earned = 0.0
            activity_match_status = "unavailable"

        available_components = int(interest_available) + int(activity_available)
        if available_components:
            effective_interest_allocation = (
                INTEREST_ALIGNMENT_MAX_POINTS
                if interest_available and not activity_available
                else CAREER_INTEREST_ORIGINAL_ALLOCATION if interest_available else 0.0
            )
            effective_activity_allocation = (
                INTEREST_ALIGNMENT_MAX_POINTS
                if activity_available and not interest_available
                else PREFERRED_WORK_ACTIVITY_ORIGINAL_ALLOCATION if activity_available else 0.0
            )
            redistributed = INTEREST_ALIGNMENT_MAX_POINTS - (
                CAREER_INTEREST_ORIGINAL_ALLOCATION * int(interest_available)
                + PREFERRED_WORK_ACTIVITY_ORIGINAL_ALLOCATION * int(activity_available)
            )
            score = round(
                career_interest_earned * effective_interest_allocation / CAREER_INTEREST_ORIGINAL_ALLOCATION
                + activity_earned * effective_activity_allocation / PREFERRED_WORK_ACTIVITY_ORIGINAL_ALLOCATION,
                4,
            )
            score = min(INTEREST_ALIGNMENT_MAX_POINTS, max(0.0, score))
        else:
            effective_interest_allocation = 0.0
            effective_activity_allocation = 0.0
            redistributed = 0.0
            score = None
        factor_available = score is not None
        total_effective_allocation = effective_interest_allocation + effective_activity_allocation
        results.append({
            "role_id": role_id,
            "role_name": role["name"],
            "role_domain_id": role_domain_id,
            "career_interest_option_id": selected_interest.get("id") if selected_interest else None,
            "career_interest_option_label": selected_interest.get("label") if selected_interest else None,
            "career_interest_status": interest_status,
            "mapped_domain_id": mapped_domain_ids[0] if len(mapped_domain_ids) == 1 else None,
            "mapped_domain_ids": mapped_domain_ids,
            "career_interest_original_allocation": CAREER_INTEREST_ORIGINAL_ALLOCATION,
            "career_interest_available": interest_available,
            "career_interest_earned_points": career_interest_earned,
            "career_interest_effective_allocation": effective_interest_allocation,
            "preferred_work_activity_original_allocation": PREFERRED_WORK_ACTIVITY_ORIGINAL_ALLOCATION,
            "preferred_work_activity_option_id": selected_activity.get("id") if selected_activity else None,
            "preferred_work_activity_option_label": selected_activity.get("label") if selected_activity else None,
            "preferred_work_activity_selection_status": activity_selection_status,
            "preferred_work_activity_status": activity_status,
            "preferred_work_activity_match_status": activity_match_status,
            "preferred_work_activity_available": activity_available,
            "preferred_work_activity_earned_points": activity_earned,
            "preferred_work_activity_effective_allocation": effective_activity_allocation,
            "redistributed_points": redistributed,
            "effective_allocation": total_effective_allocation,
            "score": score,
            "maximum_points": INTEREST_ALIGNMENT_MAX_POINTS,
            "score_available": factor_available,
            "availability_status": "available" if factor_available else "unavailable",
            "match_status": match_status,
        })

    available_role_count = sum(role["score_available"] for role in results)
    effective_allocations = {role["effective_allocation"] for role in results}
    redistribution_values = {role["redistributed_points"] for role in results}
    if available_role_count == 0:
        overall_availability = "unavailable"
    elif available_role_count == len(results):
        overall_availability = "available"
    else:
        overall_availability = "partially_available"

    return {
        "factor": "interest_alignment",
        "original_allocation": {
            "career_interest": CAREER_INTEREST_ORIGINAL_ALLOCATION,
            "preferred_work_activity": PREFERRED_WORK_ACTIVITY_ORIGINAL_ALLOCATION,
            "total": INTEREST_ALIGNMENT_MAX_POINTS,
        },
        "effective_allocation": next(iter(effective_allocations)) if len(effective_allocations) == 1 else None,
        "redistributed_points": next(iter(redistribution_values)) if len(redistribution_values) == 1 else None,
        "role_specific_allocations": len(effective_allocations) > 1 or len(redistribution_values) > 1,
        "score_available": available_role_count > 0,
        "availability_status": overall_availability,
        "role_availability_summary": {
            "roles_evaluated": len(results),
            "roles_with_score": available_role_count,
            "roles_without_score": len(results) - available_role_count,
            "career_interest_and_activity_available": sum(
                role["career_interest_available"] and role["preferred_work_activity_available"] for role in results
            ),
            "career_interest_only_available": sum(
                role["career_interest_available"] and not role["preferred_work_activity_available"] for role in results
            ),
            "activity_only_available": sum(
                not role["career_interest_available"] and role["preferred_work_activity_available"] for role in results
            ),
            "neither_available": sum(
                not role["career_interest_available"] and not role["preferred_work_activity_available"] for role in results
            ),
        },
        "roles_evaluated": len(results),
        "roles": results,
    }
