# Non-secret configuration only. The account ID and API credentials come
# from environment variables (TF_VAR_account_id, CLOUDFLARE_API_TOKEN).
bucket_name = "prompted-content"
location    = "wnam"

# Bind a custom domain once a zone exists, e.g.:
# zone_id       = "<zone id>"
# custom_domain = "content.prompted.example"
