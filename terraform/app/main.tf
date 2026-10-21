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

data "terraform_remote_state" "identity" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "${var.environment}/identity/terraform.tfstate"
    region = var.state_region
  }
}

locals {
  net       = data.terraform_remote_state.network.outputs
  identity  = data.terraform_remote_state.identity.outputs
  zone_id   = data.terraform_remote_state.dns.outputs.zone_id
  hostname  = data.terraform_remote_state.dns.outputs.zone_name
  api_image = "${data.terraform_remote_state.cicd.outputs.ecr_repository_url}:${var.api_image_tag}"
  web_image = "${data.terraform_remote_state.cicd.outputs.web_ecr_repository_url}:${var.web_image_tag}"
  app_port  = 8000
}

# ---------------------------------------------------------------------------
# Web sign-in secrets: the Auth.js session key and the Cognito app client secret.
# ---------------------------------------------------------------------------
resource "random_password" "auth_secret" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "web_auth" {
  name                    = "${local.name}/web-auth"
  description             = "Auth.js session secret and Cognito web client secret."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "web_auth" {
  secret_id = aws_secretsmanager_secret.web_auth.id
  secret_string = jsonencode({
    AUTH_SECRET         = random_password.auth_secret.result
    AUTH_COGNITO_SECRET = local.identity.web_client_secret
  })
}
