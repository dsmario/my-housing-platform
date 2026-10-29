data "aws_caller_identity" "current" {}

locals {
  name_prefix = "id-vault"
  account_id  = data.aws_caller_identity.current.account_id
}

# ---------------------------------------------------------------------------
# Terraform state bucket. Native S3 locking (use_lockfile) needs no DynamoDB.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "tfstate" {
  provider = aws.state
  bucket   = "${local.name_prefix}-tfstate-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "tfstate" {
  provider = aws.state
  bucket   = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  provider = aws.state
  bucket   = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  provider                = aws.state
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "tfstate_tls_only" {
  provider = aws.state
  bucket   = aws_s3_bucket.tfstate.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.tfstate.arn,
        "${aws_s3_bucket.tfstate.arn}/*",
      ]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# ---------------------------------------------------------------------------
# Vault KMS key. Automatic annual rotation. Per-youth data keys are wrapped
# under this key at the application layer.
# ---------------------------------------------------------------------------
resource "aws_kms_key" "vault" {
  description             = "ID Vault master key (wraps per-youth data keys)"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  # Statement 1 is the AWS default (account IAM decides). Doc Vault requires
  # narrowing it to the application role; that lands with the app's real role.
  # Statement 2 lets the account CloudTrail trail (terraform/audit) encrypt logs.
  # Statement 3 lets EventBridge put GuardDuty scan results on the scan result
  # queue, which this key encrypts (terraform/vault).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "Enable IAM User Permissions"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudTrailEncryptLogs"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = ["kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
        Condition = {
          StringEquals = {
            "aws:SourceArn" = "arn:aws:cloudtrail:${var.region}:${local.account_id}:trail/${var.cloudtrail_name}"
          }
        }
      },
      {
        Sid       = "AllowEventBridgeScanResults"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource  = "*"
        Condition = {
          ArnLike = {
            "aws:SourceArn" = "arn:aws:events:${var.region}:${local.account_id}:rule/${local.name_prefix}-*-scan-result"
          }
        }
      },
    ]
  })
}

resource "aws_kms_alias" "vault" {
  name          = "alias/${local.name_prefix}-vault"
  target_key_id = aws_kms_key.vault.key_id
}

# ---------------------------------------------------------------------------
# Audit bucket. Object Lock in COMPLIANCE mode: nobody, including the root
# user, can delete or shorten retention before the period ends.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "audit" {
  bucket              = "${local.name_prefix}-audit-${local.account_id}"
  object_lock_enabled = true
}

resource "aws_s3_bucket_versioning" "audit" {
  bucket = aws_s3_bucket.audit.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.audit_retention_days
    }
  }
  depends_on = [aws_s3_bucket_versioning.audit]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.vault.arn
    }
  }
}

# Audit records expire once their lock does (Doc Vault §10); Object Lock prevents
# anything earlier. Old versions go a day later.
resource "aws_s3_bucket_lifecycle_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    id     = "expire-after-retention"
    status = "Enabled"
    filter {}
    expiration { days = var.audit_retention_days }
    noncurrent_version_expiration { noncurrent_days = 1 }
  }
  depends_on = [aws_s3_bucket_versioning.audit]
}

resource "aws_s3_bucket_public_access_block" "audit" {
  bucket                  = aws_s3_bucket.audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
