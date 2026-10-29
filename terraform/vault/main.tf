data "aws_caller_identity" "current" {}

data "aws_kms_alias" "vault" {
  name = "alias/id-vault-vault"
}

locals {
  prefix     = "id-vault-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
}

# ---------------------------------------------------------------------------
# Buckets. Uploads land in quarantine. Only clean, re-encoded copies reach vault.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "quarantine" {
  bucket = "${local.prefix}-quarantine-${local.account_id}"
}

resource "aws_s3_bucket" "vault" {
  bucket = "${local.prefix}-vault-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_versioning" "vault" {
  bucket = aws_s3_bucket.vault.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = data.aws_kms_alias.vault.target_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "vault" {
  bucket = aws_s3_bucket.vault.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = data.aws_kms_alias.vault.target_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "quarantine" {
  bucket                  = aws_s3_bucket.quarantine.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "vault" {
  bucket                  = aws_s3_bucket.vault.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = var.scan_retention_days }
  }
  # Safety net (Doc Vault §3): the worker deletes clean files once they reach the
  # vault, and rejected files stay 7 days for support review. This expires every
  # object after 7 days, so nothing outlives that, and clears stalled uploads.
  rule {
    id     = "expire-rejected"
    status = "Enabled"
    filter {}
    expiration { days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
  depends_on = [aws_s3_bucket_versioning.quarantine]
}

resource "aws_s3_bucket_lifecycle_configuration" "vault" {
  bucket = aws_s3_bucket.vault.id
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = var.scan_retention_days }
  }
  depends_on = [aws_s3_bucket_versioning.vault]
}

# Browsers post uploads straight to quarantine (Doc Vault §3), only from the app.
resource "aws_s3_bucket_cors_configuration" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  cors_rule {
    allowed_origins = [var.app_origin]
    allowed_methods = ["POST"]
    allowed_headers = ["*"]
    max_age_seconds = 300
  }
}

# Bucket policies (Doc Vault §2, §8, §9): TLS only, and each object action is
# open to exactly the roles that need it. Everyone else is refused, admins
# included; they can still change these policies, which CloudTrail records.
locals {
  api_role    = "arn:aws:iam::${local.account_id}:role/${var.api_role_name}"
  worker_role = "arn:aws:iam::${local.account_id}:role/${var.worker_role_name}"
  scan_role   = aws_iam_role.guardduty_scan.arn
}

resource "aws_s3_bucket_policy" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid      = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*",
        Resource = [aws_s3_bucket.quarantine.arn, "${aws_s3_bucket.quarantine.arn}/*"],
      Condition = { Bool = { "aws:SecureTransport" = "false" } } },
      # Browsers upload with links the API signs; GuardDuty writes a check object.
      { Sid      = "OnlyApiAndScannerWrite", Effect = "Deny", Principal = "*", Action = "s3:PutObject",
        Resource = "${aws_s3_bucket.quarantine.arn}/*",
      Condition = { ArnNotEquals = { "aws:PrincipalArn" = [local.api_role, local.scan_role] } } },
      # The API never reads quarantine; only the scanner and the worker do.
      { Sid    = "OnlyWorkerAndScannerRead", Effect = "Deny", Principal = "*",
        Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = "${aws_s3_bucket.quarantine.arn}/*",
      Condition = { ArnNotEquals = { "aws:PrincipalArn" = [local.worker_role, local.scan_role] } } },
      { Sid    = "OnlyWorkerDeletes", Effect = "Deny", Principal = "*",
        Action = ["s3:DeleteObject", "s3:DeleteObjectVersion"], Resource = "${aws_s3_bucket.quarantine.arn}/*",
      Condition = { ArnNotEquals = { "aws:PrincipalArn" = local.worker_role } } },
    ]
  })
}

resource "aws_s3_bucket_policy" "vault" {
  bucket = aws_s3_bucket.vault.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid      = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*",
        Resource = [aws_s3_bucket.vault.arn, "${aws_s3_bucket.vault.arn}/*"],
      Condition = { Bool = { "aws:SecureTransport" = "false" } } },
      # Documents are read only by the API, which checks access on every request (§5).
      { Sid    = "OnlyApiReads", Effect = "Deny", Principal = "*",
        Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = "${aws_s3_bucket.vault.arn}/*",
      Condition = { ArnNotEquals = { "aws:PrincipalArn" = local.api_role } } },
      # Documents are written and removed only by the worker (§3, §10).
      { Sid    = "OnlyWorkerWritesAndDeletes", Effect = "Deny", Principal = "*",
        Action = ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"], Resource = "${aws_s3_bucket.vault.arn}/*",
      Condition = { ArnNotEquals = { "aws:PrincipalArn" = local.worker_role } } },
    ]
  })
}

# ---------------------------------------------------------------------------
# Malware scanning: GuardDuty Malware Protection for S3 on the quarantine bucket.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "guardduty_scan" {
  name = "${local.prefix}-guardduty-malware-scan"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "malware-protection-plan.guardduty.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Inline permissions for the S3 malware scan role, per AWS's Malware Protection for S3
# IAM prerequisite. Scoped to the quarantine bucket and the vault KMS key only.
resource "aws_iam_role_policy" "guardduty_scan_s3" {
  name = "guardduty-scan-s3"
  role = aws_iam_role.guardduty_scan.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ManagedEventBridgeRule"
        Effect   = "Allow"
        Action   = ["events:PutRule", "events:DeleteRule", "events:PutTargets", "events:RemoveTargets"]
        Resource = ["arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:rule/DO-NOT-DELETE-AmazonGuardDutyMalwareProtectionS3*"]
        Condition = {
          StringLike = { "events:ManagedBy" = "malware-protection-plan.guardduty.amazonaws.com" }
        }
      },
      {
        Sid      = "DescribeManagedEventBridgeRule"
        Effect   = "Allow"
        Action   = ["events:DescribeRule", "events:ListTargetsByRule"]
        Resource = ["arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:rule/DO-NOT-DELETE-AmazonGuardDutyMalwareProtectionS3*"]
      },
      {
        Sid      = "PostScanTags"
        Effect   = "Allow"
        Action   = ["s3:PutObjectTagging", "s3:GetObjectTagging", "s3:PutObjectVersionTagging", "s3:GetObjectVersionTagging"]
        Resource = ["${aws_s3_bucket.quarantine.arn}/*"]
      },
      {
        Sid      = "BucketNotifications"
        Effect   = "Allow"
        Action   = ["s3:PutBucketNotification", "s3:GetBucketNotification", "s3:ListBucket"]
        Resource = [aws_s3_bucket.quarantine.arn]
      },
      {
        Sid      = "ValidationObject"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${aws_s3_bucket.quarantine.arn}/malware-protection-resource-validation-object"]
      },
      {
        Sid      = "ScanObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = ["${aws_s3_bucket.quarantine.arn}/*"]
      },
      {
        Sid      = "DecryptForScan"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = [data.aws_kms_alias.vault.target_key_arn]
        Condition = {
          StringLike = { "kms:ViaService" = "s3.${var.region}.amazonaws.com" }
        }
      }
    ]
  })
}

resource "aws_guardduty_malware_protection_plan" "quarantine" {
  role = aws_iam_role.guardduty_scan.arn
  protected_resource {
    s3_bucket {
      bucket_name = aws_s3_bucket.quarantine.id
    }
  }
  actions {
    tagging {
      status = "ENABLED"
    }
  }
  depends_on = [aws_iam_role_policy.guardduty_scan_s3]
}

# Scan results go to EventBridge, then to the worker queue.
resource "aws_cloudwatch_event_rule" "scan_result" {
  name        = "${local.prefix}-scan-result"
  description = "GuardDuty malware scan results for the quarantine bucket"
  event_pattern = jsonencode({
    source        = ["aws.guardduty"]
    "detail-type" = ["GuardDuty Malware Protection Object Scan Result"]
    detail        = { s3ObjectDetails = { bucketName = [aws_s3_bucket.quarantine.id] } }
  })
}

resource "aws_sqs_queue" "scan_result_dlq" {
  name                      = "${local.prefix}-scan-result-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = data.aws_kms_alias.vault.target_key_arn
}

resource "aws_sqs_queue" "scan_result" {
  name                       = "${local.prefix}-scan-result"
  visibility_timeout_seconds = 300 # longer than the worst case: 60 s cleaning plus scan checks and S3
  kms_master_key_id          = data.aws_kms_alias.vault.target_key_arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.scan_result_dlq.arn
    maxReceiveCount     = 5
  })
}

# Queue policy so EventBridge can send scan results. The handoff flagged this as missing.
resource "aws_sqs_queue_policy" "scan_result" {
  queue_url = aws_sqs_queue.scan_result.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowEventBridgeScanResults"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.scan_result.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.scan_result.arn } }
    }]
  })
}

resource "aws_cloudwatch_event_target" "scan_result" {
  rule      = aws_cloudwatch_event_rule.scan_result.name
  target_id = "scan-result-queue"
  arn       = aws_sqs_queue.scan_result.arn
}
