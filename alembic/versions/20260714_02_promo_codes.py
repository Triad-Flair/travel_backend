"""Add real discount fields to promotional_discounts and promo tracking on payments

Revision ID: 20260714_02
Revises: 20260714_01
Create Date: 2026-07-14

Why this migration exists:
  - promotional_discounts previously had only `code` and `isActive` — no
    discount type/value/expiry/limits, so a "coupon" row could exist but
    had nothing that told checkout how much to discount or when to stop
    honoring it.
  - payments had no column to record which promo (if any) was applied or
    how much it discounted, so invoices/refunds/usage-limit checks had no
    way to look this up after the fact.
"""

from alembic import op

revision = "20260714_02"
down_revision = "20260714_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE promotional_discounts ADD COLUMN IF NOT EXISTS "description" VARCHAR(255)'
    )
    op.execute(
        "ALTER TABLE promotional_discounts ADD COLUMN IF NOT EXISTS \"discountType\" "
        "VARCHAR(20) NOT NULL DEFAULT 'PERCENTAGE'"
    )
    op.execute(
        'ALTER TABLE promotional_discounts ADD COLUMN IF NOT EXISTS "discountValue" INTEGER NOT NULL DEFAULT 0'
    )
    op.execute(
        'ALTER TABLE promotional_discounts ADD COLUMN IF NOT EXISTS "maxDiscountPaise" INTEGER'
    )
    op.execute(
        'ALTER TABLE promotional_discounts ADD COLUMN IF NOT EXISTS "minOrderAmountPaise" INTEGER'
    )
    op.execute(
        'ALTER TABLE promotional_discounts ADD COLUMN IF NOT EXISTS "usageLimit" INTEGER'
    )
    op.execute(
        'ALTER TABLE promotional_discounts ADD COLUMN IF NOT EXISTS "perUserLimit" INTEGER DEFAULT 1'
    )
    op.execute(
        'ALTER TABLE promotional_discounts ADD COLUMN IF NOT EXISTS "expiresAt" TIMESTAMPTZ'
    )
    op.execute(
        'ALTER TABLE payments ADD COLUMN IF NOT EXISTS "promoCode" VARCHAR(50)'
    )
    op.execute(
        'ALTER TABLE payments ADD COLUMN IF NOT EXISTS "promoDiscountAmount" INTEGER NOT NULL DEFAULT 0'
    )


def downgrade() -> None:
    op.execute('ALTER TABLE payments DROP COLUMN IF EXISTS "promoDiscountAmount"')
    op.execute('ALTER TABLE payments DROP COLUMN IF EXISTS "promoCode"')
    op.execute('ALTER TABLE promotional_discounts DROP COLUMN IF EXISTS "expiresAt"')
    op.execute('ALTER TABLE promotional_discounts DROP COLUMN IF EXISTS "perUserLimit"')
    op.execute('ALTER TABLE promotional_discounts DROP COLUMN IF EXISTS "usageLimit"')
    op.execute('ALTER TABLE promotional_discounts DROP COLUMN IF EXISTS "minOrderAmountPaise"')
    op.execute('ALTER TABLE promotional_discounts DROP COLUMN IF EXISTS "maxDiscountPaise"')
    op.execute('ALTER TABLE promotional_discounts DROP COLUMN IF EXISTS "discountValue"')
    op.execute('ALTER TABLE promotional_discounts DROP COLUMN IF EXISTS "discountType"')
    op.execute('ALTER TABLE promotional_discounts DROP COLUMN IF EXISTS "description"')
