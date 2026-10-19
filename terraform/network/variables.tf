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

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "azs" {
  type    = list(string)
  default = ["us-west-1a", "us-west-1c"]
}

variable "app_port" {
  type    = number
  default = 8000
}
