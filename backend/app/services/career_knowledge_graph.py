"""Load and validate the CareerCoach AI career knowledge graph."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import networkx as nx


_KNOWLEDGE_BASE_PATH = Path(__file__).resolve().parents[1] / "data" / "career_knowledge_base.json"
_VALID_GROUP_RELATIONSHIPS = {"alternatives", "complementary", "compatible_stack"}
_VALID_APPLICABILITY = {"required", "optional", "conditional"}


def _role_node_id(role_id: str) -> str:
    return f"role:{role_id}"


def _skill_node_id(skill_id: str) -> str:
    return f"skill:{skill_id}"


def _domain_node_id(domain_id: str) -> str:
    return f"domain:{domain_id}"


def _group_node_id(role_id: str, group_id: str) -> str:
    """Return a role-scoped group node ID so same-named groups never merge."""
    return f"group:{role_id}:{group_id}"


def _iter_role_skills(role: dict[str, Any]):
    for category, skills in role.get("skills", {}).items():
        for skill in skills:
            yield category, skill


def load_knowledge_base() -> dict[str, Any]:
    """Load the existing career knowledge base relative to this module."""
    if not _KNOWLEDGE_BASE_PATH.is_file():
        raise FileNotFoundError(f"Career knowledge base was not found: {_KNOWLEDGE_BASE_PATH}")

    try:
        with _KNOWLEDGE_BASE_PATH.open("r", encoding="utf-8") as file:
            knowledge_base = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Career knowledge base contains invalid JSON at line {exc.lineno}, column {exc.colno}."
        ) from exc

    if not isinstance(knowledge_base, dict):
        raise ValueError("Career knowledge base must contain a JSON object at its top level.")
    return knowledge_base


def build_career_graph(knowledge_base: dict[str, Any]) -> nx.MultiDiGraph:
    """Build a directed multigraph from role, domain, skill, and group records.

    Skill nodes are shared by stable skill ID. Role-specific importance,
    relevance, and category values stay on each role-to-skill edge.
    """
    if not isinstance(knowledge_base, dict):
        raise TypeError("knowledge_base must be a dictionary.")

    domains = knowledge_base.get("domains")
    roles = knowledge_base.get("roles")
    if not isinstance(domains, list) or not isinstance(roles, list):
        raise ValueError("Knowledge base must contain 'domains' and 'roles' arrays.")

    graph = nx.MultiDiGraph(
        knowledge_base_name=knowledge_base.get("knowledge_base_name"),
        knowledge_base_version=knowledge_base.get("version"),
        total_skill_entries=0,
        node_id_convention="role:<role_id>, skill:<skill_id>, domain:<domain_id>, group:<role_id>:<group_id>",
    )

    domain_ids: set[str] = set()
    role_domain_by_id: dict[str, str] = {}
    for domain in domains:
        domain_id = domain.get("id")
        domain_name = domain.get("name")
        if not domain_id or not domain_name or domain_id in domain_ids:
            raise ValueError(f"Invalid or duplicate career domain record: {domain!r}")
        domain_ids.add(domain_id)
        graph.add_node(
            _domain_node_id(domain_id),
            node_type="career_domain",
            domain_id=domain_id,
            domain_name=domain_name,
        )
        for role_id in domain.get("role_ids", []):
            if role_id in role_domain_by_id:
                raise ValueError(f"Role {role_id!r} is assigned to more than one career domain.")
            role_domain_by_id[role_id] = domain_id

    skill_definitions: dict[str, dict[str, Any]] = {}
    skill_categories: dict[str, set[str]] = {}
    role_ids: set[str] = set()
    scoped_group_ids: set[tuple[str, str]] = set()

    # Add career roles and shared skill nodes, along with role-specific edges.
    for role in roles:
        role_id = role.get("id")
        role_name = role.get("name")
        if not role_id or not role_name or role_id in role_ids:
            raise ValueError(f"Invalid or duplicate career role record: {role!r}")
        role_ids.add(role_id)

        domain_id = role_domain_by_id.get(role_id)
        if domain_id is None:
            raise ValueError(f"Career role {role_id!r} is not assigned to a domain.")
        domain = next(item for item in domains if item["id"] == domain_id)
        if role.get("career_domain") != domain.get("name"):
            raise ValueError(
                f"Career role {role_id!r} has a career_domain value inconsistent with its domain record."
            )

        role_node = _role_node_id(role_id)
        role_attributes: dict[str, Any] = {
            "node_type": "career_role",
            "role_id": role_id,
            "role_name": role_name,
            "domain": domain["name"],
            "description": role.get("description", ""),
        }
        # Preserve the explicit role-level one-of compatible stack requirement.
        if "technology_stack_requirement" in role:
            role_attributes["technology_stack_requirement"] = role["technology_stack_requirement"]
        graph.add_node(role_node, **role_attributes)
        graph.add_edge(
            role_node,
            _domain_node_id(domain_id),
            key="belongs_to_domain",
            edge_type="belongs_to_domain",
        )

        for category, skill in _iter_role_skills(role):
            skill_id = skill.get("id")
            skill_name = skill.get("name")
            if not skill_id or not skill_name:
                raise ValueError(f"Role {role_id!r} contains a skill without an ID or name.")

            existing = skill_definitions.get(skill_id)
            if existing and existing["name"] != skill_name:
                raise ValueError(
                    f"Shared skill ID {skill_id!r} has inconsistent names: "
                    f"{existing['name']!r} and {skill_name!r}."
                )
            skill_definitions.setdefault(skill_id, {"name": skill_name})
            skill_categories.setdefault(skill_id, set()).add(category)

            graph.add_edge(
                role_node,
                _skill_node_id(skill_id),
                key="has_skill",
                edge_type="has_skill",
                skill_id=skill_id,
                category=category,
                importance=skill.get("importance"),
                why_relevant=skill.get("why_relevant"),
            )
            graph.graph["total_skill_entries"] += 1

    if set(role_domain_by_id) != role_ids:
        unknown = sorted(set(role_domain_by_id) - role_ids)
        raise ValueError(f"Domain records reference unknown career roles: {unknown}")

    for skill_id, definition in skill_definitions.items():
        categories = sorted(skill_categories[skill_id])
        graph.add_node(
            _skill_node_id(skill_id),
            node_type="skill",
            skill_id=skill_id,
            skill_name=definition["name"],
            category=categories[0] if len(categories) == 1 else categories,
            categories=categories,
        )

    # Add role-scoped group nodes and their memberships.
    for role in roles:
        role_id = role["id"]
        for group in role.get("skill_groups", []):
            group_id = group.get("id")
            relationship = group.get("relationship")
            if not group_id or (role_id, group_id) in scoped_group_ids:
                raise ValueError(f"Missing or duplicate role-scoped skill group ID: {role_id}/{group_id}")
            if relationship not in _VALID_GROUP_RELATIONSHIPS:
                raise ValueError(
                    f"Skill group {role_id}/{group_id} has unsupported relationship {relationship!r}."
                )
            if group.get("applicability") not in _VALID_APPLICABILITY:
                raise ValueError(f"Skill group {role_id}/{group_id} has invalid applicability metadata.")
            if group["applicability"] == "conditional" and not group.get("condition", "").strip():
                raise ValueError(f"Conditional skill group {role_id}/{group_id} has no condition.")

            refs = group.get("skill_ids")
            if not isinstance(refs, list) or not refs:
                raise ValueError(f"Skill group {role_id}/{group_id} has no skill references.")
            if relationship == "alternatives":
                minimum = group.get("minimum_options_to_learn")
                if not isinstance(minimum, int) or not 1 <= minimum <= len(refs):
                    raise ValueError(f"Alternative skill group {role_id}/{group_id} has an invalid minimum.")

            group_node = _group_node_id(role_id, group_id)
            scoped_group_ids.add((role_id, group_id))
            # Keep original group attributes, including any future meaningful metadata.
            group_attributes = dict(group)
            group_attributes.update(
                {
                    "node_type": "skill_group",
                    "role_id": role_id,
                    "group_id": group_id,
                    "group_name": group.get("name", group_id),
                    "relationship": relationship,
                    "category": group.get("category"),
                    "categories": group.get("categories", [group["category"]] if group.get("category") else []),
                    "applicability": group["applicability"],
                }
            )
            graph.add_node(group_node, **group_attributes)
            graph.add_edge(
                _role_node_id(role_id),
                group_node,
                key="has_skill_group",
                edge_type="has_skill_group",
                relationship=relationship,
                applicability=group["applicability"],
            )

            seen_refs: set[str] = set()
            for skill_id in refs:
                if skill_id in seen_refs:
                    raise ValueError(f"Skill group {role_id}/{group_id} repeats skill ID {skill_id!r}.")
                seen_refs.add(skill_id)
                if skill_id not in skill_definitions:
                    raise ValueError(
                        f"Skill group {role_id}/{group_id} references unknown skill ID {skill_id!r}."
                    )
                graph.add_edge(
                    group_node,
                    _skill_node_id(skill_id),
                    key="contains_skill",
                    edge_type="contains_skill",
                    relationship=relationship,
                    group_id=group_id,
                )

    # The backend catalogue requires one compatible stack; model each listed
    # stack as an option without treating all six stack groups as mandatory.
    for role in roles:
        requirement = role.get("technology_stack_requirement")
        if not requirement:
            continue
        role_id = role["id"]
        stack_ids = requirement.get("stack_group_ids", [])
        if requirement.get("relationship") != "alternatives":
            raise ValueError(f"Role {role_id!r} has an unsupported technology stack requirement.")
        minimum = requirement.get("minimum_options_to_learn")
        if not isinstance(minimum, int) or not 1 <= minimum <= len(stack_ids):
            raise ValueError(f"Role {role_id!r} has an invalid technology stack minimum.")
        for group_id in stack_ids:
            group_node = _group_node_id(role_id, group_id)
            if not graph.has_node(group_node) or graph.nodes[group_node].get("relationship") != "compatible_stack":
                raise ValueError(f"Role {role_id!r} references an unknown compatible stack {group_id!r}.")
            graph.add_edge(
                _role_node_id(role_id),
                group_node,
                key="has_compatible_stack_option",
                edge_type="has_compatible_stack_option",
                applicability=requirement.get("applicability"),
                minimum_options_to_learn=minimum,
                explanation=requirement.get("explanation"),
            )

    return graph


def validate_career_graph(
    graph: nx.MultiDiGraph, knowledge_base: dict[str, Any]
) -> dict[str, Any]:
    """Validate graph nodes, edges, group metadata, and source references."""
    errors: list[str] = []
    if not isinstance(graph, nx.MultiDiGraph):
        return {"valid": False, "errors": ["Graph must be a NetworkX MultiDiGraph."]}

    roles = knowledge_base.get("roles", [])
    domains = knowledge_base.get("domains", [])
    expected_roles = {_role_node_id(role["id"]) for role in roles}
    expected_domains = {_domain_node_id(domain["id"]) for domain in domains}
    expected_skills = {
        _skill_node_id(skill["id"])
        for role in roles
        for _, skill in _iter_role_skills(role)
    }
    expected_groups = {
        _group_node_id(role["id"], group["id"]): (role, group)
        for role in roles
        for group in role.get("skill_groups", [])
    }
    expected_by_type = {
        "career_role": expected_roles,
        "career_domain": expected_domains,
        "skill": expected_skills,
        "skill_group": set(expected_groups),
    }

    for node_type, expected in expected_by_type.items():
        actual = {node for node, attrs in graph.nodes(data=True) if attrs.get("node_type") == node_type}
        if actual != expected:
            errors.append(
                f"{node_type} nodes mismatch; missing={sorted(expected - actual)}, "
                f"unexpected={sorted(actual - expected)}."
            )

    def has_edge(source: str, target: str, edge_type: str) -> bool:
        return graph.has_edge(source, target) and any(
            attrs.get("edge_type") == edge_type
            for attrs in graph.get_edge_data(source, target, default={}).values()
        )

    domain_for_role = {
        role_id: domain["id"]
        for domain in domains
        for role_id in domain.get("role_ids", [])
    }
    for role in roles:
        role_id = role["id"]
        role_node = _role_node_id(role_id)
        domain_id = domain_for_role.get(role_id)
        if domain_id and not has_edge(role_node, _domain_node_id(domain_id), "belongs_to_domain"):
            errors.append(f"Career role {role_id!r} is missing its domain edge.")
        for category, skill in _iter_role_skills(role):
            skill_node = _skill_node_id(skill["id"])
            if not has_edge(role_node, skill_node, "has_skill"):
                errors.append(f"Career role {role_id!r} is missing skill edge {skill['id']!r}.")
                continue
            edge_values = graph.get_edge_data(role_node, skill_node, default={}).values()
            matching = next((e for e in edge_values if e.get("edge_type") == "has_skill"), {})
            if matching.get("category") != category or matching.get("importance") != skill.get("importance") or matching.get("why_relevant") != skill.get("why_relevant"):
                errors.append(f"Role-specific metadata was lost for {role_id}/{skill['id']}.")

        for group in role.get("skill_groups", []):
            group_node = _group_node_id(role_id, group["id"])
            node = graph.nodes.get(group_node)
            if not node:
                errors.append(f"Missing group node {role_id}/{group['id']}.")
                continue
            for key in ("relationship", "applicability", "skill_ids", "explanation"):
                if node.get(key) != group.get(key):
                    errors.append(f"Group metadata {key!r} was lost for {role_id}/{group['id']}.")
            if group.get("minimum_options_to_learn") is not None and node.get("minimum_options_to_learn") != group["minimum_options_to_learn"]:
                errors.append(f"Group minimum was lost for {role_id}/{group['id']}.")
            if group.get("condition") is not None and node.get("condition") != group["condition"]:
                errors.append(f"Group condition was lost for {role_id}/{group['id']}.")
            if not has_edge(role_node, group_node, "has_skill_group"):
                errors.append(f"Missing role-to-group edge {role_id}/{group['id']}.")
            for skill_id in group.get("skill_ids", []):
                skill_node = _skill_node_id(skill_id)
                if not has_edge(group_node, skill_node, "contains_skill"):
                    errors.append(f"Missing group member edge {role_id}/{group['id']} -> {skill_id}.")
                else:
                    data = next(iter(graph.get_edge_data(group_node, skill_node).values()))
                    if data.get("relationship") != group.get("relationship"):
                        errors.append(f"Group relationship type was lost for {role_id}/{group['id']}.")

        requirement = role.get("technology_stack_requirement")
        if requirement:
            role_attrs = graph.nodes.get(role_node, {})
            if role_attrs.get("technology_stack_requirement") != requirement:
                errors.append(f"Role-level stack requirement was lost for {role_id}.")
            for group_id in requirement.get("stack_group_ids", []):
                stack_node = _group_node_id(role_id, group_id)
                if not has_edge(role_node, stack_node, "has_compatible_stack_option"):
                    errors.append(f"Missing compatible stack option edge {role_id}/{group_id}.")

    invalid_edge_nodes = [
        (source, target)
        for source, target in graph.edges()
        if source not in graph or target not in graph
    ]
    if invalid_edge_nodes:
        errors.append(f"Graph contains edges with missing endpoint nodes: {invalid_edge_nodes}.")

    return {"valid": not errors, "errors": errors}


def get_graph_summary(graph: nx.MultiDiGraph) -> dict[str, Any]:
    """Return node, edge, and relationship counts for a career graph."""
    node_counts = Counter(attrs.get("node_type", "unknown") for _, attrs in graph.nodes(data=True))
    relationship_counts = Counter(attrs.get("edge_type", "unknown") for _, _, attrs in graph.edges(data=True))
    return {
        "total_nodes": graph.number_of_nodes(),
        "total_edges": graph.number_of_edges(),
        "career_role_nodes": node_counts["career_role"],
        "skill_nodes": node_counts["skill"],
        "career_domain_nodes": node_counts["career_domain"],
        "alternative_group_nodes": sum(
            1 for _, attrs in graph.nodes(data=True)
            if attrs.get("node_type") == "skill_group" and attrs.get("relationship") == "alternatives"
        ),
        "complementary_group_nodes": sum(
            1 for _, attrs in graph.nodes(data=True)
            if attrs.get("node_type") == "skill_group" and attrs.get("relationship") == "complementary"
        ),
        "compatible_stack_group_nodes": sum(
            1 for _, attrs in graph.nodes(data=True)
            if attrs.get("node_type") == "skill_group" and attrs.get("relationship") == "compatible_stack"
        ),
        "skill_entries_across_roles": graph.graph.get("total_skill_entries", 0),
        "relationship_counts": dict(sorted(relationship_counts.items())),
    }


def main() -> int:
    """Build, validate, and print a summary when run as a script."""
    knowledge_base = load_knowledge_base()
    graph = build_career_graph(knowledge_base)
    validation = validate_career_graph(graph, knowledge_base)
    print(json.dumps(get_graph_summary(graph), indent=2, sort_keys=True))
    print(f"Validation: {'PASS' if validation['valid'] else 'FAIL'}")
    if validation["errors"]:
        for error in validation["errors"]:
            print(f"- {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
