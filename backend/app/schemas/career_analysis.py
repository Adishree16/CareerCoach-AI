"""Request and response schemas for deterministic, role-specific analysis."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.recommendations import RecommendationRequest


class CareerAnalysisRequest(RecommendationRequest):
    """The current four preferences plus one canonical role identifier."""

    role_id: Annotated[str, Field(min_length=1, max_length=100)]


class CareerAnalysisSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CareerRoleSource(CareerAnalysisSchema):
    id: str
    title: str
    publisher: str
    url: str
    use: str


class CareerRoleOverview(CareerAnalysisSchema):
    role_id: str
    name: str
    domain: str
    description: str
    typical_responsibilities: list[str]
    source_references: list[CareerRoleSource]


class CareerScoreSection(CareerAnalysisSchema):
    score: float | None
    maximum_points: float
    maximum_applicable_points: float
    available: bool
    status: str
    details: dict[str, Any]


class SkillAlignmentAnalysis(CareerScoreSection):
    evidence_coverage_percentage: float | None
    matched_skills: list[dict[str, Any]]
    requirements_not_demonstrated_in_profile: list[dict[str, Any]]
    requirements_interpretation: str
    alternative_groups: list[dict[str, Any]]
    complementary_groups: list[dict[str, Any]]
    compatible_stacks: dict[str, Any]
    conditional_exclusions: list[dict[str, Any]]


class ProjectsExperienceAnalysis(CareerScoreSection):
    category_breakdown: dict[str, Any]
    inapplicable_categories: list[str]


class InterestAlignmentAnalysis(CareerScoreSection):
    pass


class ContextualPreference(CareerAnalysisSchema):
    option_id: str | None
    label: str | None


class RelatedCareerRole(CareerAnalysisSchema):
    role_id: str
    name: str


class CareerProjectDirections(CareerAnalysisSchema):
    source: str
    catalogue_suggestions: list[str]
    distinction: str


class CareerProgression(CareerAnalysisSchema):
    paths: list[list[str]]
    related_roles: list[RelatedCareerRole]
    note: str


class CareerAnalysisAvailability(CareerAnalysisSchema):
    status: str
    unavailable_reasons: list[dict[str, str]]
    limitations: list[str]


class CareerAnalysisAIStatement(CareerAnalysisSchema):
    text: Annotated[str, Field(min_length=8, max_length=300)]
    skill_ids: list[str] = Field(default_factory=list, max_length=12)
    evidence_categories: list[Literal["projects", "internships", "professional_experience"]] = Field(
        default_factory=list, max_length=3
    )
    project_indexes: list[int] = Field(default_factory=list, max_length=8)


class CareerAnalysisAIProjectExplanation(CareerAnalysisSchema):
    text: Annotated[str, Field(min_length=8, max_length=300)]
    skill_ids: list[str] = Field(default_factory=list, max_length=12)
    evidence_categories: list[Literal["projects", "internships", "professional_experience"]] = Field(
        default_factory=list, max_length=3
    )


class CareerAnalysisAIProjectSuggestion(CareerAnalysisSchema):
    project_id: Annotated[str, Field(min_length=1, max_length=240)]
    explanation: CareerAnalysisAIProjectExplanation


class CareerAnalysisAIContent(CareerAnalysisSchema):
    why_it_may_suit: list[CareerAnalysisAIStatement] = Field(default_factory=list, max_length=5)
    strengths: list[CareerAnalysisAIStatement] = Field(default_factory=list, max_length=5)
    areas_to_explore: list[CareerAnalysisAIStatement] = Field(default_factory=list, max_length=8)
    practice_project_suggestions: list[CareerAnalysisAIProjectSuggestion] = Field(default_factory=list, max_length=3)


class CareerAnalysisAIEnrichment(CareerAnalysisSchema):
    available: Literal[True]
    content: CareerAnalysisAIContent


class CareerAnalysisResponse(CareerAnalysisSchema):
    role_overview: CareerRoleOverview
    skill_alignment: SkillAlignmentAnalysis
    projects_experience: ProjectsExperienceAnalysis
    interest_alignment: InterestAlignmentAnalysis
    contextual_preferences: dict[str, ContextualPreference]
    project_directions: CareerProjectDirections
    career_progression: CareerProgression
    availability: CareerAnalysisAvailability
    ai_enrichment: CareerAnalysisAIEnrichment | None = None
