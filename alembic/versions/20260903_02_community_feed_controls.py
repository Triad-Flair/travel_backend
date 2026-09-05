"""Add saved posts, reporting queue, and user blocking for community safety.

Revision ID: 20260903_02
Revises: 20260903_01
Create Date: 2026-09-03
"""

from alembic import op


revision = "20260903_02"
down_revision = "20260903_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('''CREATE TABLE IF NOT EXISTS post_saves (id VARCHAR(36) PRIMARY KEY, "postId" VARCHAR(36) NOT NULL REFERENCES posts(id) ON DELETE CASCADE, "userId" VARCHAR(36) NOT NULL REFERENCES users(id), "createdAt" TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE ("postId", "userId"))''')
    op.execute('CREATE INDEX IF NOT EXISTS idx_post_saves_user_created ON post_saves("userId", "createdAt" DESC)')
    op.execute('''CREATE TABLE IF NOT EXISTS post_reports (id VARCHAR(36) PRIMARY KEY, "postId" VARCHAR(36) NOT NULL REFERENCES posts(id) ON DELETE CASCADE, "reporterUserId" VARCHAR(36) NOT NULL REFERENCES users(id), reason VARCHAR(40) NOT NULL, details TEXT, status VARCHAR(20) NOT NULL DEFAULT 'OPEN', "createdAt" TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE ("postId", "reporterUserId"))''')
    op.execute('CREATE INDEX IF NOT EXISTS idx_post_reports_status_created ON post_reports(status, "createdAt" DESC)')
    op.execute('''CREATE TABLE IF NOT EXISTS user_blocks (id VARCHAR(36) PRIMARY KEY, "blockerUserId" VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE, "blockedUserId" VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE, "createdAt" TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE ("blockerUserId", "blockedUserId"))''')
    op.execute('CREATE INDEX IF NOT EXISTS idx_user_blocks_blocker ON user_blocks("blockerUserId")')


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS user_blocks')
    op.execute('DROP TABLE IF EXISTS post_reports')
    op.execute('DROP TABLE IF EXISTS post_saves')
