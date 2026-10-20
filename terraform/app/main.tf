# Dev app tier: ALB with HTTPS on dev.myhousingplatform.org, Fargate services for
# web (Next.js) and api (FastAPI), RDS PostgreSQL 16. Lean dev shape per the deck:
# single-AZ database, tasks in public subnets with tight security groups, no NAT.

locals {
  name = "mhp-${var.environment}"
}

data "terraform_remote_state" "network" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "${var.environment}/network/terraform.tfstate"
    region = var.state_region
  }
}

data "terraform_remote_state" "dns" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "${var.environment}/dns/terraform.tfstate"
    region = var.state_region
  }
}

data "terraform_remote_state" "cicd" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "${var.environment}/cicd/terraform.tfstate"
    region = var.state_region
  }
}

locals {
  net       = data.terraform_remote_state.network.outputs
  zone_id   = data.terraform_remote_state.dns.outputs.zone_id
  hostname  = data.terraform_remote_state.dns.outputs.zone_name
  api_image = "${data.terraform_remote_state.cicd.outputs.ecr_repository_url}:${var.api_image_tag}"
  web_image = "${data.terraform_remote_state.cicd.outputs.web_ecr_repository_url}:${var.web_image_tag}"
  app_port  = 8000
}
