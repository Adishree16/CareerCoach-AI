"""Remove the portfolio website column from profiles.

Revision ID: 9c1f4a6e2b70
Revises: 56e21b6c2d33
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9c1f4a6e2b70"
down_revision: Union[str, None] = "56e21b6c2d33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("profiles", "portfolio_website")


def downgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("portfolio_website", sa.Text(), nullable=True),
    )
