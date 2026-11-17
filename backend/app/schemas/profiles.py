from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ProfileSchema(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


SkillText = Annotated[str, Field(min_length=1, max_length=100)]
ProfileText = Annotated[str, Field(max_length=5_000)]
TechnologyText = Annotated[str, Field(min_length=1, max_length=1_000)]


class BasicInformation(ProfileSchema):
    first_name: Annotated[str, Field(min_length=1, max_length=100)]
    last_name: Annotated[str, Field(min_length=1, max_length=100)]
    education_level: Literal[
        "secondary",
        "higher-secondary",
        "diploma",
        "undergraduate",
        "postgraduate",
        "doctorate",
        "other",
    ]
    education_level_other: Annotated[str, Field(max_length=100)] = ""
    degree: Annotated[str, Field(min_length=1, max_length=150)]
    degree_other: Annotated[str, Field(max_length=150)] = ""
    field_of_study: Annotated[str, Field(min_length=1, max_length=150)]
    field_of_study_other: Annotated[str, Field(max_length=150)] = ""

    @model_validator(mode="after")
    def validate_other_values(self) -> "BasicInformation":
        if self.education_level == "other" and not self.education_level_other:
            raise ValueError("educationLevelOther is required when educationLevel is other.")
        if self.degree.casefold() == "other" and not self.degree_other:
            raise ValueError("degreeOther is required when degree is other.")
        if self.field_of_study.casefold() == "other" and not self.field_of_study_other:
            raise ValueError("fieldOfStudyOther is required when fieldOfStudy is other.")
        return self


class Experience(ProfileSchema):
    company: Annotated[str, Field(max_length=200)] = ""
    role: Annotated[str, Field(min_length=1, max_length=200)]
    duration: Annotated[str, Field(max_length=100)] = ""
    responsibilities: ProfileText = ""
    technologies: Annotated[str, Field(max_length=1_000)] = ""


class CareerSkills(ProfileSchema):
    career_status: Literal["Student", "Fresher", "Experienced"]
    technical_skills: Annotated[list[SkillText], Field(min_length=1, max_length=100)]
    internship_experience: Annotated[list[Experience], Field(max_length=30)]
    professional_experience: Annotated[list[Experience], Field(max_length=30)]

    @field_validator("technical_skills")
    @classmethod
    def reject_duplicate_skills(cls, skills: list[str]) -> list[str]:
        seen: set[str] = set()
        for skill in skills:
            normalized = skill.casefold()
            if normalized in seen:
                raise ValueError("Technical skills must not contain case-insensitive duplicates.")
            seen.add(normalized)
        return skills


class Project(ProfileSchema):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(min_length=1, max_length=5_000)]
    technologies: TechnologyText


class Certification(ProfileSchema):
    name: Annotated[str, Field(min_length=1, max_length=250)]
    organization: Annotated[str, Field(min_length=1, max_length=200)]


class AdditionalInformation(ProfileSchema):
    projects: Annotated[list[Project], Field(max_length=30)]
    certifications: Annotated[list[Certification], Field(max_length=50)]
    achievements: ProfileText | None = None


class ProfileCreateRequest(ProfileSchema):
    basic_information: BasicInformation
    career_skills: CareerSkills
    additional_information: AdditionalInformation


class ProfileResponse(ProfileCreateRequest):
    id: UUID
    created_at: datetime
    updated_at: datetime
