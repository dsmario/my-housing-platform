"""Doc Vault sharing codes (Doc Vault section 6).

A supporter makes a short code and gives it to a youth in person or by text. The
youth enters it to share a document or a category with that supporter. Both
people take part, and a typo can't reach the wrong person.

- share_code: one use, expires after a day. Only a SHA-256 of the code is
  stored. share_grant_id records the share it was used for.

The shares themselves stay in identity.share_grant and share_grant_scope
(purpose_code 'doc_vault'), as in 0004.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE vault.share_code (
            id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            supporter_pseudonym_id uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            code_hash              bytea NOT NULL UNIQUE,
            created_at             timestamptz NOT NULL DEFAULT now(),
            expires_at             timestamptz NOT NULL,
            used_at                timestamptz,
            share_grant_id         uuid REFERENCES identity.share_grant (id),
            CHECK ((used_at IS NULL) = (share_grant_id IS NULL))
        );
        CREATE INDEX ON vault.share_code (supporter_pseudonym_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE vault.share_code")
