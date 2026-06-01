"""Add composite performance indexes for packages and chat

Revision ID: 20260601_01
Revises:
Create Date: 2026-06-01

Why this migration exists:
  - packages(status, createdAt): used by every discover/trending query
    (WHERE status='OPEN' ORDER BY "createdAt" DESC). Without this, Postgres
    does a seq-scan even though status has a single-column index, because the
    sort order isn't covered.
  - chat_messages(groupId, createdAt): covers the cursor-based history fetch
    (WHERE "groupId"=? AND "createdAt" < cursor ORDER BY "createdAt" DESC LIMIT N).
  - direct_messages(conversationId, createdAt): covers DM history + DISTINCT ON
    for the inbox last-message query.
  - direct_messages(senderId): used by the unread-count JOIN — this column has a
    FK constraint but no index, causing seq-scans on every inbox page load.
  - direct_conversation_participants(userId, createdAt): covers inbox pagination
    (WHERE "userId"=? ORDER BY "createdAt" DESC OFFSET/LIMIT).

Tables and column names use the camelCase identifiers created by Prisma migrations.
"""

from alembic import op

revision = "20260601_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Discover / trending: status filter + recency sort (covers both ORDER BY directions)
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_packages_status_created_at '
        'ON packages (status, "createdAt" DESC)'
    )

    # Group chat history: per-group cursor pagination
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_chat_messages_group_created_at '
        'ON chat_messages ("groupId", "createdAt" DESC)'
    )

    # DM history + DISTINCT ON (conversationId): covers both use cases in one index
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_direct_messages_conv_created_at '
        'ON direct_messages ("conversationId", "createdAt" DESC)'
    )

    # Unread count JOIN: direct_messages.senderId was an unindexed FK
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_direct_messages_sender_id '
        'ON direct_messages ("senderId")'
    )

    # Inbox pagination: per-user participant lookup with recency sort
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_dcp_user_created_at '
        'ON direct_conversation_participants ("userId", "createdAt" DESC)'
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_packages_status_created_at")
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_group_created_at")
    op.execute("DROP INDEX IF EXISTS ix_direct_messages_conv_created_at")
    op.execute("DROP INDEX IF EXISTS ix_direct_messages_sender_id")
    op.execute("DROP INDEX IF EXISTS ix_dcp_user_created_at")
