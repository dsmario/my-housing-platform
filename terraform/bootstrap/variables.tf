variable "region" {
  description = "AWS region for the build."
  type        = string
  default     = "us-west-1"
}

variable "account_id" {
  description = "AWS account ID the build is allowed to act in. Terraform refuses to run in any other account."
  type        = string
  default     = "623096509073"
}

variable "audit_retention_days" {
  description = "Object Lock retention for audit records, in days. 90 days in dev (test data only). Production sets 1826 in its own state."
  type        = number
  default     = 90
}
