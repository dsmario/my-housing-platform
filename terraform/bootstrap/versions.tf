terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  backend "s3" {
    bucket       = "id-vault-tfstate-623096509073"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-west-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]

  default_tags {
    tags = {
      Project = "id-vault"
      Owner   = "valocity-digital"
      Managed = "terraform"
    }
  }
}

# The state bucket lives in us-west-1; everything else follows var.region.
provider "aws" {
  alias               = "state"
  region              = var.state_region
  allowed_account_ids = [var.account_id]

  default_tags {
    tags = {
      Project = "id-vault"
      Owner   = "valocity-digital"
      Managed = "terraform"
    }
  }
}
