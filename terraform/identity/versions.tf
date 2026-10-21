terraform {
  required_version = ">= 1.10.0"
  required_providers {
    # v6 for aws_cognito_managed_login_branding (managed login v2, needed for passkeys).
    # The other stacks stay on v5 until they are upgraded deliberately.
    aws = { source = "hashicorp/aws", version = "6.67.0" }
  }
  backend "s3" {
    bucket       = "id-vault-tfstate-623096509073"
    key          = "dev/identity/terraform.tfstate"
    region       = "us-west-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { Project = "mhp", Environment = var.environment, Managed = "terraform" }
  }
}

# Cognito custom domains are fronted by CloudFront, so their certificate must be in us-east-1.
provider "aws" {
  alias               = "us_east_1"
  region              = "us-east-1"
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { Project = "mhp", Environment = var.environment, Managed = "terraform" }
  }
}
