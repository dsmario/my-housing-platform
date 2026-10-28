output "users_pool_id" {
  value = aws_cognito_user_pool.users.id
}

output "users_issuer" {
  description = "OIDC issuer the API uses to verify tokens."
  value       = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
}

output "web_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "web_client_secret" {
  value     = aws_cognito_user_pool_client.web.client_secret
  sensitive = true
}

output "login_domain" {
  value = "https://${local.auth_domain}"
}

output "staff_pool_id" {
  value = aws_cognito_user_pool.staff.id
}

output "staff_issuer" {
  description = "OIDC issuer the API uses to verify staff tokens."
  value       = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.staff.id}"
}

output "staff_web_client_id" {
  value = aws_cognito_user_pool_client.staff_web.id
}

output "staff_web_client_secret" {
  value     = aws_cognito_user_pool_client.staff_web.client_secret
  sensitive = true
}

output "staff_login_domain" {
  value = "https://${aws_cognito_user_pool_domain.staff.domain}.auth.${var.region}.amazoncognito.com"
}
