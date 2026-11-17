"""Add per-user AI personalization consent storage.

Revision ID: a81d6f2c9b40
Revises: 9c1f4a6e2b70
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a81d6f2c9b40"
down_revision: Union[str, None] = "9c1f4a6e2b70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_personalization_consents",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("consent_state", sa.String(length=20), nullable=False),
        sa.Column("notice_version", sa.String(length=80), nullable=False),
        sa.Column("scope_version", sa.String(length=80), nullable=False),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "consent_state IN ('granted', 'declined', 'withdrawn')",
            name="ck_ai_personalization_consents_state",
        ),
        sa.CheckConstraint(
            "(consent_state = 'granted' AND granted_at IS NOT NULL AND withdrawn_at IS NULL) "
            "OR (consent_state = 'declined' AND granted_at IS NULL AND withdrawn_at IS NULL) "
            "OR (consent_state = 'withdrawn' AND granted_at IS NOT NULL AND withdrawn_at IS NOT NULL)",
            name="ck_ai_personalization_consents_state_timestamps",
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("ai_personalization_consents")
