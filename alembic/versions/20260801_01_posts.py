"""Add posts/post_likes/post_comments tables, and messageType/metadata
columns on direct_messages

Revision ID: 20260801_01
Revises: 20260719_02
Create Date: 2026-08-01

Why this migration exists:
  - New social "Posts" feature on the traveler profile page: users can
    post a caption + photos, other users can like/comment, and posts can
    be shared into an in-platform chat.
  - direct_messages had no messageType/metadata columns at all — unlike
    chat_messages (group chat), which already uses metadata for poll and
    reply-preview cards. SendDirectMessageRequest already accepted a
    metadata field but it was silently dropped since there was nowhere to
    store it. These columns are what let a "shared post" render as a rich
    card inside a DM instead of falling back to plain text.
"""

from alembic import op

revision = "20260801_01"
down_revision = "20260719_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id VARCHAR(36) PRIMARY KEY,
            "authorUserId" VARCHAR(36) NOT NULL REFERENCES users(id),
            caption TEXT,
            "imageUrls" JSONB,
            destination VARCHAR(120),
            "likeCount" INTEGER NOT NULL DEFAULT 0,
            "commentCount" INTEGER NOT NULL DEFAULT 0,
            "shareCount" INTEGER NOT NULL DEFAULT 0,
            "createdAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
            "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS idx_posts_author_created ON posts("authorUserId", "createdAt" DESC)')

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS post_likes (
            id VARCHAR(36) PRIMARY KEY,
            "postId" VARCHAR(36) NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            "userId" VARCHAR(36) NOT NULL REFERENCES users(id),
            "createdAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE ("postId", "userId")
        )
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS idx_post_likes_post ON post_likes("postId")')

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS post_comments (
            id VARCHAR(36) PRIMARY KEY,
            "postId" VARCHAR(36) NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            "authorUserId" VARCHAR(36) NOT NULL REFERENCES users(id),
            content TEXT NOT NULL,
            "createdAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute('CREATE INDEX IF NOT EXISTS idx_post_comments_post_created ON post_comments("postId", "createdAt" ASC)')

    op.execute('ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS "messageType" VARCHAR(20) NOT NULL DEFAULT \'text\'')
    op.execute('ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS "metadata" JSONB')


def downgrade() -> None:
    op.execute('ALTER TABLE direct_messages DROP COLUMN IF EXISTS "metadata"')
    op.execute('ALTER TABLE direct_messages DROP COLUMN IF EXISTS "messageType"')
    op.execute("DROP TABLE IF EXISTS post_comments")
    op.execute("DROP TABLE IF EXISTS post_likes")
    op.execute("DROP TABLE IF EXISTS posts")
