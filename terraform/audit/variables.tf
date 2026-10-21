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

variable "state_bucket" {
  type    = string
  default = "id-vault-tfstate-623096509073"
}

variable "state_region" {
  type    = string
  default = "us-west-1"
}

variable "trail_name" {
  description = "Must match cloudtrail_name in bootstrap, which grants this trail use of the vault key."
  type        = string
  default     = "mhp-account-trail"
}
