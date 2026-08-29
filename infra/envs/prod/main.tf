# prod environment for the Prompted content CDN.
#
# Credentials and identity come from the environment only:
#   CLOUDFLARE_API_TOKEN  API token (R2 edit on this account, zone rulesets
#                         if a custom domain is used; nothing broader)
#   TF_VAR_account_id     Cloudflare account ID
#
# State lives in the R2 bucket created by infra/bootstrap (see backend.tf).

terraform {
  required_version = ">= 1.9"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.24.0"
    }
  }
}

provider "cloudflare" {}

variable "account_id" {
  description = "Cloudflare account ID. Supply via TF_VAR_account_id; never commit it."
  type        = string
}

variable "bucket_name" {
  type = string
}

variable "location" {
  type    = string
  default = "wnam"
}

variable "zone_id" {
  type    = string
  default = null
}

variable "custom_domain" {
  type    = string
  default = null
}

variable "enable_managed_domain" {
  type    = bool
  default = false
}

variable "state_bucket" {
  description = "Name of the state bucket (matches infra/bootstrap)."
  type        = string
  default     = "prompted-terraform-state"
}

module "content_cdn" {
  source = "../../modules/content-cdn"

  account_id            = var.account_id
  bucket_name           = var.bucket_name
  location              = var.location
  zone_id               = var.zone_id
  custom_domain         = var.custom_domain
  enable_managed_domain = var.enable_managed_domain
}

# Consumed by tools/publish.py (terraform output -json) and, later, by the
# share-Worker repo via a terraform_remote_state data source. Keep names
# stable.
output "bucket_name" {
  value = module.content_cdn.bucket_name
}

output "account_id" {
  value     = module.content_cdn.account_id
  sensitive = true
}

output "public_base_url" {
  value = module.content_cdn.public_base_url
}

output "state_bucket" {
  value = var.state_bucket
}
