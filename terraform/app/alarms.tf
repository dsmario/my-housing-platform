# ---------------------------------------------------------------------------
# Doc Vault alarms from the app's logs (Doc Vault §11). The API and worker write
# lines starting VAULT_ for these (app/api/vault.py, vault_worker.py); log
# filters count them, and alarms send to the alerts topic in terraform/vault
# (which also holds the malware, outside-access and protection-change alerts).
# Dev thresholds: dev is quiet, so low numbers catch real problems.
# ---------------------------------------------------------------------------
locals {
  alerts_topic = data.terraform_remote_state.vault.outputs.alerts_topic_arn
  metrics      = "MHP/DocVault/${var.environment}"

  # name => log group, the log line to count, and the alarm.
  vault_alarms = {
    decrypt-failures = {
      log_group   = aws_cloudwatch_log_group.api.name
      pattern     = "VAULT_DECRYPT_FAILED"
      period      = 300
      threshold   = 1
      operator    = "GreaterThanOrEqualToThreshold"
      missing     = "notBreaching"
      description = "Doc Vault: a document couldn't be decrypted. Possible key policy or role change, or a damaged file. See the api log (VAULT_DECRYPT_FAILED)."
    }
    refused-downloads = {
      log_group   = aws_cloudwatch_log_group.api.name
      pattern     = "VAULT_DOWNLOAD_DENIED"
      period      = 300
      threshold   = 10
      operator    = "GreaterThanOrEqualToThreshold"
      missing     = "notBreaching"
      description = "Doc Vault: 10 or more refused document requests in 5 minutes. Possible misuse of a share or ID guessing. Check the audit log (download, denied)."
    }
    audit-chain-broken = {
      log_group   = aws_cloudwatch_log_group.vault_worker.name
      pattern     = "VAULT_AUDIT_CHAIN_BROKEN"
      period      = 3600
      threshold   = 1
      operator    = "GreaterThanOrEqualToThreshold"
      missing     = "notBreaching"
      description = "Doc Vault: the audit log's hash chain is broken. A row was changed or removed. Investigate before anything else; see the vault-worker log."
    }
    purge-failed = {
      log_group   = aws_cloudwatch_log_group.vault_worker.name
      pattern     = "VAULT_PURGE_FAILED"
      period      = 900
      threshold   = 1
      operator    = "GreaterThanOrEqualToThreshold"
      missing     = "notBreaching"
      description = "Doc Vault: purging deleted documents failed. Deleted files may linger in S3. See the vault-worker log."
    }
    seal-failed = {
      log_group   = aws_cloudwatch_log_group.vault_worker.name
      pattern     = "VAULT_SEAL_FAILED"
      period      = 900
      threshold   = 1
      operator    = "GreaterThanOrEqualToThreshold"
      missing     = "notBreaching"
      description = "Doc Vault: the daily audit seal failed (chain broken, or the seal didn't read back the same). Seals stop until fixed. See the vault-worker log."
    }
    seal-overdue = {
      log_group   = aws_cloudwatch_log_group.vault_worker.name
      pattern     = "VAULT_AUDIT_SEALED"
      period      = 3600
      evaluations = 26 # every hour of the last 26 without a seal
      threshold   = 1
      operator    = "LessThanThreshold"
      missing     = "breaching"
      description = "Doc Vault: no audit seal in 26 hours. The audit log isn't being locked away daily. See the vault-worker log."
    }
    worker-down = {
      log_group   = aws_cloudwatch_log_group.vault_worker.name
      pattern     = "VAULT_WORKER_HEARTBEAT"
      period      = 900
      threshold   = 1
      operator    = "LessThanThreshold"
      missing     = "breaching" # no heartbeat at all is the problem
      description = "Doc Vault: no worker heartbeat for 15 minutes. Uploads aren't scanned or saved, and deletions aren't purged. Check the vault-worker ECS service."
    }
  }
}

resource "aws_cloudwatch_log_metric_filter" "vault" {
  for_each       = local.vault_alarms
  name           = "doc-vault-${each.key}"
  log_group_name = each.value.log_group
  pattern        = "\"${each.value.pattern}\""
  metric_transformation {
    name      = each.key
    namespace = local.metrics
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "vault" {
  for_each            = local.vault_alarms
  alarm_name          = "${local.name}-doc-vault-${each.key}"
  alarm_description   = each.value.description
  namespace           = local.metrics
  metric_name         = each.key
  statistic           = "Sum"
  period              = each.value.period
  evaluation_periods  = lookup(each.value, "evaluations", 1)
  datapoints_to_alarm = lookup(each.value, "evaluations", 1)
  threshold           = each.value.threshold
  comparison_operator = each.value.operator
  treat_missing_data  = each.value.missing
  alarm_actions       = [local.alerts_topic]
  ok_actions          = [local.alerts_topic]
  depends_on          = [aws_cloudwatch_log_metric_filter.vault]
}
