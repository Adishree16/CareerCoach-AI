"""Per-authenticated-user consent for optional external AI personalization."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AIPersonalizationConsent(Base):
    """Current AI consent state keyed by the verified Supabase user UUID.

    Supabase users are external to this database, so this table intentionally
    stores the verified UUID without a profile foreign key. Consent is separate
    from the replaceable profile snapshot.
    """

    __tablename__ = "ai_personalization_consents"
    __table_args__ = (
        CheckConstraint(
            "consent_state IN ('granted', 'declined', 'withdrawn')",
            name="ck_ai_personalization_consents_state",
        ),
        CheckConstraint(
            "(consent_state = 'granted' AND granted_at IS NOT NULL AND withdrawn_at IS NULL) "
            "OR (consent_state = 'declined' AND granted_at IS NULL AND withdrawn_at IS NULL) "
            "OR (consent_state = 'withdrawn' AND granted_at IS NOT NULL AND withdrawn_at IS NOT NULL)",
            name="ck_ai_personalization_consents_state_timestamps",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    consent_state: Mapped[str] = mapped_column(String(20), nullable=False)
    notice_version: Mapped[str] = mapped_column(String(80), nullable=False)
    scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now,
        onupdate=utc_now, server_default=func.now()
    )
