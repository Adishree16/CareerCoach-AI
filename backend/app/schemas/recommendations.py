"""Request schema for profile-based career recommendations."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    career_interest: Annotated[str, Field(min_length=1, max_length=200)]
    preferred_work_activity: Annotated[str, Field(min_length=1, max_length=200)]
    career_goal: Annotated[str, Field(min_length=1, max_length=200)]
    career_priority: Annotated[str, Field(min_length=1, max_length=200)]
