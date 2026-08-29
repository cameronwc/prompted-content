# State backend: the R2 bucket created by infra/bootstrap, via Terraform's
# S3-compatible backend. Backend blocks cannot use variables, so everything
# identifying or secret is supplied through the AWS-SDK environment
# variables the s3 backend honors:
#
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   an R2 API token's S3 pair
#   AWS_ENDPOINT_URL_S3                         https://<account_id>.r2.cloudflarestorage.com
#
# Nothing account-specific is committed here.

terraform {
  backend "s3" {
    bucket = "prompted-terraform-state"
    key    = "envs/dev/terraform.tfstate"
    region = "auto"

    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_s3_checksum            = true
    use_path_style              = true
  }
}
