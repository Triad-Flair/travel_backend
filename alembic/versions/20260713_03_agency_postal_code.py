"""Add agencies.postalCode

Revision ID: 20260713_03
Revises: 20260713_02
Create Date: 2026-07-13

Why this migration exists:
  - Automated Razorpay Route Linked Account creation requires a registered
    address including a postal code (profile.addresses.registered.postal_code)
    — agencies.address/city/state already existed but nothing captured a PIN
    code, so there was no way to build that address block automatically.
"""

from alembic import op

revision = "20260713_03"
down_revision = "20260713_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE agencies ADD COLUMN IF NOT EXISTS "postalCode" VARCHAR(10)'
    )


def downgrade() -> None:
    op.execute('ALTER TABLE agencies DROP COLUMN IF EXISTS "postalCode"')
