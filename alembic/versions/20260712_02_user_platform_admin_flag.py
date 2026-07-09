"""Add users.isPlatformAdmin

Revision ID: 20260712_02
Revises: 20260712_01
Create Date: 2026-07-12

Why this migration exists:
  - UserRole.PLATFORM_ADMIN and require_admin() have existed since the
    earliest version of this backend, gating several endpoints (loyalty
    admin actions, wallet reconciliation, chat moderation, and now agency
    verification approve/reject). But the JWT `role` claim was only ever
    computed as `"agency_admin" if agency_id else "user"`
    (see services/auth.py::_build_session) — there was no data anywhere
    that could ever produce role="platform_admin", so every admin-gated
    endpoint has been unreachable by any real account. This column is the
    missing source of truth; grant it by hand (`UPDATE users SET
    "isPlatformAdmin" = true WHERE email = '...'`) since there's no
    self-service admin invitation flow, by design.
"""

from alembic import op

revision = "20260712_02"
down_revision = "20260712_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE users ADD COLUMN IF NOT EXISTS "isPlatformAdmin" BOOLEAN NOT NULL DEFAULT false'
    )


def downgrade() -> None:
    op.execute('ALTER TABLE users DROP COLUMN IF EXISTS "isPlatformAdmin"')
