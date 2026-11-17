"""Persist and enforce explicit consent for future external AI processing."""

from datetime import datetime, timezone
from uuid import UUID
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ai_consent import AIPersonalizationConsent


CURRENT_AI_CONSENT_NOTICE_VERSION = "ai-personalization-notice-v1"
CURRENT_AI_CONSENT_SCOPE_VERSION = "career-profile-allowlist-v1"


class AIConsentPersistenceError(RuntimeError):
    """Raised when the consent state cannot be read or persisted."""


class AIConsentVersionError(ValueError):
    """Raised when a decision references obsolete notice or scope versions."""


class AIConsentDecisionError(ValueError):
    """Raised when a decision is outside the supported consent states."""


class NoActiveAIConsentError(ValueError):
    """Raised when withdrawal is requested without an active grant."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _response(record: AIPersonalizationConsent | None) -> dict[str, Any]:
    if record is None:
        return {
            "state": "not_set",
            "notice_version": None,
            "scope_version": None,
            "decision_at": None,
            "granted_at": None,
            "withdrawn_at": None,
            "current_notice_version": CURRENT_AI_CONSENT_NOTICE_VERSION,
            "current_scope_version": CURRENT_AI_CONSENT_SCOPE_VERSION,
        }
    return {
        "state": record.consent_state,
        "notice_version": record.notice_version,
        "scope_version": record.scope_version,
        "decision_at": record.decision_at,
        "granted_at": record.granted_at,
        "withdrawn_at": record.withdrawn_at,
        "current_notice_version": CURRENT_AI_CONSENT_NOTICE_VERSION,
        "current_scope_version": CURRENT_AI_CONSENT_SCOPE_VERSION,
    }


def get_ai_consent(db: Session, user_id: UUID) -> dict[str, Any]:
    """Return only the consent record belonging to the authenticated UUID."""
    try:
        return _response(db.get(AIPersonalizationConsent, user_id))
    except SQLAlchemyError:
        raise AIConsentPersistenceError("AI consent could not be retrieved.") from None


def save_ai_consent(
    db: Session,
    user_id: UUID,
    decision: str,
    notice_version: str,
    scope_version: str,
) -> dict[str, Any]:
    """Record an explicit grant or decline for the current notice and scope."""
    if decision not in {"granted", "declined"}:
        raise AIConsentDecisionError("Unsupported AI consent decision.")
    if (
        notice_version != CURRENT_AI_CONSENT_NOTICE_VERSION
        or scope_version != CURRENT_AI_CONSENT_SCOPE_VERSION
    ):
        raise AIConsentVersionError("The AI consent notice or data scope is outdated.")

    try:
        now = _now()
        record = db.get(AIPersonalizationConsent, user_id)
        if record is None:
            record = AIPersonalizationConsent(user_id=user_id)
            db.add(record)
        record.consent_state = decision
        record.notice_version = notice_version
        record.scope_version = scope_version
        record.decision_at = now
        record.granted_at = now if decision == "granted" else None
        record.withdrawn_at = None
        record.updated_at = now
        db.commit()
        return _response(record)
    except SQLAlchemyError:
        db.rollback()
        raise AIConsentPersistenceError("AI consent could not be saved.") from None


def withdraw_ai_consent(db: Session, user_id: UUID) -> dict[str, Any]:
    """Withdraw a previously granted consent; declined/not-set states are unchanged."""
    try:
        record = db.get(AIPersonalizationConsent, user_id)
        if record is None or record.consent_state != "granted":
            raise NoActiveAIConsentError("There is no active AI consent to withdraw.")
        now = _now()
        record.consent_state = "withdrawn"
        record.decision_at = now
        record.withdrawn_at = now
        record.updated_at = now
        db.commit()
        return _response(record)
    except NoActiveAIConsentError:
        raise
    except SQLAlchemyError:
        db.rollback()
        raise AIConsentPersistenceError("AI consent could not be withdrawn.") from None


def has_current_ai_consent(db: Session, user_id: UUID) -> bool:
    """Fail closed: only a current, explicit grant permits future AI processing."""
    try:
        record = db.get(AIPersonalizationConsent, user_id)
        return bool(
            record is not None
            and record.consent_state == "granted"
            and record.notice_version == CURRENT_AI_CONSENT_NOTICE_VERSION
            and record.scope_version == CURRENT_AI_CONSENT_SCOPE_VERSION
            and record.granted_at is not None
            and record.withdrawn_at is None
        )
    except Exception:
        # A database error or malformed record must never permit provider calls.
        return False
