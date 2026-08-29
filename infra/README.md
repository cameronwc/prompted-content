# Infrastructure

All infrastructure is Terraform against the `cloudflare/cloudflare` provider,
pinned to **5.24.0**. Nothing is created by hand in the Cloudflare dashboard.

## Layout

- `bootstrap/` — creates ONLY the Terraform state bucket (`prompted-terraform-state`),
  using **local state**. Run once. Its `terraform.tfstate` stays local and out
  of git (covered by `.gitignore`); it describes a single bucket and can be
  reconstructed with `terraform import` if lost.
- `modules/content-cdn/` — reusable module: content bucket, explicit public
  access (custom domain + path allowlist; the r2.dev whole-bucket toggle is
  declared and disabled), cache rules.
- `envs/dev/`, `envs/prod/` — one directory per environment, separate state
  keys, side-by-side configuration. Not workspaces.

## Credentials — environment variables only

Nothing account-specific or secret is committed. Required environment:

| Variable | Used by | Value |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | provider | scoped API token, see below |
| `TF_VAR_account_id` | all configs | Cloudflare account ID |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | envs/* state backend | an R2 API token's S3 credential pair |
| `AWS_ENDPOINT_URL_S3` | envs/* state backend | `https://<account_id>.r2.cloudflarestorage.com` |

### Minimum-scope API token

Create the token at dash.cloudflare.com → My Profile → API Tokens with
exactly:

- **Account → Workers R2 Storage → Edit**, scoped to this one account only
  (covers buckets, managed domains, and custom-domain bindings)
- Only if a custom domain is bound: **Zone → Zone WAF → Edit** and
  **Zone → Cache Rules → Edit**, scoped to the single zone in
  `terraform.tfvars` (for the two `cloudflare_ruleset` resources)

Nothing broader. No user, billing, DNS-wide, or all-account scopes.

The state backend uses a *separate* R2 S3 credential pair (Account →
R2 → Manage R2 API Tokens → Object Read & Write, scoped to the
`prompted-terraform-state` bucket only).

## First-time bring-up order

```sh
# 1. state bucket (local state)
cd infra/bootstrap
terraform init && terraform plan
terraform apply

# 2. environments (state in R2)
cd ../envs/dev
terraform init      # needs the AWS_* variables above
terraform plan
terraform apply     # or: make tf-apply-dev CONFIRM=1 from the repo root
```

Until `bootstrap` has been applied, `envs/*` backend initialization has
nothing to talk to; `terraform init -backend=false` still installs providers
and lets `terraform validate` run.

## Safety

- Every bucket carries `lifecycle { prevent_destroy = true }`. The content
  bucket is the product; the state bucket is the record of the product.
- `terraform destroy` is not part of any workflow here and `make` exposes no
  target for it.
- Apply is always explicit: `make tf-apply-*` requires `CONFIRM=1`, and CI
  never applies (plan only).

## Custom domain

`cloudflare_r2_custom_domain` is supported by the pinned provider and is
created whenever `zone_id` + `custom_domain` are set in an environment's
`terraform.tfvars`. Until a zone exists in the account, both stay unset and
the bucket remains private (the r2.dev managed domain is explicitly
disabled). One genuinely manual prerequisite remains: registering/delegating
the domain and adding the zone to the Cloudflare account, which is outside
this repo's scope.
