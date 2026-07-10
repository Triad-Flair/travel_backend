"""Add agency_bank_accounts.branchName

Revision ID: 20260713_01
Revises: 20260712_02
Create Date: 2026-07-13

Why this migration exists:
  - The bank verification form has always collected a branch name, but
    nothing in the backend ever persisted it — verify_bank_account() only
    read accountNumber/ifscCode/accountHolderName/bankName/razorpayAccountId
    from the payload, so branch was silently discarded on every submission.
"""

from alembic import op

revision = "20260713_01"
down_revision = "20260712_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE agency_bank_accounts ADD COLUMN IF NOT EXISTS "branchName" VARCHAR(150)'
    )


def downgrade() -> None:
    op.execute('ALTER TABLE agency_bank_accounts DROP COLUMN IF EXISTS "branchName"')
