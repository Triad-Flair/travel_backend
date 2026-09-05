"""Add optional traveler profile cover image.

Revision ID: 20260906_01
Revises: 20260903_02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260906_01"
down_revision = "20260903_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("coverImageUrl", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "coverImageUrl")
