# Identity for MHP (Cognito Decision Brief, Oct 2026).
#   users pool: youth and supporters. Plus tier (threat protection). Username sign-in,
#               passkeys first, MFA optional. Email is an optional recovery contact.
#   staff pool: Valocity and Fostering Promise staff. Essentials tier. MFA required.
# Sign-in identifier and required attributes cannot change after creation.

data "terraform_remote_state" "dns" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "${var.environment}/dns/terraform.tfstate"
    region = var.state_region
  }
}

locals {
  name        = "mhp-${var.environment}"
  zone_id     = data.terraform_remote_state.dns.outputs.zone_id
  app_host    = data.terraform_remote_state.dns.outputs.zone_name
  auth_domain = "auth.${local.app_host}"
  app_url     = "https://${local.app_host}"
  mail_from   = "no-reply@${local.app_host}"
}

# ---------------------------------------------------------------------------
# Users pool: youth and supporters
# ---------------------------------------------------------------------------
resource "aws_cognito_user_pool" "users" {
  name                = "${local.name}-users"
  user_pool_tier      = "PLUS"
  deletion_protection = "ACTIVE"

  # Username sign-in only (no username_attributes / alias_attributes). Locked at creation.
  username_configuration {
    case_sensitive = false
  }

  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length                   = 12
    require_lowercase                = false
    require_uppercase                = false
    require_numbers                  = false
    require_symbols                  = false
    password_history_size            = 5
    temporary_password_validity_days = 7
  }

  sign_in_policy {
    allowed_first_auth_factors = ["PASSWORD", "WEB_AUTHN", "EMAIL_OTP"]
  }

  web_authn_configuration {
    relying_party_id  = local.auth_domain
    user_verification = "required"
  }

  # MFA by authenticator app; passkeys already verify the user. Email is not an MFA
  # factor: Cognito refuses email MFA when verified email is the only recovery path.
  # Email one-time codes remain available as a sign-in method (EMAIL_OTP above).
  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration {
    enabled = true
  }

  # Self-service recovery works only when a verified email exists. Everyone else goes
  # through staff-assisted recovery in the app.
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Dev: accounts are created by staff. Self sign-up is a later product decision.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  email_configuration {
    email_sending_account = "DEVELOPER"
    source_arn            = aws_sesv2_email_identity.app.arn
    from_email_address    = "My Housing Platform <${local.mail_from}>"
  }

  user_pool_add_ons {
    advanced_security_mode = var.threat_protection_mode
  }

  depends_on = [aws_sesv2_email_identity_policy.cognito]
}

resource "aws_cognito_user_group" "youth" {
  user_pool_id = aws_cognito_user_pool.users.id
  name         = "youth"
  description  = "Young people who own their plans and documents."
}

resource "aws_cognito_user_group" "supporter" {
  user_pool_id = aws_cognito_user_pool.users.id
  name         = "supporter"
  description  = "Supportive adults with access only to what a youth shares."
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${local.name}-web"
  user_pool_id                         = aws_cognito_user_pool.users.id
  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls = [
    "${local.app_url}${var.auth_callback_path}",
    "${var.local_app_url}${var.auth_callback_path}",
  ]
  logout_urls = [local.app_url, var.local_app_url]
  explicit_auth_flows = [
    "ALLOW_USER_AUTH",
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
  access_token_validity         = 60
  id_token_validity             = 60
  refresh_token_validity        = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

# Managed login on auth.dev.myhousingplatform.org (v2 supports passkeys).
resource "aws_acm_certificate" "auth" {
  provider          = aws.us_east_1
  domain_name       = local.auth_domain
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "auth_cert_validation" {
  for_each = {
    for o in aws_acm_certificate.auth.domain_validation_options : o.domain_name => o
  }
  zone_id = local.zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]
  ttl     = 300
}

resource "aws_acm_certificate_validation" "auth" {
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.auth.arn
  validation_record_fqdns = [for r in aws_route53_record.auth_cert_validation : r.fqdn]
}

resource "aws_cognito_user_pool_domain" "users" {
  domain                = local.auth_domain
  user_pool_id          = aws_cognito_user_pool.users.id
  certificate_arn       = aws_acm_certificate_validation.auth.certificate_arn
  managed_login_version = 2
}

resource "aws_route53_record" "auth" {
  zone_id = local.zone_id
  name    = local.auth_domain
  type    = "A"
  alias {
    name                   = aws_cognito_user_pool_domain.users.cloudfront_distribution
    zone_id                = aws_cognito_user_pool_domain.users.cloudfront_distribution_zone_id
    evaluate_target_health = false
  }
}

resource "aws_cognito_managed_login_branding" "web" {
  user_pool_id                = aws_cognito_user_pool.users.id
  client_id                   = aws_cognito_user_pool_client.web.id
  use_cognito_provided_values = true
}

# ---------------------------------------------------------------------------
# Staff pool: Valocity and Fostering Promise staff. MFA required.
# ---------------------------------------------------------------------------
resource "aws_cognito_user_pool" "staff" {
  name                     = "${local.name}-staff"
  user_pool_tier           = "ESSENTIALS"
  deletion_protection      = "ACTIVE"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  username_configuration {
    case_sensitive = false
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = false
    require_uppercase                = false
    require_numbers                  = false
    require_symbols                  = false
    password_history_size            = 10
    temporary_password_validity_days = 3
  }

  mfa_configuration = "ON"
  software_token_mfa_configuration {
    enabled = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  email_configuration {
    email_sending_account = "DEVELOPER"
    source_arn            = aws_sesv2_email_identity.app.arn
    from_email_address    = "My Housing Platform <${local.mail_from}>"
  }

  depends_on = [aws_sesv2_email_identity_policy.cognito]
}

resource "aws_cognito_user_group" "staff_admin" {
  user_pool_id = aws_cognito_user_pool.staff.id
  name         = "admin"
  description  = "Platform administration and usage reporting."
}

resource "aws_cognito_user_group" "staff_support" {
  user_pool_id = aws_cognito_user_pool.staff.id
  name         = "support"
  description  = "Staff-assisted account recovery and user support."
}

# Staff sign-in for the web app's /admin pages. A Cognito-provided login domain
# (no certificate or DNS): staff don't need a branded address.
resource "aws_cognito_user_pool_domain" "staff" {
  domain                = "${local.name}-staff"
  user_pool_id          = aws_cognito_user_pool.staff.id
  managed_login_version = 2
}

resource "aws_cognito_user_pool_client" "staff_web" {
  name                                 = "${local.name}-staff-web"
  user_pool_id                         = aws_cognito_user_pool.staff.id
  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls = [
    "${local.app_url}${var.staff_auth_callback_path}",
    "${var.local_app_url}${var.staff_auth_callback_path}",
  ]
  logout_urls = ["${local.app_url}/admin", "${var.local_app_url}/admin"]
  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
  access_token_validity         = 60
  id_token_validity             = 60
  refresh_token_validity        = 12
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }
}

resource "aws_cognito_managed_login_branding" "staff_web" {
  user_pool_id                = aws_cognito_user_pool.staff.id
  client_id                   = aws_cognito_user_pool_client.staff_web.id
  use_cognito_provided_values = true
}

# ---------------------------------------------------------------------------
# Email: SES identity for dev.myhousingplatform.org with DKIM. While SES is in
# sandbox, mail only reaches verified addresses. Production access is requested
# in the SES console.
# ---------------------------------------------------------------------------
resource "aws_sesv2_email_identity" "app" {
  email_identity = local.app_host
}

resource "aws_route53_record" "ses_dkim" {
  count   = 3
  zone_id = local.zone_id
  name    = "${aws_sesv2_email_identity.app.dkim_signing_attributes[0].tokens[count.index]}._domainkey.${local.app_host}"
  type    = "CNAME"
  ttl     = 600
  records = ["${aws_sesv2_email_identity.app.dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
}

resource "aws_route53_record" "spf" {
  zone_id = local.zone_id
  name    = local.app_host
  type    = "TXT"
  ttl     = 600
  records = ["v=spf1 include:amazonses.com -all"]
}

resource "aws_route53_record" "dmarc" {
  zone_id = local.zone_id
  name    = "_dmarc.${local.app_host}"
  type    = "TXT"
  ttl     = 600
  records = ["v=DMARC1; p=reject"]
}

resource "aws_sesv2_email_identity_policy" "cognito" {
  email_identity = aws_sesv2_email_identity.app.email_identity
  policy_name    = "cognito-send"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCognitoSend"
      Effect    = "Allow"
      Principal = { Service = "cognito-idp.amazonaws.com" }
      Action    = ["ses:SendEmail", "ses:SendRawEmail"]
      Resource  = aws_sesv2_email_identity.app.arn
      Condition = { StringEquals = { "aws:SourceAccount" = var.account_id } }
    }]
  })
}
