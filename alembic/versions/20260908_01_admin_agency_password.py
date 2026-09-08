"""Add forced first-login password change for admin-created agencies.

Revision ID: 20260908_01
Revises: 20260906_01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_01"
down_revision = "20260906_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("mustChangePassword", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "mustChangePassword")
