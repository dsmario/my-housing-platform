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
  description = "Bucket holding the other stacks' state, read for network, DNS and ECR outputs."
  type        = string
  default     = "id-vault-tfstate-623096509073"
}

variable "api_image_tag" {
  description = "Tag of the API image in ECR. Tags are immutable, so every build gets a new one."
  type        = string
}

variable "web_image_tag" {
  description = "Tag of the web image in ECR."
  type        = string
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "state_region" {
  description = "Region of the Terraform state bucket."
  type        = string
  default     = "us-west-1"
}
