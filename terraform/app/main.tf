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

data "terraform_remote_state" "vault" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "${var.environment}/vault/terraform.tfstate"
    region = var.state_region
  }
}

data "terraform_remote_state" "bootstrap" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "bootstrap/terraform.tfstate"
    region = var.state_region
  }
}

locals {
  # Doc Vault: buckets, scan result queue and the vault KMS key.
  quarantine_bucket = data.terraform_remote_state.vault.outputs.quarantine_bucket
  vault_bucket      = data.terraform_remote_state.vault.outputs.vault_bucket
  scan_queue_url    = data.terraform_remote_state.vault.outputs.scan_result_queue
  scan_queue_arn    = "arn:aws:sqs:${var.region}:${var.account_id}:${basename(local.scan_queue_url)}"
  vault_key_arn     = data.terraform_remote_state.bootstrap.outputs.vault_kms_key_arn
  audit_bucket      = data.terraform_remote_state.bootstrap.outputs.audit_bucket
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

# The web server shows it to the API to vouch for the youth's IP and browser in
# Doc Vault audit rows (app/api/vault.py).
resource "random_password" "api_forward" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "web_auth" {
  name                    = "${local.name}/web-auth"
  description             = "Auth.js session secret, Cognito web client secrets (users and staff), and the web-to-API forwarding secret."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "web_auth" {
  secret_id = aws_secretsmanager_secret.web_auth.id
  secret_string = jsonencode({
    AUTH_SECRET               = random_password.auth_secret.result
    AUTH_COGNITO_SECRET       = local.identity.web_client_secret
    AUTH_STAFF_COGNITO_SECRET = local.identity.staff_web_client_secret
    API_FORWARD_SECRET        = random_password.api_forward.result
  })
}
