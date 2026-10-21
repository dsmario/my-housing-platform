# Account CloudTrail (Doc Vault §2, §7): management events in every region, plus
# S3 data events on the vault bucket, written to the Object Lock audit bucket and
# encrypted with the vault key. Log file validation makes tampering detectable.

data "terraform_remote_state" "bootstrap" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "bootstrap/terraform.tfstate"
    region = var.state_region
  }
}

data "terraform_remote_state" "vault" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "${var.environment}/vault/terraform.tfstate"
    region = var.state_region
  }
}

locals {
  audit_bucket = data.terraform_remote_state.bootstrap.outputs.audit_bucket
  audit_arn    = "arn:aws:s3:::${local.audit_bucket}"
  vault_bucket = data.terraform_remote_state.vault.outputs.vault_bucket
  trail_arn    = "arn:aws:cloudtrail:${var.region}:${var.account_id}:trail/${var.trail_name}"
}

resource "aws_s3_bucket_policy" "audit" {
  bucket = local.audit_bucket
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [local.audit_arn, "${local.audit_arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "CloudTrailAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = local.audit_arn
        Condition = { StringEquals = { "aws:SourceArn" = local.trail_arn } }
      },
      {
        Sid       = "CloudTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${local.audit_arn}/cloudtrail/AWSLogs/${var.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"  = "bucket-owner-full-control"
            "aws:SourceArn" = local.trail_arn
          }
        }
      },
    ]
  })
}

resource "aws_cloudtrail" "account" {
  name                          = var.trail_name
  s3_bucket_name                = local.audit_bucket
  s3_key_prefix                 = "cloudtrail"
  kms_key_id                    = data.terraform_remote_state.bootstrap.outputs.vault_kms_key_arn
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true

  advanced_event_selector {
    name = "Management events"
    field_selector {
      field  = "eventCategory"
      equals = ["Management"]
    }
  }

  advanced_event_selector {
    name = "Vault bucket object access"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }
    field_selector {
      field       = "resources.ARN"
      starts_with = ["arn:aws:s3:::${local.vault_bucket}/"]
    }
  }

  depends_on = [aws_s3_bucket_policy.audit]
}
