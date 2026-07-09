"""Add agency_bank_accounts.razorpayAccountId — the Razorpay Route linked
account id transfers are split to

Revision ID: 20260710_03
Revises: 20260710_02
Create Date: 2026-07-10

Why this migration exists:
  - PRD 3.3/4: the 90/10 vendor/platform split is executed via Razorpay
    Route, which requires a Razorpay *linked account id* (an "acc_..." id,
    created by onboarding the agency through Razorpay's own Linked Account
    API/KYC flow — that onboarding is a separate, larger feature and is
    NOT built here). This column just gives the app somewhere to store that
    id once an agency has one, so services/payments.py::execute_agency_payout
    can attempt a real Route transfer when it's present, and fall back to
    the existing manual/bookkeeping-only payout when it's absent (which is
    every agency today).

Tables and column names use the camelCase identifiers this repo's other
Prisma-derived tables use, for consistency.
"""

from alembic import op

revision = "20260710_03"
down_revision = "20260710_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE agency_bank_accounts ADD COLUMN IF NOT EXISTS "razorpayAccountId" VARCHAR(50)'
    )


def downgrade() -> None:
    op.execute('ALTER TABLE agency_bank_accounts DROP COLUMN IF EXISTS "razorpayAccountId"')
