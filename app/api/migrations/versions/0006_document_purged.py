"""Doc Vault deletion (Doc Vault section 10): when a deleted document's files were purged.

Deleting a document removes its wrapped key at once (0004's CHECK). The worker
then removes every S3 version of the file and sets purged_at. Until then, a
database backup (which still holds the key) plus an old S3 version could be
read; afterwards a backup holds a key with nothing left to open.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE vault.document ADD COLUMN purged_at timestamptz;
        ALTER TABLE vault.document ADD CONSTRAINT document_purged_only_when_deleted
            CHECK (purged_at IS NULL OR status = 'deleted');
        CREATE INDEX document_waiting_for_purge ON vault.document (deleted_at)
            WHERE status = 'deleted' AND purged_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX vault.document_waiting_for_purge;
        ALTER TABLE vault.document DROP COLUMN purged_at;
        """
    )
