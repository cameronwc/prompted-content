output "bucket_name" {
  value       = cloudflare_r2_bucket.content.name
  description = "R2 bucket holding the catalog and images. Consumed by tools/publish.py."
}

output "account_id" {
  value       = var.account_id
  description = "Cloudflare account ID (needed to build the S3 endpoint)."
  sensitive   = true
}

output "public_base_url" {
  value = (
    var.custom_domain != null ? "https://${var.custom_domain}" : null
  )
  description = "Base URL the iOS app and share Worker read content from. Null until a custom domain is bound."
}
