"""Identity and sharing tables (schema "identity").

From the FP ERD, with the review comments applied:
- People are referenced by pseudonym everywhere. Only subject_pseudonym and the
  two profile tables hold the real subject_id.
- adult_profile added (the ERD had none).
- Keys whose lookup tables aren't designed yet (scope profile, field group) are
  plain text for now.

Additions not in the ERD:
- account_subject.identity_provider_subject links a sign-in (Cognito "sub") to
  a subject.
- share_grant.revoked_at, for immediate revocation (Doc Vault section 6).
- created_at on every table.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA identity;

        -- One row per person who can sign in. Holds no contact details.
        CREATE TABLE identity.account_subject (
            id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_type              text NOT NULL,   -- e.g. youth, supportive_adult, social_worker
            account_state             text NOT NULL DEFAULT 'active',
            display_name              text,
            locale                    text NOT NULL DEFAULT 'en',
            identity_provider_subject text UNIQUE,     -- Cognito "sub"
            created_at                timestamptz NOT NULL DEFAULT now()
        );

        -- The id other tables use for a person. unlinked_at set = no longer
        -- tied to the subject.
        CREATE TABLE identity.subject_pseudonym (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_id        uuid NOT NULL REFERENCES identity.account_subject (id),
            pseudonym_purpose text NOT NULL,
            unlinked_at       timestamptz,
            created_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON identity.subject_pseudonym (subject_id);

        CREATE TABLE identity.verified_channel (
            id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_pseudonym_id   uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            channel_type           text NOT NULL,   -- e.g. email, sms
            destination_ciphertext text NOT NULL,
            destination_hash       text NOT NULL,
            created_at             timestamptz NOT NULL DEFAULT now(),
            UNIQUE (channel_type, destination_hash)
        );
        CREATE INDEX ON identity.verified_channel (subject_pseudonym_id);

        CREATE TABLE identity.youth_profile (
            id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_id             uuid NOT NULL UNIQUE REFERENCES identity.account_subject (id),
            subject_pseudonym_id   uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            self_attested_age_band text,
            care_status            text,
            created_at             timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE identity.adult_profile (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_id           uuid NOT NULL UNIQUE REFERENCES identity.account_subject (id),
            subject_pseudonym_id uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            adult_role           text NOT NULL,   -- e.g. supportive_adult, social_worker, casa, navigator
            created_at           timestamptz NOT NULL DEFAULT now()
        );

        -- A youth blocks an adult, including any channel the adult might
        -- reappear with.
        CREATE TABLE identity.adult_block (
            id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            youth_pseudonym_id     uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            adult_pseudonym_id     uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            blocked_channel_hashes text[] NOT NULL DEFAULT '{}',
            block_state            text NOT NULL DEFAULT 'active',
            created_at             timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON identity.adult_block (youth_pseudonym_id);

        -- A youth shares with one grantee. Rows are never deleted; revoking
        -- sets revoked_at.
        CREATE TABLE identity.share_grant (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            youth_pseudonym_id   uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            grantee_pseudonym_id uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            scope_profile_key    text,
            purpose_code         text NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now(),
            revoked_at           timestamptz,
            CHECK (youth_pseudonym_id <> grantee_pseudonym_id)
        );
        CREATE INDEX ON identity.share_grant (youth_pseudonym_id);
        CREATE INDEX ON identity.share_grant (grantee_pseudonym_id) WHERE revoked_at IS NULL;

        -- What a grant covers: a field group, optionally narrowed to one object
        -- (for example one Doc Vault document).
        CREATE TABLE identity.share_grant_scope (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            share_grant_id  uuid NOT NULL REFERENCES identity.share_grant (id),
            field_group_key text NOT NULL,
            object_id       uuid,
            created_at      timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON identity.share_grant_scope (share_grant_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA identity CASCADE")
