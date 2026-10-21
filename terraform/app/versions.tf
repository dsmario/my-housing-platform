terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.80" }
    random = { source = "hashicorp/random", version = "~> 3.7" }
  }
  backend "s3" {
    bucket       = "id-vault-tfstate-623096509073"
    key          = "dev/app/terraform.tfstate"
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
