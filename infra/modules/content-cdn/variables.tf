variable "account_id" {
  description = "Cloudflare account ID. Comes from the environment (TF_VAR_account_id); never committed."
  type        = string
}

variable "bucket_name" {
  description = "Name of the R2 bucket holding the catalog and images."
  type        = string
}

variable "location" {
  description = "R2 location hint."
  type        = string
  default     = "wnam"
}

variable "zone_id" {
  description = "Cloudflare zone the custom domain lives in. Required when custom_domain is set."
  type        = string
  default     = null
}

variable "custom_domain" {
  description = "Custom domain to bind to the bucket (e.g. content-dev.example.com). Null = bucket stays private."
  type        = string
  default     = null

  validation {
    condition     = var.custom_domain == null || var.zone_id != null
    error_message = "zone_id must be set when custom_domain is set."
  }
}

variable "immutable_ttl_seconds" {
  description = "Edge/browser TTL for immutable poses/<ulid>/ and versioned catalog paths."
  type        = number
  default     = 31536000 # 1 year
}

variable "latest_ttl_seconds" {
  description = "Edge/browser TTL for the mutable latest.json pointer."
  type        = number
  default     = 300 # 5 minutes
}
