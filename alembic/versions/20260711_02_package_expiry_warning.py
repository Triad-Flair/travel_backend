"""Add packages.expiryWarningSentAt — dedupes the package-expiry-warning
email so the Celery Beat job doesn't re-notify on every run.

Revision ID: 20260711_02
Revises: 20260711_01
Create Date: 2026-07-11

Why this migration exists:
  - PRD section 5, Lifecycle Hook: send_package_expiry_warning_email fires
    once per package as it nears expiresAt (see
    app/workers/tasks.py::check_package_expiry_warnings, scheduled in
    app/celery_app.py). Without this column, every 15-minute Beat tick would
    re-send the same warning for any package still inside the warning
    window.

Tables and column names use the camelCase identifiers this repo's other
Prisma-derived tables use, for consistency.
"""

from alembic import op

revision = "20260711_02"
down_revision = "20260711_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE packages ADD COLUMN IF NOT EXISTS "expiryWarningSentAt" TIMESTAMPTZ'
    )


def downgrade() -> None:
    op.execute('ALTER TABLE packages DROP COLUMN IF EXISTS "expiryWarningSentAt"')
