"""Add chat anti-leakage moderation: redaction columns on chat_messages/
direct_messages, plus a chat_moderation_keywords table so ops can update the
platform-leakage keyword list without a redeploy.

Revision ID: 20260711_01
Revises: 20260710_03
Create Date: 2026-07-11

Why this migration exists:
  - PRD 2.2: messages containing phone numbers, emails, URLs, or
    platform-leakage keywords (WhatsApp, Paytm, etc.) get redacted before
    being stored/returned. See app/services/chat_moderation.py for the
    detection logic.
  - originalContent is nullable and only ever populated when a message was
    actually flagged — it holds the pre-redaction text for moderation/
    dispute review. It is intentionally NOT exposed by the normal chat
    endpoints; only an admin-only audit endpoint reads it
    (app/api/v1/chat.py). Ops/compliance should define a retention policy
    for this column since it can contain PII (phone numbers, emails).
  - chat_moderation_keywords is seeded with the same default list as
    app/services/chat_moderation.py::DEFAULT_KEYWORDS, so the two stay in
    sync at deploy time; from then on the DB table is the live source of
    truth and can be edited via the admin keyword endpoints.

Tables and column names use the camelCase identifiers this repo's other
Prisma-derived tables use, for consistency.
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260711_01"
down_revision = "20260710_03"
branch_labels = None
depends_on = None

DEFAULT_KEYWORDS = [
    "whatsapp", "watsapp", "wattsapp", "telegram", "instagram", "insta dm",
    "paytm", "gpay", "google pay", "phonepe", "phone pe", "upi id",
    "venmo", "cashapp", "off platform", "off-platform", "outside the app",
    "outside this app", "personal number", "my number is", "call me at",
    "text me at", "reach me at",
]


def upgrade() -> None:
    for table in ("chat_messages", "direct_messages"):
        op.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "originalContent" TEXT')
        op.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "flagged" BOOLEAN NOT NULL DEFAULT false')
        op.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "flaggedCategories" JSONB')

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_moderation_keywords (
            id VARCHAR(36) PRIMARY KEY,
            keyword VARCHAR(100) NOT NULL UNIQUE,
            "isActive" BOOLEAN NOT NULL DEFAULT true,
            "createdAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    keywords_table = sa.table(
        "chat_moderation_keywords",
        sa.column("id", sa.String),
        sa.column("keyword", sa.String),
    )
    op.bulk_insert(keywords_table, [{"id": str(uuid.uuid4()), "keyword": k} for k in DEFAULT_KEYWORDS])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_moderation_keywords")
    for table in ("chat_messages", "direct_messages"):
        op.execute(f'ALTER TABLE {table} DROP COLUMN IF EXISTS "flaggedCategories"')
        op.execute(f'ALTER TABLE {table} DROP COLUMN IF EXISTS "flagged"')
        op.execute(f'ALTER TABLE {table} DROP COLUMN IF EXISTS "originalContent"')
