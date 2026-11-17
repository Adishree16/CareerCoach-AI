"""Deterministic scoring for the Goals & Preferences factor."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any


_DATA_DIRECTORY = Path(__file__).resolve().parents[1] / "data"
_GOAL_CATALOGUE_PATH = _DATA_DIRECTORY / "career_goal_mappings.json"
_PRIORITY_CATALOGUE_PATH = _DATA_DIRECTORY / "career_priority_mappings.json"
_KNOWLEDGE_BASE_PATH = _DATA_DIRECTORY / "career_knowledge_base.json"

CAREER_GOAL_ALLOCATION = 12.0
CAREER_PRIORITY_ALLOCATION = 8.0
GOALS_PREFERENCES_MAX_POINTS = 20.0

_RELATIONSHIP_STATUSES = {
    "exact_match",
    "partial_match",
    "confirmed_mismatch",
    "neutral",
    "unavailable",
}
_REVIEW_STATUSES = {"needs_review", "approved", "neutral"}
_SCORABLE_STATUSES = {"exact_match", "partial_match", "confirmed_mismatch"}


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} could not be loaded.") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{description} has an invalid structure.")
    return data


def load_goals_preferences_data() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load both reviewed-mapping catalogues and the existing role catalogue."""
    return (
        _read_json(_GOAL_CATALOGUE_PATH, "Career goal mapping catalogue"),
        _read_json(_PRIORITY_CATALOGUE_PATH, "Career priority mapping catalogue"),
        _read_json(_KNOWLEDGE_BASE_PATH, "Career knowledge base"),
    )


def _validate_roles(knowledge_base: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], dict[str, str]]:
    roles = knowledge_base.get("roles")
    if not isinstance(roles, list) or not roles:
        raise ValueError("Career knowledge base must contain a non-empty roles list.")

    role_names: dict[str, str] = {}
    for role in roles:
        if not isinstance(role, Mapping):
            raise ValueError("Career knowledge base contains a malformed role.")
        role_id = role.get("id")
        role_name = role.get("name")
        if (
            not isinstance(role_id, str)
            or not role_id
            or role_id in role_names
            or not isinstance(role_name, str)
            or not role_name
        ):
            raise ValueError("Career knowledge base contains an invalid or duplicate role.")
        role_names[role_id] = role_name
    return roles, role_names


def _approved_review_is_complete(relationship: Mapping[str, Any]) -> bool:
    references = relationship.get("evidence_references")
    if not isinstance(references, list) or not references:
        return False
    for reference in references:
        if not isinstance(reference, Mapping):
            return False
        if not all(
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


def _validate_partial_weight(relationship: Mapping[str, Any]) -> None:
    weight = relationship.get("partial_match_weight")
    if (
        isinstance(weight, bool)
        or not isinstance(weight, (int, float))
        or not math.isfinite(weight)
        or not 0 < weight < 1
    ):
        raise ValueError("Partial-match relationships require a numeric approved weight greater than 0 and less than 1.")


def _validate_catalogue(
    catalogue: Mapping[str, Any],
    *,
    expected_component: str,
    expected_allocation: float,
    role_names: Mapping[str, str],
) -> tuple[list[Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    component = catalogue.get("component")
    if (
        not isinstance(catalogue.get("catalogue_name"), str)
        or not catalogue["catalogue_name"].strip()
        or not isinstance(catalogue.get("version"), str)
        or not catalogue["version"].strip()
        or not isinstance(component, Mapping)
        or component.get("id") != expected_component
        or component.get("original_allocation") != expected_allocation
    ):
        raise ValueError(f"{expected_component} catalogue metadata is invalid.")

    options = catalogue.get("options")
    if not isinstance(options, list):
        raise ValueError(f"{expected_component} catalogue must contain an options list.")

    by_id: dict[str, Mapping[str, Any]] = {}
    labels: set[str] = set()
    for option in options:
        if not isinstance(option, Mapping):
            raise ValueError(f"{expected_component} catalogue contains a malformed option.")
        option_id = option.get("id")
        label = option.get("label")
        neutral = option.get("neutral")
        option_review_status = option.get("review_status")
        if (
            not isinstance(option_id, str)
            or not option_id
            or option_id in by_id
            or not isinstance(label, str)
            or not label.strip()
            or label in labels
            or not isinstance(neutral, bool)
            or not isinstance(option_review_status, str)
            or option_review_status not in _REVIEW_STATUSES
        ):
            raise ValueError(f"{expected_component} catalogue contains a duplicate or invalid option.")
        if neutral != (option_review_status == "neutral"):
            raise ValueError(f"{expected_component} option {option_id!r} has inconsistent neutral metadata.")

        relationships = option.get("relationships")
        if not isinstance(relationships, list):
            raise ValueError(f"Option {option_id!r} must contain a relationships list.")
        role_relationships: dict[str, Mapping[str, Any]] = {}
        for relationship in relationships:
            if not isinstance(relationship, Mapping):
                raise ValueError(f"Option {option_id!r} contains a malformed relationship.")
            role_id = relationship.get("role_id")
            role_name = relationship.get("role_name")
            status = relationship.get("status")
            review_status = relationship.get("review_status")
            if (
                not isinstance(role_id, str)
                or role_id not in role_names
                or role_id in role_relationships
                or role_name != role_names[role_id]
                or not isinstance(status, str)
                or status not in _RELATIONSHIP_STATUSES
                or not isinstance(review_status, str)
                or review_status not in _REVIEW_STATUSES
            ):
                raise ValueError(f"Option {option_id!r} contains a duplicate, conflicting, or invalid role relationship.")
            if status == "partial_match":
                _validate_partial_weight(relationship)
            elif relationship.get("partial_match_weight") is not None:
                raise ValueError("Only partial-match relationships may specify a partial-match weight.")
            if status in _SCORABLE_STATUSES and review_status == "approved":
                if not _approved_review_is_complete(relationship):
                    raise ValueError(f"Approved relationship {option_id!r}/{role_id!r} lacks complete review evidence.")
            role_relationships[role_id] = relationship

        if neutral:
            if any(
                item.get("status") != "neutral" or item.get("review_status") != "neutral"
                for item in role_relationships.values()
            ):
                raise ValueError(f"Neutral option {option_id!r} must remain neutral for every listed role.")
        by_id[option_id] = {**option, "_relationships_by_role": role_relationships}
        labels.add(label)
    return options, by_id


def _resolve_option(value: Any, options_by_id: Mapping[str, Mapping[str, Any]], component: str) -> tuple[Mapping[str, Any] | None, str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "missing"
    if not isinstance(value, str):
        raise ValueError(f"{component} must be an exact option label or stable option ID.")
    matches = [option for option in options_by_id.values() if value == option["id"] or value == option["label"]]
    if len(matches) != 1:
        raise ValueError(f"Unknown {component} option.")
    return matches[0], "selected"


def _input_field(preferences: Mapping[str, Any], snake: str, camel: str) -> Any:
    if snake in preferences and camel in preferences and preferences[snake] != preferences[camel]:
        raise ValueError(f"Conflicting values were provided for {snake}.")
    return preferences[snake] if snake in preferences else preferences.get(camel)


def _component_state(
    option: Mapping[str, Any] | None,
    role_id: str,
    *,
    component_name: str,
) -> dict[str, Any]:
    allocation = CAREER_GOAL_ALLOCATION if component_name == "career_goal" else CAREER_PRIORITY_ALLOCATION
    base = {
        "option_id": option.get("id") if option else None,
        "option_label": option.get("label") if option else None,
        "original_allocation": allocation,
        "relationship_status": "unavailable",
        "catalogue_relationship_status": "unavailable",
        "available": False,
        "partial_match_weight": None,
        "score": None,
        "evidence_references": [],
        "rationale": "No reviewed role-specific relationship is available for scoring.",
    }
    if option is None:
        base["relationship_status"] = "unavailable"
        base["catalogue_relationship_status"] = "missing"
        base["rationale"] = "No preference was supplied; the component is unavailable."
        return base

    if option["neutral"]:
        base["relationship_status"] = "neutral"
        base["catalogue_relationship_status"] = "neutral"
        base["rationale"] = "The selected uncertainty option is neutral and unavailable for scoring."
        return base

    relationships = option["_relationships_by_role"]
    relationship = relationships.get(role_id)
    if relationship is None:
        base["catalogue_relationship_status"] = "missing"
        base["rationale"] = "No role-specific relationship record exists; missing mappings are unavailable."
        return base

    status = relationship["status"]
    base["catalogue_relationship_status"] = status
    base["evidence_references"] = list(relationship.get("evidence_references") or [])
    if status in {"neutral", "unavailable"}:
        base["relationship_status"] = status
        base["rationale"] = relationship.get("rationale") or "The relationship is unavailable for scoring."
        return base

    if relationship["review_status"] != "approved":
        base["relationship_status"] = "unavailable"
        base["rationale"] = "The relationship has not been approved and cannot contribute points."
        return base

    base["relationship_status"] = status
    base["available"] = True
    base["rationale"] = relationship.get("reviewer_rationale") or relationship.get("rationale") or "Reviewed relationship."
    if status == "partial_match":
        base["partial_match_weight"] = relationship["partial_match_weight"]
    return base


def _apply_effective_allocations(goal: dict[str, Any], priority: dict[str, Any]) -> dict[str, Any]:
    goal_available = goal["available"]
    priority_available = priority["available"]
    if goal_available and priority_available:
        goal_effective, priority_effective = CAREER_GOAL_ALLOCATION, CAREER_PRIORITY_ALLOCATION
    elif goal_available:
        goal_effective, priority_effective = GOALS_PREFERENCES_MAX_POINTS, 0.0
    elif priority_available:
        goal_effective, priority_effective = 0.0, GOALS_PREFERENCES_MAX_POINTS
    else:
        goal_effective, priority_effective = 0.0, 0.0

    goal["effective_allocation"] = goal_effective
    priority["effective_allocation"] = priority_effective
    goal["redistributed_points"] = round(max(0.0, goal_effective - CAREER_GOAL_ALLOCATION), 4)
    priority["redistributed_points"] = round(max(0.0, priority_effective - CAREER_PRIORITY_ALLOCATION), 4)
    for component in (goal, priority):
        if not component["available"]:
            component["score"] = None
        elif component["relationship_status"] == "exact_match":
            component["score"] = component["effective_allocation"]
        elif component["relationship_status"] == "partial_match":
            component["score"] = round(
                component["effective_allocation"] * component["partial_match_weight"], 4
            )
        else:  # A reviewed confirmed mismatch is available and scores zero.
            component["score"] = 0.0

    factor_available = goal_available or priority_available
    score = round(sum(component["score"] for component in (goal, priority) if component["available"]), 4) if factor_available else None
    redistributed_total = round(goal["redistributed_points"] + priority["redistributed_points"], 4)
    unavailable = [
        name for name, component in (("career_goal", goal), ("career_priority", priority))
        if not component["available"]
    ]
    redistributed_to = [
        name for name, component in (("career_goal", goal), ("career_priority", priority))
        if component["redistributed_points"] > 0
    ]
    return {
        "factor_available": factor_available,
        "score": score,
        "maximum_points": GOALS_PREFERENCES_MAX_POINTS,
        "original_allocations": {
            "career_goal": CAREER_GOAL_ALLOCATION,
            "career_priority": CAREER_PRIORITY_ALLOCATION,
            "total": GOALS_PREFERENCES_MAX_POINTS,
        },
        "effective_allocations": {"career_goal": goal_effective, "career_priority": priority_effective},
        "redistributed_points": {"career_goal": goal["redistributed_points"], "career_priority": priority["redistributed_points"]},
        "redistribution": {
            "applied": redistributed_total > 0,
            "points_redistributed": redistributed_total,
            "unavailable_components": unavailable,
            "redistributed_to": redistributed_to,
        },
    }


def score_goals_preferences(
    preferences: Any,
    *,
    career_goal_catalogue: Mapping[str, Any] | None = None,
    career_priority_catalogue: Mapping[str, Any] | None = None,
    knowledge_base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the 20-point factor for every catalogue role.

    Only reviewed, evidence-supported relationship records contribute points.
    The selected options are also returned as context, separately from scores.
    """
    if career_goal_catalogue is None or career_priority_catalogue is None or knowledge_base is None:
        loaded_goal, loaded_priority, loaded_knowledge_base = load_goals_preferences_data()
        if career_goal_catalogue is None:
            career_goal_catalogue = loaded_goal
        if career_priority_catalogue is None:
            career_priority_catalogue = loaded_priority
        if knowledge_base is None:
            knowledge_base = loaded_knowledge_base

    if preferences is None:
        preferences = {}
    elif not isinstance(preferences, Mapping):
        model_dump = getattr(preferences, "model_dump", None)
        if callable(model_dump):
            preferences = model_dump(by_alias=False)
        if not isinstance(preferences, Mapping):
            raise ValueError("Preferences must be an object containing career goal and career priority values.")

    roles, role_names = _validate_roles(knowledge_base)
    _, goals_by_id = _validate_catalogue(
        career_goal_catalogue,
        expected_component="career_goal",
        expected_allocation=CAREER_GOAL_ALLOCATION,
        role_names=role_names,
    )
    _, priorities_by_id = _validate_catalogue(
        career_priority_catalogue,
        expected_component="career_priority",
        expected_allocation=CAREER_PRIORITY_ALLOCATION,
        role_names=role_names,
    )
    goal_option, _ = _resolve_option(
        _input_field(preferences, "career_goal", "careerGoal"), goals_by_id, "career goal"
    )
    priority_option, _ = _resolve_option(
        _input_field(preferences, "career_priority", "careerPriority"), priorities_by_id, "career priority"
    )

    context = {
        "career_goal": {"option_id": goal_option.get("id") if goal_option else None, "label": goal_option.get("label") if goal_option else None},
        "career_priority": {"option_id": priority_option.get("id") if priority_option else None, "label": priority_option.get("label") if priority_option else None},
        "scoring_note": "Contextual inputs are not scored unless a reviewed, evidence-supported role relationship is available.",
    }
    results: list[dict[str, Any]] = []
    for role in roles:
        goal_state = _component_state(goal_option, role["id"], component_name="career_goal")
        priority_state = _component_state(priority_option, role["id"], component_name="career_priority")
        factor = _apply_effective_allocations(goal_state, priority_state)
        results.append({
            "role_id": role["id"],
            "role_name": role["name"],
            **factor,
            "components": {"career_goal": goal_state, "career_priority": priority_state},
            "contextual_preferences": context,
        })

    return {
        "factor": "goals_preferences",
        "maximum_points": GOALS_PREFERENCES_MAX_POINTS,
        "original_allocations": {
            "career_goal": CAREER_GOAL_ALLOCATION,
            "career_priority": CAREER_PRIORITY_ALLOCATION,
            "total": GOALS_PREFERENCES_MAX_POINTS,
        },
        "roles_evaluated": len(results),
        "roles": results,
    }
