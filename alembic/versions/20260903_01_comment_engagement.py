"""Add persistent likes and one-level replies to community post comments.

Revision ID: 20260903_01
Revises: 20260801_01
Create Date: 2026-09-03
"""

from alembic import op


revision = "20260903_01"
down_revision = "20260801_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE post_comments ADD COLUMN IF NOT EXISTS "parentCommentId" VARCHAR(36) REFERENCES post_comments(id)')
    op.execute('ALTER TABLE post_comments ADD COLUMN IF NOT EXISTS "likeCount" INTEGER NOT NULL DEFAULT 0')
    op.execute('CREATE INDEX IF NOT EXISTS idx_post_comments_parent ON post_comments("parentCommentId")')
    op.execute(
        '''
        CREATE TABLE IF NOT EXISTS post_comment_likes (
            id VARCHAR(36) PRIMARY KEY,
            "commentId" VARCHAR(36) NOT NULL REFERENCES post_comments(id) ON DELETE CASCADE,
            "userId" VARCHAR(36) NOT NULL REFERENCES users(id),
            "createdAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE ("commentId", "userId")
        )
        '''
    )
    op.execute('CREATE INDEX IF NOT EXISTS idx_post_comment_likes_comment ON post_comment_likes("commentId")')


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS post_comment_likes')
    op.execute('DROP INDEX IF EXISTS idx_post_comments_parent')
    op.execute('ALTER TABLE post_comments DROP COLUMN IF EXISTS "likeCount"')
    op.execute('ALTER TABLE post_comments DROP COLUMN IF EXISTS "parentCommentId"')
