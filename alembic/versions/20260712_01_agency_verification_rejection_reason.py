"""Add agencies.verificationRejectionReason

Revision ID: 20260712_01
Revises: 20260711_02
Create Date: 2026-07-12

Why this migration exists:
  - reject_verification() has always accepted a `reason` argument from the
    admin-only reject endpoint but had nowhere to store it, so it was
    silently discarded — an agency rejected for a fixable reason (e.g. a
    blurry tourism license scan) had no way to find out why, making the
    frontend's "verification outcome" surface a dead end. This column lets
    the reason survive past the request that set it.
"""

from alembic import op

revision = "20260712_01"
down_revision = "20260711_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE agencies ADD COLUMN IF NOT EXISTS "verificationRejectionReason" TEXT'
    )


def downgrade() -> None:
    op.execute('ALTER TABLE agencies DROP COLUMN IF EXISTS "verificationRejectionReason"')
