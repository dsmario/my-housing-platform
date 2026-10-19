# ID Vault: bootstrap

Creates the state bucket, the vault KMS key, and the audit bucket (Object Lock, compliance mode).

Run from this folder on your Mac:

    terraform init
    terraform plan -out=bootstrap.plan

Review the plan before any apply. Apply only after review:

    terraform apply bootstrap.plan

This bootstrap starts with local state. After the state bucket exists, migrate state into it.
Terraform refuses to run unless the active AWS credentials belong to account 623096509073.
