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
