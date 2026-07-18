"""Add package/agency scoping to promotional_discounts

Revision ID: 20260719_02
Revises: 20260719_01
Create Date: 2026-07-19

Why this migration exists:
  - The coupon generator could only create platform-wide codes (any
    package, any agency) — the admin asked for codes restricted to one
    specific package and/or one specific agency, on top of the existing
    validity window / usage limits / min order amount / per-user limit
    (all of which were already real and enforced, just not scoped to a
    package or agency).
"""

from alembic import op

revision = "20260719_02"
down_revision = "20260719_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE promotional_discounts ADD COLUMN IF NOT EXISTS "packageId" VARCHAR(36)'
    )
    op.execute(
        'ALTER TABLE promotional_discounts ADD COLUMN IF NOT EXISTS "agencyId" VARCHAR(36)'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_promotional_discounts_package_id ON promotional_discounts ("packageId")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_promotional_discounts_agency_id ON promotional_discounts ("agencyId")'
    )


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_promotional_discounts_agency_id')
    op.execute('DROP INDEX IF EXISTS ix_promotional_discounts_package_id')
    op.execute('ALTER TABLE promotional_discounts DROP COLUMN IF EXISTS "agencyId"')
    op.execute('ALTER TABLE promotional_discounts DROP COLUMN IF EXISTS "packageId"')
