"""Replace the tranche1/tranche2 payout split with a single full release

Revision ID: 20260718_01
Revises: 20260714_02
Create Date: 2026-07-18

Why this migration exists:
  - The agency's net share used to be released in two Razorpay Route
    transfers — 45% the instant the trip confirmed (tranche1), the
    remaining 55% only after the trip completed (tranche2). The platform
    owner wants the agency's full 90% share sent in a single transfer at
    confirmation instead, so the two-tranche bookkeeping (tranche1Released/
    tranche2Released) no longer maps to how payouts work.
  - payoutAmountPaise replaces both booleans with the actual amount ever
    transferred to the agency for a payment. This isn't just a rename: 3
    real production payments already have tranche1Released=true,
    tranche2Released=false (their 45% advance went out under the old
    scheme, the 55% final was never sent since no trip had completed yet).
    Backfilling payoutAmountPaise as whatever was actually transferred
    means execute_agency_payout's new "pay whatever remains owed" logic
    correctly tops those payments up to 100% on its next run, instead of
    either re-sending the 45% (double payment) or forgetting the 55% is
    still owed.
"""

from alembic import op

revision = "20260718_01"
down_revision = "20260714_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE payments ADD COLUMN IF NOT EXISTS "payoutAmountPaise" INTEGER NOT NULL DEFAULT 0'
    )
    op.execute(
        'ALTER TABLE payments ADD COLUMN IF NOT EXISTS "payoutReleased" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        """
        UPDATE payments SET
            "payoutAmountPaise" =
                (CASE WHEN "tranche1Released" THEN ROUND((COALESCE("tripAmount", 0) - COALESCE("commissionAmount", 0)) * 0.45) ELSE 0 END)::int +
                (CASE WHEN "tranche2Released" THEN ROUND((COALESCE("tripAmount", 0) - COALESCE("commissionAmount", 0)) * 0.55) ELSE 0 END)::int,
            "payoutReleased" = ("tranche1Released" AND "tranche2Released")
        """
    )
    op.execute('ALTER TABLE payments DROP COLUMN IF EXISTS "tranche1Released"')
    op.execute('ALTER TABLE payments DROP COLUMN IF EXISTS "tranche2Released"')


def downgrade() -> None:
    # Lossy: a payment topped up under the new single-release scheme has no
    # 45/55 split to reconstruct, so both flags come back False for any
    # payment that isn't fully released — an admin would need to re-derive
    # the correct historical flags manually if this is ever rolled back.
    op.execute(
        'ALTER TABLE payments ADD COLUMN IF NOT EXISTS "tranche1Released" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        'ALTER TABLE payments ADD COLUMN IF NOT EXISTS "tranche2Released" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        """
        UPDATE payments SET
            "tranche1Released" = "payoutReleased",
            "tranche2Released" = "payoutReleased"
        """
    )
    op.execute('ALTER TABLE payments DROP COLUMN IF EXISTS "payoutAmountPaise"')
    op.execute('ALTER TABLE payments DROP COLUMN IF EXISTS "payoutReleased"')
