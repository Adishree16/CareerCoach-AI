"""Reusable queries over the CareerCoach AI NetworkX knowledge graph."""

from __future__ import annotations

from typing import Any

import networkx as nx


_APPLICABILITY_VALUES = {"required", "optional", "conditional"}


def _validate_graph(graph: nx.MultiDiGraph) -> None:
    if not isinstance(graph, nx.MultiDiGraph):
        raise TypeError("graph must be a NetworkX MultiDiGraph.")


def _validate_identifier(value: str, label: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string.")
    if not value.strip():
        raise ValueError(f"{label} must not be empty.")


def _role_node(graph: nx.MultiDiGraph, role_id: str) -> str:
    _validate_identifier(role_id, "role_id")
    node_id = f"role:{role_id}"
    if node_id not in graph or graph.nodes[node_id].get("node_type") != "career_role":
        raise KeyError(f"Unknown career role ID: {role_id!r}.")
    return node_id


def _skill_node(graph: nx.MultiDiGraph, skill_id: str) -> str:
    _validate_identifier(skill_id, "skill_id")
    node_id = f"skill:{skill_id}"
    if node_id not in graph or graph.nodes[node_id].get("node_type") != "skill":
        raise KeyError(f"Unknown skill ID: {skill_id!r}.")
    return node_id


def _typed_edges(
    graph: nx.MultiDiGraph, node_id: str, direction: str, edge_type: str
) -> list[tuple[str, dict[str, Any]]]:
    """Return adjacent node IDs and edge data for one explicit edge type."""
    if direction == "out":
        edges = graph.out_edges(node_id, keys=True, data=True)
        return [(target, data) for _, target, _, data in edges if data.get("edge_type") == edge_type]
    if direction == "in":
        edges = graph.in_edges(node_id, keys=True, data=True)
        return [(source, data) for source, _, _, data in edges if data.get("edge_type") == edge_type]
    raise ValueError("direction must be either 'in' or 'out'.")


def _role_groups(graph: nx.MultiDiGraph, role_node: str) -> list[str]:
    group_nodes = [
        node_id for node_id, _ in _typed_edges(graph, role_node, "out", "has_skill_group")
    ]
    for group_node in group_nodes:
        if group_node not in graph or graph.nodes[group_node].get("node_type") != "skill_group":
            raise ValueError(f"Role {role_node!r} has an invalid skill-group edge to {group_node!r}.")
    return sorted(group_nodes, key=lambda node: (graph.nodes[node].get("group_name", "").casefold(), node))


def _group_members(graph: nx.MultiDiGraph, group_node: str) -> list[dict[str, Any]]:
    """Resolve group member edges in the stable order stored on the group node."""
    group = graph.nodes[group_node]
    member_edges = {
        node_id: data
        for node_id, data in _typed_edges(graph, group_node, "out", "contains_skill")
    }
    members: list[dict[str, Any]] = []
    for skill_id in group.get("skill_ids", []):
        skill_node = f"skill:{skill_id}"
        if skill_node not in graph or skill_node not in member_edges:
            raise ValueError(
                f"Skill group {group.get('group_id')!r} references missing graph skill {skill_id!r}."
            )
        skill = graph.nodes[skill_node]
        members.append({"skill_id": skill_id, "skill_name": skill.get("skill_name")})
    if set(member_edges) != {f"skill:{skill_id}" for skill_id in group.get("skill_ids", [])}:
        raise ValueError(f"Skill group {group.get('group_id')!r} has unexpected membership edges.")
    return members


def _group_result(graph: nx.MultiDiGraph, group_node: str) -> dict[str, Any]:
    group = graph.nodes[group_node]
    result: dict[str, Any] = {
        "group_id": group["group_id"],
        "group_name": group["group_name"],
        "relationship": group["relationship"],
        "category": group.get("category"),
        "categories": list(group.get("categories", [])),
        "applicability": group["applicability"],
        "skills": _group_members(graph, group_node),
        "explanation": group.get("explanation", ""),
    }
    if "minimum_options_to_learn" in group:
        result["minimum_options_to_learn"] = group["minimum_options_to_learn"]
    if "condition" in group:
        result["condition"] = group["condition"]
    return result


def get_role_skills(graph: nx.MultiDiGraph, role_id: str) -> list[dict[str, Any]]:
    """Return a role's skills with role-specific importance and group membership."""
    _validate_graph(graph)
    role_node = _role_node(graph, role_id)
    group_memberships: dict[str, list[dict[str, Any]]] = {}
    for group_node in _role_groups(graph, role_node):
        group = graph.nodes[group_node]
        for member in _group_members(graph, group_node):
            group_memberships.setdefault(member["skill_id"], []).append(
                {
                    "group_id": group["group_id"],
                    "group_name": group["group_name"],
                    "relationship": group["relationship"],
                    "applicability": group["applicability"],
                    **({"minimum_options_to_learn": group["minimum_options_to_learn"]}
                       if "minimum_options_to_learn" in group else {}),
                    **({"condition": group["condition"]} if "condition" in group else {}),
                }
            )

    results: dict[str, dict[str, Any]] = {}
    for skill_node, edge in _typed_edges(graph, role_node, "out", "has_skill"):
        if skill_node not in graph or graph.nodes[skill_node].get("node_type") != "skill":
            raise ValueError(f"Role {role_id!r} has an invalid skill edge to {skill_node!r}.")
        skill = graph.nodes[skill_node]
        skill_id = skill["skill_id"]
        if skill_id in results:
            # A role should have one association per skill ID; duplicate graph edges are corrupt.
            raise ValueError(f"Role {role_id!r} has duplicate edges for skill {skill_id!r}.")
        results[skill_id] = {
            "skill_id": skill_id,
            "skill_name": skill["skill_name"],
            "category": edge.get("category"),
            "importance": edge.get("importance"),
            "why_relevant": edge.get("why_relevant"),
            "groups": sorted(group_memberships.get(skill_id, []), key=lambda item: item["group_id"]),
        }
    if not results:
        raise ValueError(f"Career role {role_id!r} has no connected skills in the graph.")
    unassociated_members = set(group_memberships) - set(results)
    if unassociated_members:
        raise ValueError(
            f"Career role {role_id!r} has group members without role-skill edges: "
            f"{sorted(unassociated_members)}."
        )
    return sorted(results.values(), key=lambda item: (item["category"], item["skill_name"].casefold(), item["skill_id"]))


def get_roles_for_skill(graph: nx.MultiDiGraph, skill_id: str) -> list[dict[str, Any]]:
    """Return every role using a skill, retaining each role's skill metadata."""
    _validate_graph(graph)
    skill_node = _skill_node(graph, skill_id)
    results: dict[str, dict[str, Any]] = {}
    for role_node, edge in _typed_edges(graph, skill_node, "in", "has_skill"):
        if role_node not in graph or graph.nodes[role_node].get("node_type") != "career_role":
            raise ValueError(f"Skill {skill_id!r} has an invalid role edge from {role_node!r}.")
        role = graph.nodes[role_node]
        role_id = role["role_id"]
        if role_id in results:
            raise ValueError(f"Skill {skill_id!r} has duplicate edges from role {role_id!r}.")
        results[role_id] = {
            "role_id": role_id,
            "role_name": role["role_name"],
            "domain": role["domain"],
            "importance": edge.get("importance"),
            "why_relevant": edge.get("why_relevant"),
            "category": edge.get("category"),
        }
    return sorted(results.values(), key=lambda item: (item["role_name"].casefold(), item["role_id"]))


def get_alternative_groups(graph: nx.MultiDiGraph, role_id: str) -> list[dict[str, Any]]:
    """Return alternatives as collective groups, never as individually mandatory skills."""
    _validate_graph(graph)
    role_node = _role_node(graph, role_id)
    groups = [
        _group_result(graph, node_id)
        for node_id in _role_groups(graph, role_node)
        if graph.nodes[node_id].get("relationship") == "alternatives"
    ]
    return groups


def get_compatible_stacks(graph: nx.MultiDiGraph, role_id: str) -> dict[str, Any]:
    """Return explicit stack combinations and any role-level selection rule.

    Each stack remains an intact group. A role-level minimum is returned
    separately so optional stack records are not mistaken for individually
    mandatory choices.
    """
    _validate_graph(graph)
    role_node = _role_node(graph, role_id)
    groups = [
        _group_result(graph, node_id)
        for node_id in _role_groups(graph, role_node)
        if graph.nodes[node_id].get("relationship") == "compatible_stack"
    ]
    # Return stack options as intact combinations; never compute a cross-product.
    requirement = graph.nodes[role_node].get("technology_stack_requirement")
    return {
        "role_id": role_id,
        "selection_requirement": requirement,
        "stacks": groups,
    }


def get_role_skill_groups(
    graph: nx.MultiDiGraph, role_id: str, applicability: str | None = None
) -> list[dict[str, Any]]:
    """Return all group types for a role, optionally filtered by applicability."""
    _validate_graph(graph)
    if applicability is not None and not isinstance(applicability, str):
        raise TypeError("applicability must be a string or None.")
    if applicability is not None and applicability not in _APPLICABILITY_VALUES:
        raise ValueError(
            "applicability must be one of 'required', 'optional', 'conditional', or None."
        )
    role_node = _role_node(graph, role_id)
    groups = [
        _group_result(graph, node_id)
        for node_id in _role_groups(graph, role_node)
        if applicability is None or graph.nodes[node_id].get("applicability") == applicability
    ]
    return sorted(groups, key=lambda item: (item["relationship"], item["group_name"].casefold(), item["group_id"]))


def get_roles_by_domain(graph: nx.MultiDiGraph, domain_name: str) -> list[dict[str, Any]]:
    """Return roles for an exact, case-insensitive domain-name match."""
    _validate_graph(graph)
    _validate_identifier(domain_name, "domain_name")
    normalized_name = domain_name.strip().casefold()
    domain_nodes = [
        node_id
        for node_id, attrs in graph.nodes(data=True)
        if attrs.get("node_type") == "career_domain"
        and attrs.get("domain_name", "").strip().casefold() == normalized_name
    ]
    if not domain_nodes:
        raise KeyError(f"Unknown career domain: {domain_name!r}.")
    if len(domain_nodes) > 1:
        raise ValueError(f"Career domain name {domain_name!r} is ambiguous in the graph.")

    roles: dict[str, dict[str, Any]] = {}
    for role_node, _ in _typed_edges(graph, domain_nodes[0], "in", "belongs_to_domain"):
        if role_node not in graph or graph.nodes[role_node].get("node_type") != "career_role":
            raise ValueError(f"Domain {domain_name!r} has an invalid role edge from {role_node!r}.")
        role = graph.nodes[role_node]
        role_id = role["role_id"]
        if role_id in roles:
            raise ValueError(f"Domain {domain_name!r} has duplicate edges from role {role_id!r}.")
        roles[role_id] = {
            "role_id": role_id,
            "role_name": role["role_name"],
            "description": role["description"],
        }
    return sorted(roles.values(), key=lambda item: (item["role_name"].casefold(), item["role_id"]))
