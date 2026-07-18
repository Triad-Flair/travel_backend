"""Add group_members.tripReminderSentAt

Revision ID: 20260718_02
Revises: 20260718_01
Create Date: 2026-07-18

Why this migration exists:
  - send_upcoming_trip_reminders (workers/tasks.py) notifies group members
    whose trip starts within the next few days — an unpaid member gets a
    "complete your payment" nudge, a paid (COMMITTED) member gets a "pack
    your bags" reminder. Without a sent-at marker per member, the same
    notification would fire again on every beat tick between now and the
    trip's start date instead of exactly once.
"""

from alembic import op

revision = "20260718_02"
down_revision = "20260718_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE group_members ADD COLUMN IF NOT EXISTS "tripReminderSentAt" TIMESTAMPTZ'
    )


def downgrade() -> None:
    op.execute('ALTER TABLE group_members DROP COLUMN IF EXISTS "tripReminderSentAt"')
