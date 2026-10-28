"""Planning and rules tables (schema "planning").

From the FP ERD, with the review comments applied:
- Every person reference is a subject_pseudonym id.
- housing_readiness_plan has no plan_status. A plan is ready when a
  readiness_assessment with is_ready = true exists for it.
- target_site_program_id stays as an optional uuid with no foreign key (the
  team is moving away from target sites, and there is no site table).
- guidance_country_fips and jurisdiction_keys are kept, nullable, until the plan
  defines them.

Other choices:
- Keys whose lookup tables aren't designed yet (question version, document type,
  action, schema release) are plain text.
- schema_template and schema_instance columns are jsonb, not text, so they can
  be validated and queried.
- action_result has no readiness_assessment_id. readiness_assessment already
  points to its action_result, and keeping both would make a cycle.
- plan_input's three arrays hold the ids of the rows that went into that input,
  as a snapshot. Arrays can't have foreign keys.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA planning;

        -- Plan and what the youth has told us -----------------------------

        CREATE TABLE planning.housing_readiness_plan (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_pseudonym_id  uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            guidance_country_fips text,   -- meaning still open (ERD "??")
            created_at            timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON planning.housing_readiness_plan (subject_pseudonym_id);

        CREATE TABLE planning.plan_answer (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            plan_id              uuid NOT NULL REFERENCES planning.housing_readiness_plan (id),
            question_version_key text NOT NULL,
            answer_state         text NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON planning.plan_answer (plan_id);

        CREATE TABLE planning.youth_document_status (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_pseudonym_id uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            document_type_key    text NOT NULL,
            status               text NOT NULL,
            held_by              text,
            created_at           timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON planning.youth_document_status (subject_pseudonym_id);

        -- Rule releases (versioned, with rollback) -------------------------

        CREATE TABLE planning.rules_input_release (
            id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            rollback_release_id           uuid REFERENCES planning.rules_input_release (id),
            release_key                   text NOT NULL UNIQUE,
            input_rules_schema_release_id text,
            created_at                    timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE planning.rules_corpus_release (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            rollback_release_id uuid REFERENCES planning.rules_corpus_release (id),
            release_key         text NOT NULL UNIQUE,
            schema_release_id   text,
            jurisdiction_keys   text[],   -- may be too specific; the plan should define this
            created_at          timestamptz NOT NULL DEFAULT now()
        );

        -- Schemas that shape inputs, facts, task results and assessments ---

        CREATE TABLE planning.plan_input_schema (
            id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            plan_id                 uuid NOT NULL REFERENCES planning.housing_readiness_plan (id),
            input_schema_release_id text,
            schema_template         jsonb NOT NULL,
            created_at              timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON planning.plan_input_schema (plan_id);

        CREATE TABLE planning.plan_fact_schema (
            id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            plan_id                uuid NOT NULL REFERENCES planning.housing_readiness_plan (id),
            fact_schema_release_id text,
            schema_template        jsonb NOT NULL,
            created_at             timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON planning.plan_fact_schema (plan_id);

        CREATE TABLE planning.task_result_schema (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            release_key       text NOT NULL,
            schema_release_id text,
            schema_template   jsonb NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE planning.readiness_assessment_schema (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            release_key       text NOT NULL,
            schema_release_id text,
            schema_template   jsonb NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now()
        );

        -- Inputs sent to the rules, and evaluation history ------------------
        -- Evaluations are never edited; a new row supersedes the old one.

        CREATE TABLE planning.plan_input (
            id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            plan_input_schema_id      uuid NOT NULL REFERENCES planning.plan_input_schema (id),
            plan_answer_ids           uuid[] NOT NULL DEFAULT '{}',
            youth_document_status_ids uuid[] NOT NULL DEFAULT '{}',
            pathway_task_result_ids   uuid[] NOT NULL DEFAULT '{}',
            schema_instance           jsonb NOT NULL,
            created_at                timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON planning.plan_input (plan_input_schema_id);

        CREATE TABLE planning.rule_input_evaluation (
            id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            supersedes_evaluation_id uuid REFERENCES planning.rule_input_evaluation (id),
            plan_input_id            uuid NOT NULL REFERENCES planning.plan_input (id),
            rule_input_release_id    uuid NOT NULL REFERENCES planning.rules_input_release (id),
            recorded_at              timestamptz NOT NULL DEFAULT now(),
            snapshot_hash            text NOT NULL
        );
        CREATE INDEX ON planning.rule_input_evaluation (plan_input_id);

        CREATE TABLE planning.fact_snapshot (
            id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            recorded_at              timestamptz NOT NULL DEFAULT now(),
            subject_pseudonym_id     uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            fact_schema_id           uuid NOT NULL REFERENCES planning.plan_fact_schema (id),
            fact_schema_instance     jsonb NOT NULL,
            snapshot_hash            text NOT NULL,
            rule_input_evaluation_id uuid REFERENCES planning.rule_input_evaluation (id)
        );
        CREATE INDEX ON planning.fact_snapshot (subject_pseudonym_id);

        CREATE TABLE planning.rule_evaluation (
            id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            supersedes_evaluation_id uuid REFERENCES planning.rule_evaluation (id),
            fact_snapshot_id         uuid NOT NULL REFERENCES planning.fact_snapshot (id),
            rule_corpus_release_id   uuid NOT NULL REFERENCES planning.rules_corpus_release (id),
            recorded_at              timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON planning.rule_evaluation (fact_snapshot_id);

        CREATE TABLE planning.action_result (
            id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            rule_evaluation_id     uuid NOT NULL REFERENCES planning.rule_evaluation (id),
            recorded_at            timestamptz NOT NULL DEFAULT now(),
            action_key             text NOT NULL,
            target_site_program_id uuid   -- optional, no foreign key (see header)
        );
        CREATE INDEX ON planning.action_result (rule_evaluation_id);

        -- What the rules produce: tasks for the youth, and readiness --------

        CREATE TABLE planning.pathway_task (
            id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            source_action_result_id uuid NOT NULL REFERENCES planning.action_result (id),
            task_description        text NOT NULL,
            target_site_program_id  uuid,   -- optional, no foreign key (see header)
            subject_pseudonym_id    uuid NOT NULL REFERENCES identity.subject_pseudonym (id),
            created_at              timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON planning.pathway_task (subject_pseudonym_id);

        CREATE TABLE planning.pathway_task_result (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            pathway_task_id       uuid NOT NULL REFERENCES planning.pathway_task (id),
            result_description    text,
            task_result_schema_id uuid NOT NULL REFERENCES planning.task_result_schema (id),
            schema_instance       jsonb NOT NULL,
            created_at            timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON planning.pathway_task_result (pathway_task_id);

        CREATE TABLE planning.readiness_assessment (
            id                             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            action_result_id               uuid NOT NULL REFERENCES planning.action_result (id),
            is_ready                       boolean NOT NULL,
            readiness_assessment_schema_id uuid NOT NULL REFERENCES planning.readiness_assessment_schema (id),
            schema_instance                jsonb NOT NULL,
            housing_readiness_plan_id      uuid NOT NULL REFERENCES planning.housing_readiness_plan (id),
            created_at                     timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON planning.readiness_assessment (housing_readiness_plan_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA planning CASCADE")
