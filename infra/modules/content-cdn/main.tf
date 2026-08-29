# content-cdn: one R2 bucket for the pose catalog + images, with explicit
# public read access and cache rules.
#
# R2 has no per-path bucket ACL, so "public read for the catalog paths" is
# built as: a custom-domain binding on the bucket (the only public entry
# point — the r2.dev managed domain is explicitly disabled), plus a zone
# firewall rule that blocks every path except the known catalog paths, plus
# cache rules matched to the content's mutability.
#
# When no zone/custom domain is supplied (custom_domain = null), only the
# bucket is created and nothing is publicly reachable; the r2.dev toggle
# stays declared and disabled so public access is always explicit in code.

terraform {
  required_version = ">= 1.9"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.24.0"
    }
  }
}

resource "cloudflare_r2_bucket" "content" {
  account_id    = var.account_id
  name          = var.bucket_name
  location      = var.location
  storage_class = "Standard"

  # This bucket IS the pose catalog. Destroying it is a business-ending
  # event, not an inconvenience. Never destroyable.
  lifecycle {
    prevent_destroy = true
  }
}

# Explicitly declare the r2.dev managed domain DISABLED: the whole-bucket
# public toggle is never how this bucket is exposed.
resource "cloudflare_r2_managed_domain" "content" {
  account_id  = var.account_id
  bucket_name = cloudflare_r2_bucket.content.name
  enabled     = false
}

# Public entry point: custom domain bound to the bucket.
resource "cloudflare_r2_custom_domain" "content" {
  count = var.custom_domain == null ? 0 : 1

  account_id  = var.account_id
  bucket_name = cloudflare_r2_bucket.content.name
  domain      = var.custom_domain
  zone_id     = var.zone_id
  enabled     = true
  min_tls     = "1.2"
}

# Scope public reads to the catalog paths only. Everything else on the
# content hostname is blocked at the zone edge.
resource "cloudflare_ruleset" "path_allowlist" {
  count = var.custom_domain == null ? 0 : 1

  zone_id = var.zone_id
  name    = "${var.bucket_name} catalog path allowlist"
  kind    = "zone"
  phase   = "http_request_firewall_custom"

  rules = [
    {
      description = "Block everything on the content host except catalog paths"
      expression  = <<-EOT
        http.host eq "${var.custom_domain}" and not (
          starts_with(http.request.uri.path, "/poses/") or
          starts_with(http.request.uri.path, "/catalog/") or
          http.request.uri.path eq "/latest.json"
        )
      EOT
      action      = "block"
      enabled     = true
    },
  ]
}

# Cache rules: pose image paths are content-addressed by ULID and immutable,
# so they get a year at the edge and in browsers. latest.json is the mutable
# pointer, so it gets minutes.
resource "cloudflare_ruleset" "cache" {
  count = var.custom_domain == null ? 0 : 1

  zone_id = var.zone_id
  name    = "${var.bucket_name} cache rules"
  kind    = "zone"
  phase   = "http_request_cache_settings"

  rules = [
    {
      description = "Short TTL on the latest.json pointer"
      expression  = "http.host eq \"${var.custom_domain}\" and http.request.uri.path eq \"/latest.json\""
      action      = "set_cache_settings"
      enabled     = true
      action_parameters = {
        cache = true
        edge_ttl = {
          mode    = "override_origin"
          default = var.latest_ttl_seconds
        }
        browser_ttl = {
          mode    = "override_origin"
          default = var.latest_ttl_seconds
        }
      }
    },
    {
      description = "Long TTL on immutable pose image and versioned catalog paths"
      expression  = "http.host eq \"${var.custom_domain}\" and (starts_with(http.request.uri.path, \"/poses/\") or starts_with(http.request.uri.path, \"/catalog/\"))"
      action      = "set_cache_settings"
      enabled     = true
      action_parameters = {
        cache = true
        edge_ttl = {
          mode    = "override_origin"
          default = var.immutable_ttl_seconds
        }
        browser_ttl = {
          mode    = "override_origin"
          default = var.immutable_ttl_seconds
        }
      }
    },
  ]
}
