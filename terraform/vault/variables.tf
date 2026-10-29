variable "region" {
  type    = string
  default = "us-west-2"
}

variable "account_id" {
  type    = string
  default = "623096509073"
}

variable "environment" {
  type    = string
  default = "dev"
}
variable "scan_retention_days" {
  description = "Noncurrent version expiry in the quarantine and vault buckets."
  type        = number
  default     = 1
}

variable "app_origin" {
  description = "The web app's address. Only it may post uploads to quarantine from a browser."
  type        = string
  default     = "https://dev.myhousingplatform.org"
}

variable "api_role_name" {
  description = "The Doc Vault API's task role (terraform/app). Only it signs uploads and reads documents."
  type        = string
  default     = "mhp-dev-api"
}

variable "worker_role_name" {
  description = "The Doc Vault worker's task role (terraform/app). Only it reads quarantine and writes documents."
  type        = string
  default     = "mhp-dev-vault-worker"
}

variable "alert_emails" {
  description = "Who gets Doc Vault alerts (§11). Each address must confirm AWS's subscription email once."
  type        = list(string)
  default     = ["davidsmario@valocitydigital.com"]
}
