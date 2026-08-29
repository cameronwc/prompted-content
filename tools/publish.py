#!/usr/bin/env python3
"""Publish dist/catalog.json and poses/*/ images to the R2 content bucket.

Configuration resolution, in order:
  1. `terraform output -json` in infra/envs/<env> (bucket_name, account_id,
     public_base_url, state_bucket) — the normal path.
  2. Explicit environment variables PROMPTED_BUCKET and PROMPTED_ACCOUNT_ID,
     with a clear warning that Terraform state was unavailable.

Credentials are never read from files or flags: boto3 uses the standard
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment variables (an R2 S3
token pair). The endpoint is https://<account_id>.r2.cloudflarestorage.com,
overridable with PROMPTED_S3_ENDPOINT.

Layout written to the bucket:
  catalog/v<catalog_version>.json   immutable, one per build, never deleted
  latest.json                       small pointer to the current version
  poses/<ulid>/thumb.jpg|detail.jpg immutable, skipped if already present

Dry-run is the default and prints the exact object keys that would be
written. Uploading requires --confirm. Nothing is ever deleted, so the
previous five (and all older) catalog versions remain available.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys

from common import DIST_DIR, REPO_ROOT, iter_pose_dirs

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
LATEST_CACHE = "public, max-age=300"


def resolve_config(env: str, tf_dir: str | None) -> dict:
    tf_dir = tf_dir or str(REPO_ROOT / "infra" / "envs" / env)
    try:
        out = subprocess.run(
            ["terraform", f"-chdir={tf_dir}", "output", "-json", "-no-color"],
            capture_output=True, text=True, check=True, timeout=60,
        )
        outputs = {k: v.get("value") for k, v in json.loads(out.stdout).items()}
        if outputs.get("bucket_name") and outputs.get("account_id"):
            print(f"Resolved config from Terraform outputs in {tf_dir}")
            return {
                "source": "terraform",
                "bucket": outputs["bucket_name"],
                "account_id": outputs["account_id"],
                "public_base_url": outputs.get("public_base_url"),
            }
        reason = "state has no bucket_name/account_id outputs"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, json.JSONDecodeError) as exc:
        reason = getattr(exc, "stderr", "") or str(exc)
    reason = next((l.strip("│╷╵ ") for l in reason.splitlines()
                   if "Error" in l or "error" in l), reason.strip())[:160]

    bucket = os.environ.get("PROMPTED_BUCKET")
    account_id = os.environ.get("PROMPTED_ACCOUNT_ID")
    if not (bucket and account_id):
        sys.exit(
            f"error: could not read Terraform outputs from {tf_dir} "
            f"({reason.strip()}), and no PROMPTED_BUCKET / PROMPTED_ACCOUNT_ID "
            f"fallback environment variables are set."
        )
    print(
        f"WARNING: Terraform state unavailable in {tf_dir} ({reason.strip()}); "
        f"falling back to PROMPTED_BUCKET / PROMPTED_ACCOUNT_ID environment variables.",
        file=sys.stderr,
    )
    return {"source": "env", "bucket": bucket, "account_id": account_id,
            "public_base_url": os.environ.get("PROMPTED_PUBLIC_BASE_URL")}


def planned_uploads() -> list[tuple[str, str, str, str]]:
    """(local_path, key, content_type, cache_control), catalog first."""
    catalog_path = DIST_DIR / "catalog.json"
    if not catalog_path.is_file():
        sys.exit("error: dist/catalog.json does not exist — run `make build` first.")
    version = json.loads(catalog_path.read_text())["catalog_version"]

    uploads = [
        (str(catalog_path), f"catalog/v{version}.json", "application/json", IMMUTABLE_CACHE),
    ]
    for pose_dir in iter_pose_dirs():
        for name in ("thumb.jpg", "detail.jpg"):
            path = pose_dir / name
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            uploads.append((str(path), f"poses/{pose_dir.name}/{name}", ctype, IMMUTABLE_CACHE))
    return uploads, version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=("dev", "prod"), default="dev")
    parser.add_argument("--tf-dir", help="Override the Terraform directory to read outputs from")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually upload. Without this flag, dry-run only.")
    args = parser.parse_args()

    config = resolve_config(args.env, args.tf_dir)
    endpoint = os.environ.get(
        "PROMPTED_S3_ENDPOINT",
        f"https://{config['account_id']}.r2.cloudflarestorage.com",
    )
    uploads, version = planned_uploads()
    latest = {
        "path": f"catalog/v{version}.json",
        "catalog_version": version,
    }

    print(f"\nTarget: bucket '{config['bucket']}' ({args.env}), endpoint {endpoint}")
    print(f"Catalog version: {version}\n")

    if not args.confirm:
        print("DRY RUN — no objects will be written. Keys that would be written:")
        for _, key, ctype, cache in uploads:
            print(f"  PUT {key}  ({ctype}; cache-control: {cache})")
        print(f"  PUT latest.json  (application/json; cache-control: {LATEST_CACHE})")
        print(f"       -> {json.dumps(latest)}")
        print(f"\n{len(uploads) + 1} objects total. Re-run with --confirm to upload.")
        return 0

    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        sys.exit("error: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (R2 S3 token pair) "
                 "must be set in the environment to upload.")

    import boto3  # deferred so dry-run works without credentials configured
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="auto")

    def exists(key: str) -> bool:
        try:
            s3.head_object(Bucket=config["bucket"], Key=key)
            return True
        except s3.exceptions.ClientError:
            return False

    uploaded = skipped = 0
    for path, key, ctype, cache in uploads:
        # Pose images and versioned catalogs are immutable: never overwrite,
        # never delete. Existing keys are skipped.
        if exists(key):
            skipped += 1
            continue
        s3.upload_file(path, config["bucket"], key,
                       ExtraArgs={"ContentType": ctype, "CacheControl": cache})
        uploaded += 1
        print(f"  uploaded {key}")

    s3.put_object(
        Bucket=config["bucket"], Key="latest.json",
        Body=json.dumps(latest).encode(),
        ContentType="application/json", CacheControl=LATEST_CACHE,
    )
    print(f"\nDone: {uploaded} uploaded, {skipped} already present, latest.json -> v{version}.")
    print("Previous catalog versions are retained; this tool never deletes objects.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
