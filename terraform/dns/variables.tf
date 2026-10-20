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

variable "parent_domain" {
  description = "Registered at GoDaddy. Only the environment subdomain is delegated to Route 53."
  type        = string
  default     = "myhousingplatform.org"
}
