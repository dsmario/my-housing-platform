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

variable "auth_callback_path" {
  description = "Where the Next.js app receives the sign-in result. Not under /api, which the ALB routes to FastAPI."
  type        = string
  default     = "/auth/callback/cognito"
}

variable "staff_auth_callback_path" {
  description = "Where the Next.js app receives a staff sign-in result (Auth.js provider id cognito-staff)."
  type        = string
  default     = "/auth/callback/cognito-staff"
}

variable "local_app_url" {
  description = "Local stack origin, allowed as a sign-in callback for development."
  type        = string
  default     = "http://localhost:3000"
}

variable "threat_protection_mode" {
  description = "Plus tier threat protection for the users pool: AUDIT logs risk only, ENFORCED acts on it. Production uses ENFORCED."
  type        = string
  default     = "AUDIT"
}
