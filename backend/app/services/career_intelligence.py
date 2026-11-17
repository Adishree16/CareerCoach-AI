"""Deterministic career-intelligence scoring components."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_KNOWLEDGE_BASE_PATH = Path(__file__).resolve().parents[1] / "data" / "career_knowledge_base.json"
_CERTIFICATION_CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "data" / "certification_skill_mappings.json"
_TECHNOLOGY_SPLIT = re.compile(r"[,;|\n\r]+")
_CREDENTIAL_TYPES = {
    "industry_certification",
    "professional_certificate",
    "course_completion",
    "academic_certificate",
}
_EVIDENCE_SKILL_CATEGORIES = {
    "programming_languages",
    "core_technical_skills",
    "frameworks_and_libraries",
    "databases",
    "cloud_and_devops",
    "tools_and_platforms",
    "domain_specific_knowledge",
}

_ALLOCATIONS: dict[str, dict[str, float]] = {
    "student_fresher": {
        "internships": 15.625,
        "projects": 10.9375,
        "certifications": 4.6875,
    },
    "professional": {
        "professional_experience": 15.625,
        "internships": 6.25,
        "projects": 6.25,
        "certifications": 3.125,
    },
}


def load_career_roles() -> list[dict[str, Any]]:
    """Load the existing curated role records without modifying the knowledge base."""
    with _KNOWLEDGE_BASE_PATH.open(encoding="utf-8") as source:
        knowledge_base = json.load(source)
    roles = knowledge_base.get("roles")
    if not isinstance(roles, list):
        raise ValueError("Career knowledge base does not contain a roles list.")
    return roles


def load_certification_catalogue() -> dict[str, Any]:
    """Load and validate certification identities and role-specific evidence."""
    try:
        with _CERTIFICATION_CATALOGUE_PATH.open(encoding="utf-8") as source:
            catalogue = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Certification mapping catalogue could not be loaded.") from exc

    if (
        not isinstance(catalogue, dict)
        or not isinstance(catalogue.get("catalogue_name"), str)
        or not catalogue["catalogue_name"].strip()
        or not isinstance(catalogue.get("version"), str)
        or not catalogue["version"].strip()
        or not isinstance(catalogue.get("certifications"), list)
    ):
        raise ValueError("Certification mapping catalogue has an invalid structure.")
    matching_policy = catalogue.get("matching_policy")
    if (
        not isinstance(matching_policy, Mapping)
        or not isinstance(matching_policy.get("profile_fields"), list)
        or not {"name", "organization"}.issubset(matching_policy["profile_fields"])
        or not isinstance(matching_policy.get("comparison"), str)
        or not matching_policy["comparison"].strip()
        or not isinstance(matching_policy.get("scoring_policy"), str)
        or not matching_policy["scoring_policy"].strip()
    ):
        raise ValueError("Certification catalogue has an invalid matching policy.")
    roles = load_career_roles()
    role_skills: dict[str, set[str]] = {}
    for role in roles:
        if not isinstance(role, Mapping) or not isinstance(role.get("id"), str):
            raise ValueError("Career knowledge base contains a malformed role.")
        skills_by_category = role.get("skills")
        if not isinstance(skills_by_category, Mapping):
            raise ValueError(f"Career role {role['id']!r} has malformed skills.")
        skill_ids: set[str] = set()
        for skills in skills_by_category.values():
            if not isinstance(skills, list):
                raise ValueError(f"Career role {role['id']!r} has a malformed skill category.")
            for skill in skills:
                if not isinstance(skill, Mapping) or not isinstance(skill.get("id"), str):
                    raise ValueError(f"Career role {role['id']!r} has a malformed skill.")
                skill_ids.add(skill["id"])
        role_skills[role["id"]] = skill_ids
    if not role_skills:
        raise ValueError("Career knowledge base has no valid role definitions.")

    seen_ids: set[str] = set()
    for record in catalogue["certifications"]:
        if not isinstance(record, dict):
            raise ValueError("Certification catalogue contains a malformed record.")
        record_id = record.get("id")
        name = record.get("name")
        issuer = record.get("issuing_organization")
        profile_match = record.get("profile_match")
        status = record.get("mapping_status")
        eligible = record.get("scoring_eligible")
        if (
            not isinstance(record_id, str) or not record_id.strip() or record_id in seen_ids
            or not isinstance(name, str) or not name.strip()
            or not isinstance(issuer, str) or not issuer.strip()
        ):
            raise ValueError("Certification catalogue contains an invalid or duplicate identity.")
        seen_ids.add(record_id)
        if (
            not isinstance(profile_match, Mapping)
            or not isinstance(profile_match.get("name"), str)
            or not isinstance(profile_match.get("organization"), str)
            or _normalize(profile_match["name"]) != _normalize(name)
            or _normalize(profile_match["organization"]) != _normalize(issuer)
        ):
            raise ValueError(f"Certification {record_id!r} has incomplete or inconsistent profile-match identity.")
        credential_type = record.get("credential_type")
        if credential_type is not None and credential_type not in _CREDENTIAL_TYPES:
            raise ValueError(f"Certification {record_id!r} has an unsupported credential type.")
        for optional_text in ("platform", "exam_code", "version"):
            value = record.get(optional_text)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Certification {record_id!r} has invalid {optional_text} metadata.")
        for identity_field in ("credential_type", "platform", "exam_code", "version"):
            if identity_field in profile_match:
                profile_value = profile_match[identity_field]
                canonical_value = record.get(identity_field)
                if profile_value is None and canonical_value is None:
                    continue
                if (
                    not isinstance(profile_value, str)
                    or not isinstance(canonical_value, str)
                    or _normalize(profile_value) != _normalize(canonical_value)
                ):
                    raise ValueError(f"Certification {record_id!r} has inconsistent {identity_field} identity metadata.")
        if status not in {"verified", "needs_review"} or not isinstance(eligible, bool):
            raise ValueError(f"Certification {record_id!r} has invalid verification eligibility.")
        if status != "verified" and eligible:
            raise ValueError(f"Certification {record_id!r} cannot be eligible without verified status.")
        role_mappings = record.get("role_mappings")
        if not isinstance(role_mappings, list):
            raise ValueError(f"Certification {record_id!r} has invalid role mappings.")
        seen_role_ids: set[str] = set()
        for role_mapping in role_mappings:
            if not isinstance(role_mapping, Mapping):
                raise ValueError(f"Certification {record_id!r} has a malformed role mapping.")
            role_id = role_mapping.get("role_id")
            skill_ids = role_mapping.get("skill_ids")
            source_url = role_mapping.get("source_url")
            source_section = role_mapping.get("source_section")
            objective_summary = role_mapping.get("objective_summary")
            verification_date = role_mapping.get("verification_date")
            parsed_url = urlparse(source_url) if isinstance(source_url, str) else None
            if (
                not isinstance(role_id, str) or role_id not in role_skills or role_id in seen_role_ids
                or not isinstance(skill_ids, list) or not skill_ids
                or any(not isinstance(skill_id, str) or not skill_id for skill_id in skill_ids)
                or len(skill_ids) != len(set(skill_ids))
                or any(skill_id not in role_skills[role_id] for skill_id in skill_ids)
                or parsed_url is None or parsed_url.scheme != "https" or not parsed_url.netloc
                or not isinstance(source_section, str) or not source_section.strip()
                or not isinstance(objective_summary, str) or not objective_summary.strip()
                or not isinstance(verification_date, str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", verification_date)
            ):
                raise ValueError(f"Certification {record_id!r} has an invalid or unsupported role mapping.")
            seen_role_ids.add(role_id)
        if eligible and not role_mappings:
            raise ValueError(f"Certification {record_id!r} is scoring-eligible without role mappings.")
    return catalogue


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(by_alias=True)
        if isinstance(dumped, Mapping):
            return dumped
    raise TypeError("Profile and role inputs must be mappings or Pydantic models.")


def _field(mapping: Mapping[str, Any], snake: str, camel: str | None = None, default: Any = None) -> Any:
    if snake in mapping:
        return mapping[snake]
    if camel and camel in mapping:
        return mapping[camel]
    return default


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _technology_tokens(value: Any) -> list[str]:
    """Split only on conventional list delimiters; preserve punctuation in names."""
    if not isinstance(value, str):
        return []
    return [part.strip() for part in _TECHNOLOGY_SPLIT.split(value) if part.strip()]


def _resolve_profile(role: Mapping[str, Any] | str) -> dict[str, Any]:
    if isinstance(role, str):
        role_id = role.strip()
        role = next((item for item in load_career_roles() if item.get("id") == role_id), None)
        if role is None:
            raise KeyError(f"Unknown career role ID: {role_id!r}.")
    role_map = _as_mapping(role)
    role_id = role_map.get("id")
    role_name = role_map.get("name")
    skills_by_category = role_map.get("skills")
    if not isinstance(role_id, str) or not isinstance(role_name, str) or not isinstance(skills_by_category, Mapping):
        raise ValueError("Role must contain an ID, name, and categorized skills.")

    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, list[str]] = {}
    for category, skills in skills_by_category.items():
        if not isinstance(skills, list):
            raise ValueError(f"Role skill category {category!r} must be a list.")
        for skill in skills:
            if not isinstance(skill, Mapping):
                raise ValueError(f"Role skill in {category!r} is malformed.")
            skill_id, name = skill.get("id"), skill.get("name")
            if not isinstance(skill_id, str) or not isinstance(name, str):
                raise ValueError(f"Role skill in {category!r} lacks an ID or name.")
            if skill_id in by_id:
                raise ValueError(f"Role {role_id!r} contains duplicate skill ID {skill_id!r}.")
            item = {"id": skill_id, "name": name, "category": category, "importance": skill.get("importance")}
            by_id[skill_id] = item
            by_name.setdefault(_normalize(name), []).append(skill_id)

    # Ambiguous names cannot safely award points. Skill IDs remain unambiguous.
    name_lookup = {name: ids[0] for name, ids in by_name.items() if len(ids) == 1}
    role_map = dict(role_map)
    role_map["_skills_by_id"] = by_id
    role_map["_skills_by_name"] = name_lookup
    return role_map


def _role_requirement_units(role: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build countable requirements while collapsing optional and grouped choices."""
    skills: Mapping[str, Mapping[str, Any]] = role["_skills_by_id"]
    groups = role.get("skill_groups", [])
    if not isinstance(groups, list):
        raise ValueError("Role skill_groups must be a list.")

    excluded: set[str] = set()
    units: list[dict[str, Any]] = []
    seen_group_ids: set[str] = set()
    for group in groups:
        if not isinstance(group, Mapping):
            raise ValueError("Role contains a malformed skill group.")
        group_id = group.get("id")
        member_ids = group.get("skill_ids", [])
        relation = group.get("relationship")
        applicability = group.get("applicability", "optional")
        if not isinstance(member_ids, list) or any(item not in skills for item in member_ids):
            raise ValueError(f"Role skill group {group_id!r} references invalid skills.")
        if group_id in seen_group_ids:
            raise ValueError(f"Role contains duplicate skill group ID {group_id!r}.")
        seen_group_ids.add(group_id)

        if applicability == "conditional":
            # No condition evaluator exists in this P&E component, so do not
            # silently turn conditional skills into universal requirements.
            excluded.update(
                item for item in member_ids
                if skills[item]["category"] in _EVIDENCE_SKILL_CATEGORIES
            )
            continue

        if relation == "alternatives":
            # All members of an alternative group are choices, never separate
            # mandatory requirements. Optional/conditional groups are not scored.
            eligible_members = [
                item for item in dict.fromkeys(member_ids)
                if skills[item]["category"] in _EVIDENCE_SKILL_CATEGORIES
            ]
            excluded.update(eligible_members)
            if applicability == "required":
                minimum = group.get("minimum_options_to_learn", 1)
                if not isinstance(minimum, int) or minimum < 1 or minimum > len(set(member_ids)):
                    raise ValueError(f"Skill group {group_id!r} has an invalid minimum option count.")
                # This evidence source cannot evaluate non-technology choices.
                if minimum > len(eligible_members):
                    continue
                units.append(
                    {
                        "id": f"alternative:{group_id}",
                        "kind": "alternative_group",
                        "skill_ids": eligible_members,
                        "weight": minimum,
                    }
                )
        elif relation == "compatible_stack":
            # Only a stack selected by the role-level requirement is evaluated.
            # It is one choice unit and must be evidenced as a complete stack.
            continue
        elif relation == "complementary":
            continue

    stack_requirement = role.get("technology_stack_requirement")
    if isinstance(stack_requirement, Mapping) and stack_requirement.get("applicability") == "required":
        stack_ids = stack_requirement.get("stack_group_ids", [])
        minimum = stack_requirement.get("minimum_options_to_learn", 1)
        stacks_by_id = {
            group["id"]: group
            for group in groups
            if isinstance(group, Mapping) and group.get("relationship") == "compatible_stack"
        }
        selected_stacks = []
        for stack_id in stack_ids:
            stack = stacks_by_id.get(stack_id)
            if stack is None:
                raise ValueError(f"Role stack requirement references unknown stack {stack_id!r}.")
            stack_skill_ids = list(dict.fromkeys(stack.get("skill_ids", [])))
            if not stack_skill_ids or any(
                skills[item]["category"] not in _EVIDENCE_SKILL_CATEGORIES
                for item in stack_skill_ids
            ):
                continue
            selected_stacks.append(set(stack_skill_ids))
            excluded.update(stack_skill_ids)
        if not selected_stacks:
            minimum = 0
        if not isinstance(minimum, int) or minimum < 1 or minimum > len(selected_stacks):
            # The stack relationship may include non-technology skill evidence;
            # this component omits that requirement rather than inventing a match.
            if minimum != 0:
                raise ValueError("Role technology stack requirement has an invalid minimum option count.")
        else:
            units.append(
                {"id": "required_compatible_stacks", "kind": "stack_group", "stacks": selected_stacks, "weight": minimum}
            )

    for skill_id, skill in skills.items():
        importance = skill.get("importance")
        if importance not in {"core", "important", "specialised", "optional"}:
            raise ValueError(f"Role skill {skill_id!r} has an unsupported importance value.")
        if (
            importance != "optional"
            and skill_id not in excluded
            and skill["category"] in _EVIDENCE_SKILL_CATEGORIES
        ):
            units.append({"id": f"skill:{skill_id}", "kind": "single", "skill_ids": [skill_id], "weight": 1})
    return units


def _match_evidence(
    evidence_items: Sequence[Mapping[str, Any]],
    role: Mapping[str, Any],
    units: Sequence[Mapping[str, Any]],
    *,
    technology_field: str = "technologies",
) -> dict[str, Any]:
    skill_by_id: Mapping[str, Mapping[str, Any]] = role["_skills_by_id"]
    name_lookup: Mapping[str, str] = role["_skills_by_name"]
    ids_by_token = {_normalize(skill_id): skill_id for skill_id in skill_by_id}
    matched_ids: set[str] = set()
    unsupported: set[str] = set()
    tokens_by_item: list[set[str]] = []
    for evidence in evidence_items:
        values = {_normalize(token): token for token in _technology_tokens(evidence.get(technology_field))}
        item_ids: set[str] = set()
        for normalized, original in values.items():
            skill_id = ids_by_token.get(normalized) or name_lookup.get(normalized)
            if skill_id is None:
                unsupported.add(original)
            else:
                item_ids.add(skill_id)
                matched_ids.add(skill_id)
        tokens_by_item.append(item_ids)

    matched_units: list[str] = []
    matched_count = 0
    for unit in units:
        kind = unit["kind"]
        if kind == "single" and unit["skill_ids"][0] in matched_ids:
            matched_units.append(unit["id"])
            matched_count += 1
        elif kind == "alternative_group":
            selected = sorted(matched_ids.intersection(unit["skill_ids"]))[: unit["weight"]]
            matched_units.extend(f"{unit['id']}:{skill_id}" for skill_id in selected)
            matched_count += len(selected)
        elif kind == "stack_group":
            matched_stacks = [
                index
                for index, stack in enumerate(unit["stacks"])
                if stack and any(stack.issubset(item_ids) for item_ids in tokens_by_item)
            ][: unit["weight"]]
            matched_units.extend(f"{unit['id']}:{index}" for index in matched_stacks)
            matched_count += len(matched_stacks)

    matched_role_skills = [
        {"id": skill_id, "name": skill_by_id[skill_id]["name"]}
        for skill_id in sorted(matched_ids, key=lambda item: (skill_by_id[item]["name"].casefold(), item))
    ]
    return {
        "matched_role_skills": matched_role_skills,
        "unsupported_technologies": sorted(unsupported, key=str.casefold),
        "matched_requirement_units": matched_units,
        "matched_count": matched_count,
        "evaluated_count": sum(unit["weight"] for unit in units),
    }


def _score_category(max_points: float, match: Mapping[str, Any], *, evidence_count: int) -> dict[str, Any]:
    total = match["evaluated_count"]
    if total == 0:
        earned: float | None = None
        status = "unavailable_no_evaluated_role_skills"
    else:
        earned = round(max_points * match["matched_count"] / total, 4) if evidence_count else 0.0
        status = "scored" if evidence_count else "empty"
    return {
        "maximum_points": max_points,
        "earned_points": earned,
        "status": status,
        "evidence_count": evidence_count,
        "matched_role_skills": match["matched_role_skills"],
        "matched_requirement_units": match["matched_requirement_units"],
        "evaluated_requirement_units": total,
        "unsupported_technologies": match["unsupported_technologies"],
    }


def _redistribute_inapplicable_allocations(
    allocations: Mapping[str, float], inapplicable_categories: Sequence[str]
) -> dict[str, float]:
    """Redistribute only explicitly inapplicable, positively allocated categories."""
    result = dict(allocations)
    removed = sum(result.pop(category, 0.0) for category in set(inapplicable_categories))
    remaining = sum(result.values())
    if removed and remaining:
        for category, points in result.items():
            result[category] = points + removed * points / remaining
    return result


def _first_field(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _credential_identity(credential: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": _first_field(credential, "name"),
        "issuing_organization": _first_field(credential, "organization", "issuing_organization", "issuingOrganization"),
        "credential_type": _first_field(credential, "credential_type", "credentialType"),
        "platform": _first_field(credential, "platform"),
        "exam_code": _first_field(credential, "exam_code", "examCode"),
        "version": _first_field(credential, "version"),
    }


def _credential_matches_record(identity: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    profile_match = record["profile_match"]
    name = identity.get("name")
    issuer = identity.get("issuing_organization")
    if (
        not isinstance(name, str) or not name.strip()
        or not isinstance(issuer, str) or not issuer.strip()
        or _normalize(name) != _normalize(profile_match["name"])
        or _normalize(issuer) != _normalize(profile_match["organization"])
    ):
        return False

    for field in ("credential_type", "platform", "exam_code", "version"):
        supplied = identity.get(field)
        if supplied is None or (isinstance(supplied, str) and not supplied.strip()):
            continue
        expected = record.get(field)
        if (
            not isinstance(supplied, str)
            or not isinstance(expected, str)
            or _normalize(supplied) != _normalize(expected)
        ):
            return False
    return True


def _certification_evidence(
    certifications: Sequence[Mapping[str, Any]],
    role: Mapping[str, Any],
    units: Sequence[Mapping[str, Any]],
    catalogue: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve profile credentials against exact, validated catalogue identities."""
    role_id = role["id"]
    skill_by_id: Mapping[str, Mapping[str, Any]] = role["_skills_by_id"]
    evaluated_skill_ids: set[str] = set()
    for unit in units:
        evaluated_skill_ids.update(unit.get("skill_ids", []))
        for stack in unit.get("stacks", []):
            evaluated_skill_ids.update(stack)
    role_mapping_available = any(
        record.get("mapping_status") == "verified"
        and record.get("scoring_eligible") is True
        and any(
            mapping.get("role_id") == role_id
            and evaluated_skill_ids.intersection(mapping.get("skill_ids", []))
            for mapping in record.get("role_mappings", [])
            if isinstance(mapping, Mapping)
        )
        for record in catalogue["certifications"]
        if isinstance(record, Mapping)
    )

    details: dict[str, list[dict[str, Any]]] = {
        "eligible_credentials": [],
        "unverified_credentials": [],
        "ambiguous_credentials": [],
        "unmatched_credentials": [],
        "no_role_mapping_credentials": [],
        "no_evaluated_requirement_mappings": [],
    }
    eligible_by_id: dict[str, dict[str, Any]] = {}

    for credential_value in certifications:
        credential = _as_mapping(credential_value)
        identity = _credential_identity(credential)
        name = identity.get("name") if isinstance(identity.get("name"), str) else ""
        issuer = identity.get("issuing_organization") if isinstance(identity.get("issuing_organization"), str) else ""
        candidates = [
            record for record in catalogue["certifications"]
            if _credential_matches_record(identity, record)
        ]
        summary = {
            "name": name,
            "issuing_organization": issuer,
            "credential_type": identity.get("credential_type"),
        }
        if not candidates:
            details["unmatched_credentials"].append({**summary, "reason": "no_exact_catalogue_identity"})
            continue
        if len(candidates) > 1:
            details["ambiguous_credentials"].append({
                **summary,
                "catalogue_ids": sorted(record["id"] for record in candidates),
                "reason": "available_identity_fields_match_multiple_catalogue_records",
            })
            continue

        record = candidates[0]
        if record["mapping_status"] != "verified" or not record["scoring_eligible"]:
            details["unverified_credentials"].append({
                **summary,
                "catalogue_id": record["id"],
                "reason": record.get("mapping_status_reason", "mapping_not_eligible_for_scoring"),
            })
            continue

        role_mapping = next(
            (item for item in record["role_mappings"] if item["role_id"] == role_id),
            None,
        )
        if role_mapping is None:
            details["no_role_mapping_credentials"].append({
                **summary,
                "catalogue_id": record["id"],
                "reason": "no_verified_mapping_for_evaluated_role",
            })
            continue

        mapped_ids = set(role_mapping["skill_ids"])
        evaluated_ids = mapped_ids.intersection(evaluated_skill_ids)
        if not evaluated_ids:
            details["no_evaluated_requirement_mappings"].append({
                **summary,
                "catalogue_id": record["id"],
                "reason": "role_mapping_does_not_cover_evaluated_requirements",
            })
            continue

        eligible_by_id.setdefault(record["id"], {
            "record": record,
            "role_mapping": role_mapping,
            "skill_ids": evaluated_ids,
        })

    eligible_credentials: list[dict[str, Any]] = []
    for record_id in sorted(eligible_by_id):
        item = eligible_by_id[record_id]
        record = item["record"]
        eligible_credentials.append({
            "catalogue_id": record_id,
            "name": record["name"],
            "issuing_organization": record["issuing_organization"],
            "credential_type": record.get("credential_type") or "unknown",
            "platform": record.get("platform"),
            "mapped_evaluated_skill_ids": sorted(item["skill_ids"]),
        })
        details["eligible_credentials"].append(eligible_credentials[-1])

    if not eligible_by_id:
        if not certifications:
            unavailable_reason = "no_certification_evidence"
        elif details["ambiguous_credentials"]:
            unavailable_reason = "ambiguous_credential_identity"
        elif details["unverified_credentials"]:
            unavailable_reason = "no_verified_score_eligible_mapping"
        elif details["no_role_mapping_credentials"]:
            unavailable_reason = "no_verified_mapping_for_evaluated_role"
        elif details["no_evaluated_requirement_mappings"]:
            unavailable_reason = "no_mapping_for_evaluated_requirements"
        else:
            unavailable_reason = "no_exact_catalogue_identity"
        return {
            **details,
            "available": role_mapping_available,
            "unavailable_reason": None if role_mapping_available else unavailable_reason,
            "role_mapping_available": role_mapping_available,
            "matched_skill_ids": [],
            "matched_role_skills": [],
            "matched_requirement_units": [],
            "matched_count": 0,
            "evaluated_count": sum(unit["weight"] for unit in units),
            "verified_mapping_sources": [],
        }

    matched_ids = set().union(*(item["skill_ids"] for item in eligible_by_id.values()))
    credential_skill_sets = [item["skill_ids"] for item in eligible_by_id.values()]
    matched_units: list[str] = []
    matched_skill_ids: set[str] = set()
    matched_count = 0
    for unit in units:
        kind = unit["kind"]
        if kind == "single" and unit["skill_ids"][0] in matched_ids:
            skill_id = unit["skill_ids"][0]
            matched_units.append(unit["id"])
            matched_skill_ids.add(skill_id)
            matched_count += 1
        elif kind == "alternative_group":
            selected = sorted(matched_ids.intersection(unit["skill_ids"]))[:unit["weight"]]
            matched_units.extend(f"{unit['id']}:{skill_id}" for skill_id in selected)
            matched_skill_ids.update(selected)
            matched_count += len(selected)
        elif kind == "stack_group":
            matched_stacks = [
                (index, stack)
                for index, stack in enumerate(unit["stacks"])
                if stack and any(stack.issubset(item_ids) for item_ids in credential_skill_sets)
            ][:unit["weight"]]
            matched_units.extend(f"{unit['id']}:{index}" for index, _ in matched_stacks)
            for _, stack in matched_stacks:
                matched_skill_ids.update(stack)
            matched_count += len(matched_stacks)

    sources = []
    for record_id in sorted(eligible_by_id):
        record = eligible_by_id[record_id]["record"]
        role_mapping = eligible_by_id[record_id]["role_mapping"]
        sources.append({
            "catalogue_id": record_id,
            "role_id": role_id,
            "source_url": role_mapping["source_url"],
            "source_section": role_mapping["source_section"],
            "verification_date": role_mapping["verification_date"],
        })

    details["eligible_credentials"] = eligible_credentials
    return {
        **details,
        "available": True,
        "role_mapping_available": True,
        "unavailable_reason": None,
        "matched_skill_ids": sorted(matched_skill_ids),
        "matched_role_skills": [
            {"id": skill_id, "name": skill_by_id[skill_id]["name"]}
            for skill_id in sorted(matched_skill_ids, key=lambda item: (skill_by_id[item]["name"].casefold(), item))
        ],
        "matched_requirement_units": matched_units,
        "matched_count": matched_count,
        "evaluated_count": sum(unit["weight"] for unit in units),
        "verified_mapping_sources": sources,
    }


def score_projects_experience(profile: Any, role: Mapping[str, Any] | str) -> dict[str, Any]:
    """Score projects, experience, and exact verified catalogue-backed credentials."""
    profile_map = _as_mapping(profile)
    role_map = _resolve_profile(role)
    career_skills = _as_mapping(_field(profile_map, "career_skills", "careerSkills", {}))
    additional = _as_mapping(_field(profile_map, "additional_information", "additionalInformation", {}))
    status = _field(career_skills, "career_status", "careerStatus")
    if status not in {"Student", "Fresher", "Experienced"}:
        raise ValueError("Profile careerStatus must be Student, Fresher, or Experienced.")
    candidate_category = "professional" if status == "Experienced" else "student_fresher"
    original_allocations = dict(_ALLOCATIONS[candidate_category])
    inapplicable = ["professional_experience"] if candidate_category == "student_fresher" else []

    internships = _field(career_skills, "internship_experience", "internshipExperience", []) or []
    professional = _field(career_skills, "professional_experience", "professionalExperience", []) or []
    projects = _field(additional, "projects", default=[]) or []
    certifications = _field(additional, "certifications", default=[]) or []
    internship_maps = [_as_mapping(item) for item in internships]
    professional_maps = [_as_mapping(item) for item in professional]
    project_maps = [_as_mapping(item) for item in projects]
    certification_maps = [_as_mapping(item) for item in certifications]

    units = _role_requirement_units(role_map)
    catalogue = load_certification_catalogue()
    certification_match = _certification_evidence(certification_maps, role_map, units, catalogue)
    certification_mapping_available = certification_match["available"]

    removed_categories = list(inapplicable)
    if not certification_mapping_available:
        removed_categories.append("certifications")
    allocations = _redistribute_inapplicable_allocations(original_allocations, removed_categories)
    removed_allocation = sum(original_allocations.get(category, 0.0) for category in set(removed_categories))
    redistributed_to = {
        category: round(max(0.0, allocations[category] - original_allocations.get(category, 0.0)), 4)
        for category in allocations
        if allocations[category] > original_allocations.get(category, 0.0)
    }
    points_redistributed = round(removed_allocation, 4) if redistributed_to else 0.0

    category_specs = {
        "internships": internship_maps,
        "projects": project_maps,
    }
    if candidate_category == "professional":
        category_specs["professional_experience"] = professional_maps
    result_categories: dict[str, Any] = {}
    for category, evidence in category_specs.items():
        match = _match_evidence(evidence, role_map, units)
        result_categories[category] = _score_category(
            allocations[category], match, evidence_count=len(evidence)
        )

    original_certification_allocation = original_allocations["certifications"]
    if certification_mapping_available:
        cert_match = {
            "matched_role_skills": certification_match["matched_role_skills"],
            "matched_requirement_units": certification_match["matched_requirement_units"],
            "evaluated_count": certification_match["evaluated_count"],
            "matched_count": certification_match["matched_count"],
            "unsupported_technologies": [],
        }
        certification_category = _score_category(
            original_certification_allocation,
            cert_match,
            evidence_count=len(certification_maps),
        )
        if certification_maps and not certification_match["eligible_credentials"]:
            certification_category["status"] = "no_eligible_credentials"
        certification_category.update({
            "matched_skill_ids": certification_match["matched_skill_ids"],
            "matched_count": certification_match["matched_count"],
            "eligible_credentials": certification_match["eligible_credentials"],
            "eligible_evidence_count": len(certification_match["eligible_credentials"]),
            "unverified_credentials": certification_match["unverified_credentials"],
            "ambiguous_credentials": certification_match["ambiguous_credentials"],
            "unmatched_credentials": certification_match["unmatched_credentials"],
            "no_role_mapping_credentials": certification_match["no_role_mapping_credentials"],
            "no_evaluated_requirement_mappings": certification_match["no_evaluated_requirement_mappings"],
            "verified_mapping_sources": certification_match["verified_mapping_sources"],
            "certification_contribution_points": certification_category["earned_points"],
            "original_allocation_points": original_certification_allocation,
        })
    else:
        certification_category = {
            "maximum_points": 0.0,
            "original_allocation_points": original_certification_allocation,
            "earned_points": None,
            "status": "unavailable_no_score_eligible_verified_mappings",
            "evidence_count": len(certification_maps),
            "matched_role_skills": [],
            "matched_skill_ids": [],
            "matched_count": 0,
            "matched_requirement_units": [],
            "evaluated_requirement_units": certification_match["evaluated_count"],
            "eligible_credentials": [],
            "eligible_evidence_count": 0,
            "unverified_credentials": certification_match["unverified_credentials"],
            "ambiguous_credentials": certification_match["ambiguous_credentials"],
            "unmatched_credentials": certification_match["unmatched_credentials"],
            "no_role_mapping_credentials": certification_match["no_role_mapping_credentials"],
            "no_evaluated_requirement_mappings": certification_match["no_evaluated_requirement_mappings"],
            "verified_mapping_sources": [],
            "certification_contribution_points": None,
            "unavailable_reason": certification_match["unavailable_reason"],
            "reason": "No supplied credential has a verified, scoring-eligible mapping to evaluated requirements for this role; this is unavailable evidence, not a confirmed skill gap.",
        }
    result_categories["certifications"] = certification_category

    if "professional_experience" not in result_categories:
        result_categories["professional_experience"] = {
            "maximum_points": 0.0,
            "earned_points": 0.0,
            "status": "inapplicable",
            "evidence_count": 0,
            "matched_role_skills": [],
            "matched_requirement_units": [],
            "evaluated_requirement_units": len(units),
            "unsupported_technologies": [],
            "reason": "The approved Student/Fresher allocation excludes professional experience.",
        }

    scoreable_categories = [
        item for item in result_categories.values() if item["earned_points"] is not None
    ]
    total_earned = round(sum(item["earned_points"] for item in scoreable_categories), 4)
    evaluated_max = round(sum(item["maximum_points"] for item in scoreable_categories), 4)
    unavailable_max = round(
        sum(item["maximum_points"] for item in result_categories.values() if item["earned_points"] is None),
        4,
    )
    effective_allocations = dict(allocations)
    return {
        "role_id": role_map["id"],
        "role_name": role_map["name"],
        "evaluated_role_skill_categories": sorted(_EVIDENCE_SKILL_CATEGORIES),
        "candidate_category": candidate_category,
        "maximum_points": 31.25,
        "categories": result_categories,
        "inapplicable_categories": inapplicable,
        "redistribution": {
            "applied": points_redistributed > 0,
            "points_redistributed": points_redistributed,
            "unavailable_categories": ["certifications"] if not certification_mapping_available else [],
            "effective_category_allocations": effective_allocations,
            "allocation_before_redistribution": {
                category: points for category, points in original_allocations.items()
            },
            "allocation_removed": round(removed_allocation, 4),
            "redistributed_to": redistributed_to,
            "note": "Unavailable certification allocation is redistributed once in proportion to the remaining applicable evidence allocations. Student/Fresher professional experience has no original allocation to redistribute.",
        },
        "certification_mapping_available": certification_mapping_available,
        "total_points": total_earned,
        "evaluated_maximum_points": evaluated_max,
        "unavailable_maximum_points": unavailable_max,
        "score_status": "partial" if unavailable_max else "complete",
        "calculation": (
            "For each scored category: effective category allocation × matched distinct evaluated requirement units ÷ "
            "evaluated requirement units. Certification mappings must match an exact credential and issuer, be verified "
            "and scoring-eligible, and apply to this role. Repeated credential mappings and skill IDs count once. "
            "Alternative choices count up to their configured minimum; a required compatible stack counts only when all "
            "its mapped skills occur within one credential mapping. Unavailable certification allocation is redistributed "
            "proportionally once. Project and experience evidence scoring is unchanged."
        ),
    }
