# ---------------------------------------------------------------------------
# Doc Vault alerts (Doc Vault §11). One SNS topic; email for now, Slack later.
# Messages say what happened and where to look, never titles or names.
#
# The topic isn't KMS-encrypted: EventBridge can't publish to a topic under the
# AWS-managed SNS key, and the messages hold nothing sensitive.
#
# Log-based alarms (decrypt failures, refused downloads, audit chain, worker
# heartbeat, purge failures) are in terraform/app/alarms.tf and use this topic.
# ---------------------------------------------------------------------------
resource "aws_sns_topic" "alerts" {
  name = "${local.prefix}-alerts"
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EventBridgeAndCloudWatchPublish"
      Effect    = "Allow"
      Principal = { Service = ["events.amazonaws.com", "cloudwatch.amazonaws.com"] }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.alerts.arn
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

# Email subscriptions wait until the address clicks AWS's confirmation link.
resource "aws_sns_topic_subscription" "alerts_email" {
  for_each  = toset(var.alert_emails)
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

locals {
  audit_bucket = "id-vault-audit-${local.account_id}" # terraform/bootstrap, shared by all environments
  app_roles    = [var.api_role_name, var.worker_role_name]
}

# 1. GuardDuty found malware in an upload. The file stays in quarantine; the worker rejects it.
resource "aws_cloudwatch_event_rule" "malware_found" {
  name        = "${local.prefix}-alert-malware"
  description = "Doc Vault alert: malware in an upload"
  event_pattern = jsonencode({
    source        = ["aws.guardduty"]
    "detail-type" = ["GuardDuty Malware Protection Object Scan Result"]
    detail = {
      s3ObjectDetails   = { bucketName = [aws_s3_bucket.quarantine.id] }
      scanResultDetails = { scanResultStatus = ["THREATS_FOUND"] }
    }
  })
}

resource "aws_cloudwatch_event_target" "malware_found" {
  rule = aws_cloudwatch_event_rule.malware_found.name
  arn  = aws_sns_topic.alerts.arn
  input_transformer {
    input_paths    = { key = "$.detail.s3ObjectDetails.objectKey", time = "$.time" }
    input_template = "\"Doc Vault (${var.environment}): malware found in upload <key> at <time>. It stays in quarantine for review and was rejected.\""
  }
}

# 2. Anyone other than the app touched a vault document (§11: "any S3 read not from
#    the application role"), including refused attempts. CloudTrail records vault
#    object access (terraform/audit). Quarantine object access isn't in the trail;
#    its bucket policy refuses everyone else.
resource "aws_cloudwatch_event_rule" "outside_vault_access" {
  name        = "${local.prefix}-alert-outside-access"
  description = "Doc Vault alert: vault documents accessed by someone other than the app"
  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource       = ["s3.amazonaws.com"]
      requestParameters = { bucketName = [aws_s3_bucket.vault.id] }
      "$or" = [
        { userIdentity = { type = [{ "anything-but" = ["AssumedRole", "AWSService"] }] } },
        { userIdentity = { type = ["AssumedRole"], sessionContext = { sessionIssuer = { userName = [{ "anything-but" = local.app_roles }] } } } },
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "outside_vault_access" {
  rule = aws_cloudwatch_event_rule.outside_vault_access.name
  arn  = aws_sns_topic.alerts.arn
  input_transformer {
    input_paths = {
      action = "$.detail.eventName"
      who    = "$.detail.userIdentity.arn"
      error  = "$.detail.errorCode"
      time   = "$.time"
    }
    input_template = "\"Doc Vault (${var.environment}): <action> on the vault bucket by <who> at <time> (error: <error>). Only the app may touch documents. Check CloudTrail.\""
  }
}

# 3. Someone changed who can use the vault key or the buckets (the 10e lock-down).
#    Our own Terraform changes show up here too, as a record.
resource "aws_cloudwatch_event_rule" "protection_changed" {
  name        = "${local.prefix}-alert-protection-changed"
  description = "Doc Vault alert: vault key or bucket protection changed"
  event_pattern = jsonencode({
    "detail-type" = ["AWS API Call via CloudTrail"]
    "$or" = [
      {
        source = ["aws.kms"]
        detail = {
          eventName = ["PutKeyPolicy", "CreateGrant", "DisableKey", "ScheduleKeyDeletion", "DisableKeyRotation"]
          resources = { ARN = [data.aws_kms_alias.vault.target_key_arn] }
        }
      },
      {
        source = ["aws.s3"]
        detail = {
          eventName = [
            "PutBucketPolicy", "DeleteBucketPolicy", "PutBucketAcl", "PutBucketPublicAccessBlock",
            "DeleteBucketPublicAccessBlock", "PutBucketVersioning", "PutBucketEncryption", "DeleteBucketEncryption",
          ]
          requestParameters = { bucketName = [aws_s3_bucket.vault.id, aws_s3_bucket.quarantine.id, local.audit_bucket] }
        }
      },
    ]
  })
}

resource "aws_cloudwatch_event_target" "protection_changed" {
  rule = aws_cloudwatch_event_rule.protection_changed.name
  arn  = aws_sns_topic.alerts.arn
  input_transformer {
    input_paths    = { action = "$.detail.eventName", who = "$.detail.userIdentity.arn", time = "$.time" }
    input_template = "\"Doc Vault (${var.environment}): <action> by <who> at <time>. Vault key or bucket protection changed. If this wasn't planned, check CloudTrail now.\""
  }
}

# 4. Scan results that failed five times sit in the dead-letter queue: uploads stuck on "Checking…".
resource "aws_cloudwatch_metric_alarm" "scan_results_stuck" {
  alarm_name          = "${local.prefix}-scan-results-stuck"
  alarm_description   = "Doc Vault: scan results failed 5 times and are in the dead-letter queue. Uploads may be stuck on Checking. See the vault-worker log."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.scan_result_dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

# 5. IAM Access Analyzer (§9): reviews resource policies (buckets, keys, roles,
#    queues, topics) whenever they change, and reports any access from outside the
#    account. Expected outside access is archived by rules below, so only new
#    findings alert. Account analyzers for external access are free.
resource "aws_accessanalyzer_analyzer" "account" {
  analyzer_name = "mhp-${var.environment}-external-access"
  type          = "ACCOUNT"
}

# Expected outside access, found on the first scan (all IAM roles, none on Doc Vault
# data): the AWS Organization's management account and its single sign-on, and our
# GitHub deploy role (terraform/cicd). Anything else that appears raises an alert.
resource "aws_accessanalyzer_archive_rule" "organization" {
  analyzer_name = aws_accessanalyzer_analyzer.account.analyzer_name
  rule_name     = "organization-management-account"
  filter {
    criteria = "principal.AWS"
    contains = [var.organization_management_account]
  }
}

resource "aws_accessanalyzer_archive_rule" "federated" {
  analyzer_name = aws_accessanalyzer_analyzer.account.analyzer_name
  rule_name     = "organization-sso-and-github-deploy"
  filter {
    criteria = "principal.Federated"
    eq = [
      "arn:aws:iam::${local.account_id}:saml-provider/SSOConsoleAccess",
      "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com",
    ]
  }
}

resource "aws_cloudwatch_event_rule" "outside_access_found" {
  name        = "${local.prefix}-alert-access-analyzer"
  description = "Doc Vault alert: Access Analyzer found new access from outside the account"
  event_pattern = jsonencode({
    source        = ["aws.access-analyzer"]
    "detail-type" = ["Access Analyzer Finding"]
    detail        = { status = ["ACTIVE"] }
  })
}

resource "aws_cloudwatch_event_target" "outside_access_found" {
  rule = aws_cloudwatch_event_rule.outside_access_found.name
  arn  = aws_sns_topic.alerts.arn
  input_transformer {
    input_paths    = { type = "$.detail.resourceType", resource = "$.detail.resource", time = "$.time" }
    input_template = "\"Doc Vault (${var.environment}): Access Analyzer found new access from outside the account to <type> <resource> at <time>. If it isn't expected, check the resource's policy now.\""
  }
}
