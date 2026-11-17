"""Request and response schemas for AI personalization consent."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class AIConsentSchema(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class AIConsentDecisionRequest(AIConsentSchema):
    decision: Literal["granted", "declined"]
    notice_version: Annotated[str, Field(min_length=1, max_length=80)]
    scope_version: Annotated[str, Field(min_length=1, max_length=80)]


class AIConsentResponse(AIConsentSchema):
    state: Literal["not_set", "granted", "declined", "withdrawn"]
    notice_version: str | None
    scope_version: str | None
    decision_at: datetime | None
    granted_at: datetime | None
    withdrawn_at: datetime | None
    current_notice_version: str
    current_scope_version: str
