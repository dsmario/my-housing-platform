"""Create messages table.

Baseline for databases the walking skeleton already created on startup, hence
IF NOT EXISTS.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id         serial PRIMARY KEY,
            text       text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE messages")
