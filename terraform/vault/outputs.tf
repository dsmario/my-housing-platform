output "quarantine_bucket" { value = aws_s3_bucket.quarantine.bucket }
output "vault_bucket" { value = aws_s3_bucket.vault.bucket }
output "scan_result_queue" { value = aws_sqs_queue.scan_result.url }
output "guardduty_scan_plan_id" { value = aws_guardduty_malware_protection_plan.quarantine.id }
output "alerts_topic_arn" { value = aws_sns_topic.alerts.arn }
