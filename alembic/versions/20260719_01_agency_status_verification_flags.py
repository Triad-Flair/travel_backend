"""Add Agency.status (operational gate) and per-field verification flags

Revision ID: 20260719_01
Revises: 20260718_02
Create Date: 2026-07-19

Why this migration exists:
  - New Super Admin Dashboard requirement: a coarse "can this agency
    operate" gate (PENDING/APPROVED/REJECTED/PAUSED/SUSPENDED) distinct
    from the existing KYC-review verification_status
    (pending/under_review/verified/rejected), plus individual per-field
    verification checkboxes (name/email/phone/bankDetails/gst/pan/
    travelLicense) an admin ticks off one at a time before a "Final
    Approve" becomes possible. Deliberately additive, not a migration of
    the existing verification_status column — kept as a plain VARCHAR
    (matching the existing sibling `verification` column on this same
    table) rather than a native Postgres enum, since altering enum types
    later is painful and this codebase has no precedent for creating new
    native enum types in a migration; validity is enforced at the
    Pydantic schema layer instead.
"""

from alembic import op

revision = "20260719_01"
down_revision = "20260718_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE agencies ADD COLUMN IF NOT EXISTS "status" VARCHAR(20) NOT NULL DEFAULT \'PENDING\''
    )
    op.execute(
        'ALTER TABLE agencies ADD COLUMN IF NOT EXISTS "nameVerified" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        'ALTER TABLE agencies ADD COLUMN IF NOT EXISTS "emailVerified" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        'ALTER TABLE agencies ADD COLUMN IF NOT EXISTS "phoneVerified" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        'ALTER TABLE agencies ADD COLUMN IF NOT EXISTS "bankDetailsVerified" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        'ALTER TABLE agencies ADD COLUMN IF NOT EXISTS "gstVerified" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        'ALTER TABLE agencies ADD COLUMN IF NOT EXISTS "panVerified" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        'ALTER TABLE agencies ADD COLUMN IF NOT EXISTS "travelLicenseVerified" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_agencies_status ON agencies ("status")'
    )


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_agencies_status')
    op.execute('ALTER TABLE agencies DROP COLUMN IF EXISTS "travelLicenseVerified"')
    op.execute('ALTER TABLE agencies DROP COLUMN IF EXISTS "panVerified"')
    op.execute('ALTER TABLE agencies DROP COLUMN IF EXISTS "gstVerified"')
    op.execute('ALTER TABLE agencies DROP COLUMN IF EXISTS "bankDetailsVerified"')
    op.execute('ALTER TABLE agencies DROP COLUMN IF EXISTS "phoneVerified"')
    op.execute('ALTER TABLE agencies DROP COLUMN IF EXISTS "emailVerified"')
    op.execute('ALTER TABLE agencies DROP COLUMN IF EXISTS "nameVerified"')
    op.execute('ALTER TABLE agencies DROP COLUMN IF EXISTS "status"')
