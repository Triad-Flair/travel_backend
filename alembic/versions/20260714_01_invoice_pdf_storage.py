"""Add invoices.userPdfData / agencyPdfData / pdfGeneratedAt

Revision ID: 20260714_01
Revises: 20260713_03
Create Date: 2026-07-14

Why this migration exists:
  - Nothing generated an actual PDF anywhere — the invoice pages only called
    window.print() and Invoice.pdfUrl was always null. Storing the rendered
    bytes directly in Postgres (bytea) rather than object storage, since no
    S3/Supabase Storage credentials are configured and these are small
    per-invoice documents.
"""

from alembic import op

revision = "20260714_01"
down_revision = "20260713_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE invoices ADD COLUMN IF NOT EXISTS "userPdfData" BYTEA')
    op.execute('ALTER TABLE invoices ADD COLUMN IF NOT EXISTS "agencyPdfData" BYTEA')
    op.execute('ALTER TABLE invoices ADD COLUMN IF NOT EXISTS "pdfGeneratedAt" TIMESTAMPTZ')


def downgrade() -> None:
    op.execute('ALTER TABLE invoices DROP COLUMN IF EXISTS "pdfGeneratedAt"')
    op.execute('ALTER TABLE invoices DROP COLUMN IF EXISTS "agencyPdfData"')
    op.execute('ALTER TABLE invoices DROP COLUMN IF EXISTS "userPdfData"')
