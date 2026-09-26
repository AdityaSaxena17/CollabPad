"""Create durable collaboration documents and update log.

Revision ID: 0001_documents
Revises:
"""

from alembic import op

revision = "0001_documents"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA collaboration")
    op.execute(
        """
        CREATE TABLE collaboration.documents (
          id UUID PRIMARY KEY,
          owner_clerk_id TEXT NOT NULL,
          title TEXT NOT NULL,
          share_enabled BOOLEAN NOT NULL DEFAULT FALSE,
          version BIGINT NOT NULL DEFAULT 0,
          snapshot_version BIGINT NOT NULL DEFAULT 0,
          snapshot BYTEA,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          CONSTRAINT documents_title_length CHECK (char_length(title) BETWEEN 1 AND 200),
          CONSTRAINT documents_snapshot_version CHECK (
            snapshot_version >= 0 AND version >= snapshot_version
          )
        )
        """
    )
    op.execute(
        "CREATE INDEX documents_owner_updated_idx "
        "ON collaboration.documents (owner_clerk_id, updated_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE collaboration.document_updates (
          document_id UUID NOT NULL REFERENCES collaboration.documents(id) ON DELETE CASCADE,
          sequence BIGINT NOT NULL,
          operation_id UUID NOT NULL UNIQUE,
          actor_clerk_id TEXT NOT NULL,
          update BYTEA NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (document_id, sequence),
          CONSTRAINT document_updates_nonempty CHECK (octet_length(update) > 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE collaboration.document_title_events (
          operation_id UUID PRIMARY KEY,
          document_id UUID NOT NULL REFERENCES collaboration.documents(id) ON DELETE CASCADE,
          actor_clerk_id TEXT NOT NULL,
          title TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE collaboration.document_title_events")
    op.execute("DROP TABLE collaboration.document_updates")
    op.execute("DROP TABLE collaboration.documents")
    op.execute("DROP SCHEMA collaboration")
