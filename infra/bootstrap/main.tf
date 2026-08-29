# Bootstrap: creates ONLY the R2 bucket that holds Terraform state for
# envs/dev and envs/prod. This configuration uses LOCAL state on purpose —
# it is the answer to the chicken-and-egg problem of storing state in a
# bucket that Terraform itself creates.
#
# Run once:
#   export CLOUDFLARE_API_TOKEN=...   # never in a file
#   export TF_VAR_account_id=...      # never in a file
#   terraform init && terraform apply
#
# The resulting terraform.tfstate stays LOCAL and out of git (.gitignore
# covers *.tfstate). It contains only this one bucket; if it is ever lost,
# recover with:
#   terraform import cloudflare_r2_bucket.terraform_state \
#     '<account_id>/prompted-terraform-state/default'

terraform {
  required_version = ">= 1.9"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.24.0"
    }
  }
}

# Auth comes from the CLOUDFLARE_API_TOKEN environment variable.
provider "cloudflare" {}

variable "account_id" {
  description = "Cloudflare account ID. Supply via TF_VAR_account_id; never commit it."
  type        = string
}

variable "state_bucket_name" {
  description = "Name of the R2 bucket that stores Terraform state."
  type        = string
  default     = "prompted-terraform-state"
}

variable "location" {
  description = "R2 location hint for the state bucket."
  type        = string
  default     = "wnam"
}

resource "cloudflare_r2_bucket" "terraform_state" {
  account_id    = var.account_id
  name          = var.state_bucket_name
  location      = var.location
  storage_class = "Standard"

  # Losing this bucket loses all environment state. Never destroyable.
  lifecycle {
    prevent_destroy = true
  }
}

output "state_bucket" {
  value       = cloudflare_r2_bucket.terraform_state.name
  description = "Bucket the envs/* S3-compatible backends point at."
}

output "state_endpoint" {
  value       = "https://${var.account_id}.r2.cloudflarestorage.com"
  description = "S3-compatible endpoint; export as AWS_ENDPOINT_URL_S3 for envs/* init."
  sensitive   = true # contains the account id
}
