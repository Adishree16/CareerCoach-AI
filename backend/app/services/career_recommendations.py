"""Combine the deterministic scoring components for each career role."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from app.services import career_intelligence, goals_preferences, interest_alignment, skill_assessment


MAXIMUM_SCORE = 100.0
EXPECTED_ROLE_COUNT = 22
_COMPONENT_MAXIMA = {
    "skill_alignment": 43.75,
    "projects_experience": 31.25,
    "interest_alignment": 25.0,
}
_PREFERENCE_KEYS = {
    "career_interest": "careerInterest",
    "preferred_work_activity": "preferredWorkActivity",
    "career_goal": "careerGoal",
    "career_priority": "careerPriority",
}
_SKILL_SCORE_STATUSES = {"scored", "unavailable_no_applicable_requirements"}
_PROJECT_SCORE_STATUSES = {"complete", "partial"}
_INTEREST_AVAILABILITY_STATUSES = {"available", "unavailable"}
_CAREER_INTEREST_STATUSES = {"mapped", "missing", "unknown", "neutral", "unmapped", "unreviewed"}
_ACTIVITY_SELECTION_STATUSES = {"selected", "missing", "unknown", "neutral"}
_ACTIVITY_RELATIONSHIP_STATUSES = {"exact_match", "partial_match", "confirmed_mismatch", "unavailable"}
_SCORABLE_RELATIONSHIP_STATUSES = {"exact_match", "partial_match", "confirmed_mismatch"}


class ProfileRequiredError(ValueError):
    """Raised when the supplied profile cannot be scored."""


class InvalidPreferenceError(ValueError):
    """Raised when a supplied preference is not an exact catalogue option."""


class RecommendationDataIntegrityError(RuntimeError):
    """Raised when catalogue or scorer role data is inconsistent."""


class RecommendationOutputError(RuntimeError):
    """Raised when a scorer returns a malformed or out-of-bounds result."""


def _as_mapping(value: Any, description: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(by_alias=False)
        if isinstance(dumped, Mapping):
            return dumped
    raise ValueError(f"{description} must be an object.")


def _field(data: Mapping[str, Any], snake: str, camel: str, default: Any = None) -> Any:
    return data[snake] if snake in data else data.get(camel, default)


def _validate_profile(profile: Any) -> Mapping[str, Any]:
    if profile is None:
        raise ProfileRequiredError("A profile is required for career scoring.")
    try:
        profile_data = _as_mapping(profile, "Profile")
    except ValueError as exc:
        raise ProfileRequiredError("A usable profile is required for career scoring.") from exc
    if not profile_data:
        raise ProfileRequiredError("A usable profile is required for career scoring.")

    career_skills = _as_mapping(
        _field(profile_data, "career_skills", "careerSkills", {}), "Profile career skills"
    )
    additional = _as_mapping(
        _field(profile_data, "additional_information", "additionalInformation", {}),
        "Profile additional information",
    )
    career_status = _field(career_skills, "career_status", "careerStatus")
    technical_skills = _field(career_skills, "technical_skills", "technicalSkills")
    if career_status not in {"Student", "Fresher", "Experienced"}:
        raise ProfileRequiredError("Profile career status is missing or invalid.")
    if not isinstance(technical_skills, list) or any(not isinstance(item, str) for item in technical_skills):
        raise ProfileRequiredError("Profile technical skills must be a list of names.")
    if not isinstance(additional, Mapping):
        raise ProfileRequiredError("Profile additional information must be an object.")
    return profile_data


def _normalize_preferences(preferences: Any) -> dict[str, Any]:
    if preferences is None:
        source: Mapping[str, Any] = {}
    else:
        source = _as_mapping(preferences, "Preferences")

    accepted_keys = set(_PREFERENCE_KEYS) | set(_PREFERENCE_KEYS.values())
    unexpected = set(source) - accepted_keys
    if unexpected:
        raise InvalidPreferenceError("Preferences contain unsupported fields.")

    normalized: dict[str, Any] = {}
    for snake, camel in _PREFERENCE_KEYS.items():
        if snake in source and camel in source and source[snake] != source[camel]:
            raise InvalidPreferenceError(f"Conflicting values were supplied for {snake}.")
        value = source[snake] if snake in source else source.get(camel)
        if value is not None and not isinstance(value, str):
            raise InvalidPreferenceError(f"{snake} must be an exact option ID or label.")
        normalized[snake] = value
    return normalized


def _catalogue_options(catalogue: Mapping[str, Any], key: str, label: str) -> list[Mapping[str, Any]]:
    options = catalogue.get(key)
    if not isinstance(options, list):
        raise RecommendationDataIntegrityError(f"{label} catalogue has no valid {key} list.")
    if any(not isinstance(option, Mapping) for option in options):
        raise RecommendationDataIntegrityError(f"{label} catalogue contains a malformed option.")
    return options


def _validate_preference_values(
    preferences: Mapping[str, Any],
    interest_catalogue: Mapping[str, Any],
    activity_catalogue: Mapping[str, Any],
    goal_catalogue: Mapping[str, Any],
    priority_catalogue: Mapping[str, Any],
) -> None:
    sources = {
        "career_interest": _catalogue_options(interest_catalogue, "options", "Career interest"),
        "preferred_work_activity": _catalogue_options(activity_catalogue, "activities", "Work activity"),
        "career_goal": _catalogue_options(goal_catalogue, "options", "Career goal"),
        "career_priority": _catalogue_options(priority_catalogue, "options", "Career priority"),
    }
    for field, options in sources.items():
        value = preferences[field]
        if value is None or not value.strip():
            continue
        if not any(value == option.get("id") or value == option.get("label") for option in options):
            raise InvalidPreferenceError(f"Unknown {field.replace('_', ' ')} option.")


def _contextual_option(value: Any, options: list[Mapping[str, Any]]) -> dict[str, str | None]:
    if value is None or not value.strip():
        return {"option_id": None, "label": None}
    matches = [option for option in options if value == option.get("id") or value == option.get("label")]
    if len(matches) != 1:
        raise RecommendationDataIntegrityError("A contextual preference does not resolve uniquely.")
    option = matches[0]
    return {"option_id": option["id"], "label": option["label"]}


def _contextual_preferences(
    preferences: Mapping[str, Any],
    goal_catalogue: Mapping[str, Any],
    priority_catalogue: Mapping[str, Any],
) -> dict[str, dict[str, str | None]]:
    return {
        "career_goal": _contextual_option(
            preferences["career_goal"],
            _catalogue_options(goal_catalogue, "options", "Career goal"),
        ),
        "career_priority": _contextual_option(
            preferences["career_priority"],
            _catalogue_options(priority_catalogue, "options", "Career priority"),
        ),
    }


def _number(value: Any, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RecommendationOutputError(f"Scorer returned an invalid {description}.")
    return float(value)


def _role_index(
    output: Any,
    expected: Mapping[str, str],
    component: str,
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(output, Mapping):
        raise RecommendationOutputError(f"{component} scorer did not return an object.")
    rows = output.get("roles")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise RecommendationDataIntegrityError(f"{component} scorer returned the wrong number of roles.")
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise RecommendationOutputError(f"{component} scorer returned a malformed role result.")
        role_id = row.get("role_id")
        if not isinstance(role_id, str) or role_id not in expected:
            raise RecommendationDataIntegrityError(f"{component} scorer returned an unexpected role ID.")
        if role_id in indexed:
            raise RecommendationDataIntegrityError(f"{component} scorer returned duplicate role ID {role_id!r}.")
        if row.get("role_name") != expected[role_id]:
            raise RecommendationDataIntegrityError(f"{component} scorer returned an inconsistent role name.")
        indexed[role_id] = row
    if set(indexed) != set(expected):
        raise RecommendationDataIntegrityError(f"{component} scorer omitted one or more expected role IDs.")
    return indexed


def _unavailable_reason(component: str, details: Mapping[str, Any]) -> str:
    if component == "skill_alignment":
        return "No applicable weighted skill requirements are available for this role."
    if component == "projects_experience":
        if details.get("unavailable_maximum_points", 0):
            return "Some Projects/Experience allocation has no evaluable requirement capacity."
        return "No Projects/Experience requirement capacity is evaluable for this role."
    if component == "interest_alignment":
        reasons: list[str] = []
        interest_status = details.get("career_interest_status")
        if interest_status == "missing":
            reasons.append("Career interest selection is missing.")
        elif interest_status == "neutral":
            reasons.append("Career interest selection is neutral.")
        elif interest_status in {"unmapped", "unreviewed"}:
            reasons.append(f"Career interest selection is {interest_status}.")
        activity_selection = details.get("preferred_work_activity_selection_status")
        if activity_selection == "missing":
            reasons.append("Preferred work activity selection is missing.")
        elif activity_selection == "neutral":
            reasons.append("Preferred work activity selection is neutral.")
        elif activity_selection == "selected" and details.get("preferred_work_activity_status") == "unavailable":
            reasons.append("No approved work-activity relationship is available for this role.")
        elif activity_selection == "unknown":
            reasons.append("Preferred work activity selection is unknown.")
        return " ".join(reasons) or "No interest or activity component is available for this role."
    components = details.get("components")
    reasons = []
    if isinstance(components, Mapping):
        for name in ("career_goal", "career_priority"):
            component = components.get(name)
            if isinstance(component, Mapping) and component.get("available") is False:
                rationale = component.get("rationale")
                if isinstance(rationale, str) and rationale.strip():
                    reasons.append(rationale.strip())
    return " ".join(reasons) or "No reviewed career goal or priority relationship is available for this role."


def _component(
    name: str,
    score: float | None,
    applicable: float,
    available: bool,
    status: str,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "score": score,
        "maximum_points": _COMPONENT_MAXIMA[name],
        "maximum_applicable_points": applicable,
        "available": available,
        "status": status,
        "details": details,
    }


def _select_top_recommendation_role_ids(roles: list[Mapping[str, Any]]) -> list[str]:
    """Select the top three scoreable roles without changing canonical result order."""
    eligible: list[tuple[float, float, int, str]] = []
    for position, role in enumerate(roles):
        if role.get("available") is not True or role.get("status") not in {
            "available", "partially_available"
        }:
            continue

        role_id = role.get("role_id")
        score_value = role.get("final_score")
        maximum_value = role.get("maximum_applicable_score")
        if (
            not isinstance(role_id, str)
            or not role_id
            or isinstance(score_value, bool)
            or not isinstance(score_value, (int, float))
            or isinstance(maximum_value, bool)
            or not isinstance(maximum_value, (int, float))
        ):
            continue

        try:
            score = float(score_value)
            maximum = float(maximum_value)
        except (OverflowError, ValueError):
            continue
        if (
            not math.isfinite(score)
            or not math.isfinite(maximum)
            or maximum <= 0
            or score < 0
            or score > maximum
        ):
            continue
        eligible.append((-score, -maximum, position, role_id))

    eligible.sort()
    return [role_id for _, _, _, role_id in eligible[:3]]


def _skill_component(row: Mapping[str, Any]) -> dict[str, Any]:
    if row.get("maximum_points") != _COMPONENT_MAXIMA["skill_alignment"]:
        raise RecommendationOutputError("Skill Alignment returned the wrong maximum allocation.")
    available = row.get("score_available")
    if not isinstance(available, bool):
        raise RecommendationOutputError("Skill Alignment availability is malformed.")
    status = row.get("score_status")
    if status not in _SKILL_SCORE_STATUSES:
        raise RecommendationOutputError("Skill Alignment returned an unknown score status.")
    if available != (status == "scored"):
        raise RecommendationOutputError("Skill Alignment status conflicts with its availability.")
    raw_score = row.get("score")
    if available:
        score = _number(raw_score, "Skill Alignment score")
        if not 0 <= score <= _COMPONENT_MAXIMA["skill_alignment"]:
            raise RecommendationOutputError("Skill Alignment score is outside its allocation.")
        coverage = _number(row.get("evidence_coverage_percentage"), "Skill Alignment coverage")
        if not 0 <= coverage <= 100:
            raise RecommendationOutputError("Skill Alignment coverage is outside 0–100%.")
        return _component("skill_alignment", score, _COMPONENT_MAXIMA["skill_alignment"], True, str(row.get("score_status")), row)
    if raw_score is not None:
        raise RecommendationOutputError("Unavailable Skill Alignment must have a null score.")
    if row.get("evidence_coverage_percentage") is not None:
        raise RecommendationOutputError("Unavailable Skill Alignment must have null evidence coverage.")
    return _component("skill_alignment", None, 0.0, False, status, row)


def _projects_component(row: Mapping[str, Any]) -> dict[str, Any]:
    if row.get("maximum_points") != _COMPONENT_MAXIMA["projects_experience"]:
        raise RecommendationOutputError("Projects/Experience returned the wrong maximum allocation.")
    applicable = _number(row.get("evaluated_maximum_points"), "Projects/Experience applicable maximum")
    unavailable_max = _number(row.get("unavailable_maximum_points"), "Projects/Experience unavailable maximum")
    if applicable < 0 or unavailable_max < 0 or applicable + unavailable_max > _COMPONENT_MAXIMA["projects_experience"]:
        raise RecommendationOutputError("Projects/Experience allocations are inconsistent.")
    status = row.get("score_status")
    if status not in _PROJECT_SCORE_STATUSES or (status == "partial") != (unavailable_max > 0):
        raise RecommendationOutputError("Projects/Experience status conflicts with its unavailable allocation.")
    raw_score = _number(row.get("total_points"), "Projects/Experience score")
    if applicable == 0:
        if raw_score != 0:
            raise RecommendationOutputError("Projects/Experience cannot earn points with zero evaluable capacity.")
        return _component(
            "projects_experience", None, 0.0, False,
            status, row,
        )
    score = raw_score
    if not 0 <= score <= applicable:
        raise RecommendationOutputError("Projects/Experience score exceeds its applicable maximum.")
    component_status = "partially_available" if status == "partial" else "complete"
    return _component("projects_experience", score, applicable, True, component_status, row)


def _interest_component(row: Mapping[str, Any]) -> dict[str, Any]:
    available = row.get("score_available")
    if not isinstance(available, bool) or row.get("maximum_points") != _COMPONENT_MAXIMA["interest_alignment"]:
        raise RecommendationOutputError("Interest Alignment result is malformed.")
    availability_status = row.get("availability_status")
    if availability_status not in _INTEREST_AVAILABILITY_STATUSES or available != (availability_status == "available"):
        raise RecommendationOutputError("Interest Alignment status conflicts with its availability.")
    career_interest_available = row.get("career_interest_available")
    activity_available = row.get("preferred_work_activity_available")
    if not isinstance(career_interest_available, bool) or not isinstance(activity_available, bool):
        raise RecommendationOutputError("Interest Alignment component availability is malformed.")
    if available != (career_interest_available or activity_available):
        raise RecommendationOutputError("Interest Alignment factor availability conflicts with its components.")
    if row.get("career_interest_status") not in _CAREER_INTEREST_STATUSES:
        raise RecommendationOutputError("Interest Alignment returned an unknown career-interest status.")
    if row.get("preferred_work_activity_selection_status") not in _ACTIVITY_SELECTION_STATUSES:
        raise RecommendationOutputError("Interest Alignment returned an unknown activity-selection status.")
    activity_status = row.get("preferred_work_activity_status")
    if activity_status not in _ACTIVITY_RELATIONSHIP_STATUSES:
        raise RecommendationOutputError("Interest Alignment returned an unknown activity relationship status.")
    if career_interest_available != (row.get("career_interest_status") == "mapped"):
        raise RecommendationOutputError("Career-interest status conflicts with its availability.")
    if activity_available != (activity_status in _SCORABLE_RELATIONSHIP_STATUSES):
        raise RecommendationOutputError("Activity relationship status conflicts with its availability.")
    expected_interest_allocation = 15.0 if career_interest_available and activity_available else 25.0 if career_interest_available else 0.0
    expected_activity_allocation = 10.0 if career_interest_available and activity_available else 25.0 if activity_available else 0.0
    interest_allocation = _number(row.get("career_interest_effective_allocation"), "career-interest allocation")
    activity_allocation = _number(row.get("preferred_work_activity_effective_allocation"), "work-activity allocation")
    if (
        row.get("career_interest_original_allocation") != 15.0
        or row.get("preferred_work_activity_original_allocation") != 10.0
        or interest_allocation != expected_interest_allocation
        or activity_allocation != expected_activity_allocation
    ):
        raise RecommendationOutputError("Interest Alignment effective allocations are inconsistent.")
    applicable = _number(row.get("effective_allocation"), "Interest Alignment applicable maximum")
    expected_applicable = _COMPONENT_MAXIMA["interest_alignment"] if available else 0.0
    if applicable != expected_applicable or applicable != interest_allocation + activity_allocation:
        raise RecommendationOutputError("Interest Alignment effective allocation is inconsistent with availability.")
    interest_earned = _number(row.get("career_interest_earned_points"), "career-interest score")
    activity_earned = _number(row.get("preferred_work_activity_earned_points"), "work-activity score")
    if not 0 <= interest_earned <= 15 or not 0 <= activity_earned <= 10:
        raise RecommendationOutputError("Interest Alignment subcomponent score is outside its allocation.")
    if (not career_interest_available and interest_earned != 0) or (not activity_available and activity_earned != 0):
        raise RecommendationOutputError("Unavailable Interest Alignment subcomponent cannot earn points.")
    if not available:
        if row.get("score") is not None or applicable != 0:
            raise RecommendationOutputError("Unavailable Interest Alignment has inconsistent score or allocation.")
        return _component("interest_alignment", None, 0.0, False, availability_status, row)
    score = _number(row.get("score"), "Interest Alignment score")
    expected_score = round(
        interest_earned * interest_allocation / 15.0
        + activity_earned * activity_allocation / 10.0,
        4,
    )
    if applicable <= 0 or not 0 <= score <= applicable or score != expected_score:
        raise RecommendationOutputError("Interest Alignment score exceeds its applicable allocation.")
    return _component("interest_alignment", score, applicable, True, availability_status, row)


def _combine_role(
    role: Mapping[str, Any],
    skill_row: Mapping[str, Any],
    project_row: Mapping[str, Any],
    interest_row: Mapping[str, Any],
) -> dict[str, Any]:
    if project_row.get("role_id") != role["id"] or project_row.get("role_name") != role["name"]:
        raise RecommendationDataIntegrityError("Projects/Experience scorer returned a mismatched role.")

    components = {
        "skill_alignment": _skill_component(skill_row),
        "projects_experience": _projects_component(project_row),
        "interest_alignment": _interest_component(interest_row),
    }
    available = [item for item in components.values() if item["available"]]
    applicable_maximum = round(sum(item["maximum_applicable_points"] for item in available), 4)
    if applicable_maximum > MAXIMUM_SCORE:
        raise RecommendationOutputError("Combined applicable maximum exceeds the global score allocation.")
    final_score = round(sum(item["score"] for item in available), 4) if available else None
    if final_score is not None and (final_score < 0 or final_score > applicable_maximum or final_score > MAXIMUM_SCORE):
        raise RecommendationOutputError("Combined score exceeds its maximum applicable allocation.")

    if not available:
        status = "unavailable"
    elif len(available) == len(components) and all(
        item["status"] not in {"partially_available", "partial"} for item in available
    ):
        status = "available"
    else:
        status = "partially_available"

    reasons = [
        {"component": name, "reason": _unavailable_reason(name, item["details"])}
        for name, item in components.items()
        if not item["available"]
    ]
    for name, item in components.items():
        if item["available"] and item["status"] in {"partially_available", "partial"}:
            reasons.append({
                "component": name,
                "reason": "This component has only partial evaluable capacity; see its details.",
            })

    return {
        "role_id": role["id"],
        "role_name": role["name"],
        "components": components,
        "final_score": final_score,
        "maximum_applicable_score": applicable_maximum,
        "maximum_score": MAXIMUM_SCORE,
        "available": bool(available),
        "status": status,
        "unavailable_reasons": reasons,
    }


def score_career_recommendations(profile: Any, preferences: Any) -> dict[str, Any]:
    """Combine deterministic profile-to-role alignment scores for all 22 roles."""
    profile_data = _validate_profile(profile)
    normalized_preferences = _normalize_preferences(preferences)

    interest_catalogue, activity_catalogue, knowledge_base = interest_alignment.load_interest_catalogues()
    goal_catalogue, priority_catalogue, goals_knowledge_base = goals_preferences.load_goals_preferences_data()
    if not isinstance(knowledge_base, Mapping) or not isinstance(goals_knowledge_base, Mapping):
        raise RecommendationDataIntegrityError("A career knowledge base could not be loaded.")
    roles = knowledge_base.get("roles")
    goals_roles = goals_knowledge_base.get("roles")
    if not isinstance(roles, list) or not isinstance(goals_roles, list):
        raise RecommendationDataIntegrityError("Career knowledge base has no valid roles list.")
    if knowledge_base.get("total_roles") != EXPECTED_ROLE_COUNT or len(roles) != EXPECTED_ROLE_COUNT:
        raise RecommendationDataIntegrityError("Career knowledge base must contain exactly 22 roles.")

    expected: dict[str, str] = {}
    for role in roles:
        if not isinstance(role, Mapping):
            raise RecommendationDataIntegrityError("Career knowledge base contains a malformed role.")
        role_id, role_name = role.get("id"), role.get("name")
        if not isinstance(role_id, str) or not role_id or not isinstance(role_name, str) or not role_name:
            raise RecommendationDataIntegrityError("Career knowledge base role is missing its ID or name.")
        if role_id in expected:
            raise RecommendationDataIntegrityError(f"Career knowledge base has duplicate role ID {role_id!r}.")
        expected[role_id] = role_name
    goals_identity = {
        role.get("id"): role.get("name") for role in goals_roles if isinstance(role, Mapping)
    }
    if goals_identity != expected or len(goals_roles) != EXPECTED_ROLE_COUNT:
        raise RecommendationDataIntegrityError("Scoring catalogues do not reference the same canonical roles.")

    _validate_preference_values(
        normalized_preferences, interest_catalogue, activity_catalogue, goal_catalogue, priority_catalogue
    )

    skill_output = skill_assessment.score_skill_alignment(profile_data, knowledge_base=knowledge_base)
    interest_output = interest_alignment.score_interest_alignment(
        normalized_preferences,
        career_interest_catalogue=interest_catalogue,
        role_activity_catalogue=activity_catalogue,
        knowledge_base=knowledge_base,
    )
    skill_rows = _role_index(skill_output, expected, "Skill Alignment")
    interest_rows = _role_index(interest_output, expected, "Interest Alignment")
    contextual_preferences = _contextual_preferences(
        normalized_preferences, goal_catalogue, priority_catalogue
    )

    results: list[dict[str, Any]] = []
    for role in roles:
        project_output = career_intelligence.score_projects_experience(profile_data, role)
        if not isinstance(project_output, Mapping):
            raise RecommendationOutputError("Projects/Experience scorer did not return an object.")
        role_id = role["id"]
        results.append(_combine_role(
            role,
            skill_rows[role_id],
            project_output,
            interest_rows[role_id],
        ))

    return {
        "maximum_score": MAXIMUM_SCORE,
        "roles_evaluated": len(results),
        "contextual_preferences": contextual_preferences,
        "top_recommendation_role_ids": _select_top_recommendation_role_ids(results),
        "roles": results,
    }
