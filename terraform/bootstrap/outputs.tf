output "tfstate_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "vault_kms_key_arn" {
  value = aws_kms_key.vault.arn
}

output "audit_bucket" {
  value = aws_s3_bucket.audit.bucket
}
