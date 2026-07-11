"""Add dispute source/razorpay_dispute_id and payments.payoutFrozen

Revision ID: 20260713_02
Revises: 20260713_01
Create Date: 2026-07-13

Why this migration exists:
  - The existing `disputes` table only ever modeled customer-filed support
    complaints (create_dispute/resolve_dispute) — there was nowhere to
    record a real Razorpay chargeback (payment.dispute.* webhooks), so
    those events had no persistence path.
  - payments.payoutFrozen lets a chargeback block execute_agency_payout
    from releasing more money to the agency while it's in flight; without
    it, a chargeback opened after a payout already ran leaves the platform
    holding the loss with no way to even flag the affected payment.
"""

from alembic import op

revision = "20260713_02"
down_revision = "20260713_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE disputes ADD COLUMN IF NOT EXISTS "source" VARCHAR(20) NOT NULL DEFAULT \'CUSTOMER\''
    )
    op.execute(
        'ALTER TABLE disputes ADD COLUMN IF NOT EXISTS "razorpayDisputeId" VARCHAR(100)'
    )
    op.execute(
        'CREATE UNIQUE INDEX IF NOT EXISTS ix_disputes_razorpay_dispute_id '
        'ON disputes ("razorpayDisputeId") WHERE "razorpayDisputeId" IS NOT NULL'
    )
    op.execute(
        'ALTER TABLE payments ADD COLUMN IF NOT EXISTS "payoutFrozen" BOOLEAN NOT NULL DEFAULT FALSE'
    )


def downgrade() -> None:
    op.execute('ALTER TABLE payments DROP COLUMN IF EXISTS "payoutFrozen"')
    op.execute('DROP INDEX IF EXISTS ix_disputes_razorpay_dispute_id')
    op.execute('ALTER TABLE disputes DROP COLUMN IF EXISTS "razorpayDisputeId"')
    op.execute('ALTER TABLE disputes DROP COLUMN IF EXISTS "source"')
