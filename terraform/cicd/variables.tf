variable "region" {
  type    = string
  default = "us-west-1"
}

variable "account_id" {
  type    = string
  default = "623096509073"
}

variable "environment" {
  type    = string
  default = "dev"
}
variable "github_repo" {
  description = "GitHub repo allowed to deploy, as owner/name."
  type        = string
  default     = "dsmario/my-housing-platform"
}
