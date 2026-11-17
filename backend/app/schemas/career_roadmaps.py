"""Response schemas for deterministic, role-specific career roadmaps."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class CareerRoadmapSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CareerOverview(CareerRoadmapSchema):
    role_id: str
    name: str
    domain: str
    description: str
    main_responsibilities: list[str]


class SkillReference(CareerRoadmapSchema):
    skill_id: str
    name: str
    category_id: str
    category_name: str
    importance: Literal["core", "important", "specialised", "optional"]
    why_relevant: str


class SkillCategorySuggestions(CareerRoadmapSchema):
    category_id: str
    category_name: str
    importance: Literal["core", "important", "specialised", "optional"]
    skills: list[SkillReference]


class ComplementaryRequirement(CareerRoadmapSchema):
    name: str
    skills_to_explore: list[SkillReference]


class AlternativeRequirement(CareerRoadmapSchema):
    name: str
    minimum_options_to_learn: int
    options_already_demonstrated: int
    additional_options_to_explore: int
    options: list[SkillReference]


class CompatibleStackOption(CareerRoadmapSchema):
    name: str
    skills_already_demonstrated: list[SkillReference]
    skills_to_explore: list[SkillReference]


class CompatibleStackRequirement(CareerRoadmapSchema):
    minimum_options_to_learn: int
    options_already_demonstrated: int
    additional_options_to_explore: int
    options: list[CompatibleStackOption]


class SkillsToExplore(CareerRoadmapSchema):
    individual_requirements: list[SkillCategorySuggestions]
    complementary_requirements: list[ComplementaryRequirement]
    alternative_requirements: list[AlternativeRequirement]
    compatible_stack_requirements: list[CompatibleStackRequirement]
    note: str


class StructuredEvidence(CareerRoadmapSchema):
    source_category: Literal["projects", "internships", "professional_experience"]
    source_label: str
    relevant_skills: list[SkillReference]


class EligibleCertificationEvidence(CareerRoadmapSchema):
    name: str
    issuing_organization: str
    credential_type: str
    relevant_skills: list[SkillReference]


class ExistingSkills(CareerRoadmapSchema):
    demonstrated_technical_skills: list[SkillReference]
    structured_project_and_experience: list[StructuredEvidence]
    eligible_certifications: list[EligibleCertificationEvidence]
    note: str


class LearningGuidance(CareerRoadmapSchema):
    catalogue_supported: list[str]
    general_guidance: list[str]


class PracticeProjects(CareerRoadmapSchema):
    ideas: list[str]
    note: str


class RelatedCareer(CareerRoadmapSchema):
    role_id: str
    name: str


class CareerGrowth(CareerRoadmapSchema):
    related_roles: list[RelatedCareer]
    progression_paths: list[list[str]]
    note: str


class CareerRoadmapAIStatement(CareerRoadmapSchema):
    text: Annotated[str, Field(min_length=8, max_length=300)]
    skill_ids: list[str] = Field(default_factory=list, max_length=12)
    evidence_categories: list[Literal["projects", "internships", "professional_experience"]] = Field(
        default_factory=list, max_length=3
    )
    project_indexes: list[int] = Field(default_factory=list, max_length=8)


class CareerRoadmapAIRoleGuidance(CareerRoadmapSchema):
    text: Annotated[str, Field(min_length=8, max_length=300)]
    related_role_ids: list[str] = Field(min_length=1, max_length=8)


class CareerRoadmapAIContent(CareerRoadmapSchema):
    learning_guidance: list[CareerRoadmapAIStatement] = Field(default_factory=list, max_length=8)
    practice_approaches: list[CareerRoadmapAIStatement] = Field(default_factory=list, max_length=5)
    career_growth: list[CareerRoadmapAIRoleGuidance] = Field(default_factory=list, max_length=5)


class CareerRoadmapAIEnrichment(CareerRoadmapSchema):
    available: Literal[True]
    content: CareerRoadmapAIContent


class CareerRoadmapResponse(CareerRoadmapSchema):
    career_overview: CareerOverview
    your_existing_skills: ExistingSkills
    skills_to_explore: SkillsToExplore
    learning_guidance: LearningGuidance
    practice_projects: PracticeProjects
    career_growth: CareerGrowth
    ai_enrichment: CareerRoadmapAIEnrichment | None = None
