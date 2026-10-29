"""Doc Vault tables (schema "vault"), from Doc-Vault-Architecture-v3.

- youth_data_key: one wrapped (encrypted) data key per youth. Never plaintext.
  Destroying it (wrapped_key = NULL) makes all that youth's documents unreadable.
- document: metadata only; file bytes live in S3 (SeaweedFS locally). Deleting a
  document removes its wrapped key in the same transaction (crypto-shredding).
- audit_event: append-only. Each row carries a hash of the previous row, so an
  edit or deletion breaks the chain. UPDATE, DELETE and TRUNCATE are refused.
- audit_seal: one row per sealed batch written to the Object Lock bucket.

Shares are not a separate table. They use identity.share_grant and
identity.share_grant_scope:
  field_group_key = 'vault.document', object_id = the document id
  field_group_key = 'vault.category:<category key>' for a whole category
Revoking a grant (revoked_at) takes effect on the next request.

Audit rows don't use foreign keys, because they outlive the documents and
accounts they mention (5-year retention).

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA vault;

        CREATE TABLE vault.youth_data_key (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            youth_pseudonym_id uuid NOT NULL UNIQUE REFERENCES identity.subject_pseudonym (id),
            wrapped_key        bytea,          -- NULL once destroyed
            key_provider       text NOT NULL,  -- kms, or local (development only)
            wrapping_key_id    text NOT NULL,  -- KMS key ARN or local key name
            created_at         timestamptz NOT NULL DEFAULT now(),
            destroyed_at       timestamptz,
            CHECK ((wrapped_key IS NULL) = (destroyed_at IS NOT NULL))
        );

        CREATE TABLE vault.document (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_pseudonym_id    uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            document_category_key text NOT NULL,   -- e.g. id_documents, income, housing
            document_type_key     text,
            title                 text NOT NULL,
            content_type          text NOT NULL,
            size_bytes            bigint NOT NULL CHECK (size_bytes > 0),
            status                text NOT NULL DEFAULT 'uploading'
                CHECK (status IN ('uploading', 'scanning', 'ready', 'rejected', 'deleted')),
            quarantine_object_key text,
            vault_object_key      text,
            wrapped_document_key  bytea,   -- wrapped with the youth's data key
            created_at            timestamptz NOT NULL DEFAULT now(),
            status_changed_at     timestamptz NOT NULL DEFAULT now(),
            deleted_at            timestamptz,
            CHECK (status <> 'ready' OR (vault_object_key IS NOT NULL AND wrapped_document_key IS NOT NULL)),
            CHECK (status <> 'deleted' OR (wrapped_document_key IS NULL AND deleted_at IS NOT NULL))
        );
        CREATE INDEX ON vault.document (owner_pseudonym_id) WHERE status <> 'deleted';

        -- Audit log --------------------------------------------------------

        CREATE SEQUENCE vault.audit_event_id_seq;

        CREATE TABLE vault.audit_event (
            id                 bigint PRIMARY KEY,   -- set by the chain trigger
            occurred_at        timestamptz NOT NULL DEFAULT now(),
            action             text NOT NULL
                CHECK (action IN ('upload', 'view', 'download', 'preview', 'share', 'revoke', 'delete')),
            outcome            text NOT NULL CHECK (outcome IN ('allowed', 'denied')),
            actor_pseudonym_id uuid,
            youth_pseudonym_id uuid,
            document_id        uuid,
            share_grant_id     uuid,
            ip_address         inet,
            user_agent         text,
            detail             jsonb,
            prev_hash          bytea NOT NULL,
            row_hash           bytea NOT NULL
        );
        ALTER SEQUENCE vault.audit_event_id_seq OWNED BY vault.audit_event.id;
        CREATE INDEX ON vault.audit_event (youth_pseudonym_id, occurred_at);
        CREATE INDEX ON vault.audit_event (document_id);

        -- The text that row_hash covers. Fixed format (UTC time, empty string
        -- for NULL) so any checker can recompute it.
        CREATE FUNCTION vault.audit_event_payload(e vault.audit_event) RETURNS bytea
        LANGUAGE sql IMMUTABLE AS $$
            SELECT convert_to(concat_ws('|',
                e.id::text,
                to_char(e.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                e.action,
                e.outcome,
                coalesce(e.actor_pseudonym_id::text, ''),
                coalesce(e.youth_pseudonym_id::text, ''),
                coalesce(e.document_id::text, ''),
                coalesce(e.share_grant_id::text, ''),
                coalesce(host(e.ip_address), ''),
                coalesce(e.user_agent, ''),
                coalesce(e.detail::text, '')
            ), 'UTF8')
        $$;

        -- Links each new row to the previous one. The lock makes inserts take
        -- turns, so ids follow commit order and the chain has no forks.
        CREATE FUNCTION vault.audit_event_chain() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(727002);
            NEW.id := nextval('vault.audit_event_id_seq');
            SELECT row_hash INTO NEW.prev_hash
              FROM vault.audit_event ORDER BY id DESC LIMIT 1;
            NEW.prev_hash := coalesce(NEW.prev_hash, sha256(convert_to('mhp-vault-audit-genesis', 'UTF8')));
            NEW.row_hash := sha256(NEW.prev_hash || vault.audit_event_payload(NEW));
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER audit_event_chain BEFORE INSERT ON vault.audit_event
            FOR EACH ROW EXECUTE FUNCTION vault.audit_event_chain();

        CREATE FUNCTION vault.refuse_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END
        $$;

        CREATE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON vault.audit_event
            FOR EACH ROW EXECUTE FUNCTION vault.refuse_change();
        CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON vault.audit_event
            FOR EACH STATEMENT EXECUTE FUNCTION vault.refuse_change();

        -- Returns the id of the first row whose hash doesn't match, or NULL
        -- if the whole chain is intact.
        CREATE FUNCTION vault.verify_audit_chain() RETURNS bigint
        LANGUAGE plpgsql STABLE AS $$
        DECLARE
            e    vault.audit_event;
            prev bytea := sha256(convert_to('mhp-vault-audit-genesis', 'UTF8'));
        BEGIN
            FOR e IN SELECT * FROM vault.audit_event ORDER BY id LOOP
                IF e.prev_hash <> prev OR e.row_hash <> sha256(prev || vault.audit_event_payload(e)) THEN
                    RETURN e.id;
                END IF;
                prev := e.row_hash;
            END LOOP;
            RETURN NULL;
        END
        $$;

        CREATE TABLE vault.audit_seal (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            sealed_at      timestamptz NOT NULL DEFAULT now(),
            first_event_id bigint NOT NULL,
            last_event_id  bigint NOT NULL,
            head_hash      bytea NOT NULL,   -- row_hash of last_event_id
            object_key     text NOT NULL,    -- where the batch went in the audit bucket
            CHECK (first_event_id <= last_event_id)
        );
        CREATE TRIGGER audit_seal_append_only BEFORE UPDATE OR DELETE ON vault.audit_seal
            FOR EACH ROW EXECUTE FUNCTION vault.refuse_change();
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA vault CASCADE")
