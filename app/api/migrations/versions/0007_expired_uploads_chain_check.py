"""Unfinished uploads expire, and the worker's audit chain check is kept.

- vault.document gets a status 'expired': an upload started over an hour ago whose
  file never reached quarantine. The worker sets it; it doesn't count against the
  youth's storage limit.
- vault.audit_chain_check holds the result of the worker's hourly check of the
  whole audit chain (one row), so the admin page doesn't redo it on every load.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE vault.document DROP CONSTRAINT document_status_check;
        ALTER TABLE vault.document ADD CONSTRAINT document_status_check
            CHECK (status IN ('uploading', 'scanning', 'ready', 'rejected', 'deleted', 'expired'));

        CREATE TABLE vault.audit_chain_check (
            id              boolean PRIMARY KEY DEFAULT true CHECK (id),  -- only one row
            checked_at      timestamptz NOT NULL,
            last_event_id   bigint,    -- the newest row covered; NULL if the log was empty
            chain_broken_at bigint     -- first row that didn't match; NULL if intact
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE vault.audit_chain_check;
        -- 0006 has no 'expired'; those uploads never had a file, so they become deleted.
        UPDATE vault.document SET status = 'deleted', deleted_at = coalesce(deleted_at, now())
            WHERE status = 'expired';
        ALTER TABLE vault.document DROP CONSTRAINT document_status_check;
        ALTER TABLE vault.document ADD CONSTRAINT document_status_check
            CHECK (status IN ('uploading', 'scanning', 'ready', 'rejected', 'deleted'));
        """
    )
