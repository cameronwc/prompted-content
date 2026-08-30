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

Promotion (--promote): prod never gets a fresh build. The EXACT catalog
version already published and verified in dev is copied to prod
(server-side CopyObject for the images, byte-identical catalog JSON), a
diff against current prod (poses added / changed / retired) is printed,
and latest.json is repointed. Refuses if any promoted record has
placeholder: true. Rollback (--rollback-to N) repoints prod latest.json
at an existing catalog/vN.json and touches nothing else.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys

from common import DIST_DIR, REPO_ROOT, iter_pose_dirs, load_pose

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
        # Upload the files each record actually references (thumb.jpg or
        # thumb_ai.jpg etc.), matching the keys the catalog points at.
        image = load_pose(pose_dir)["image"]
        for kind in ("thumb", "detail"):
            name = image[kind]
            path = pose_dir / name
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            uploads.append((str(path), f"poses/{pose_dir.name}/{name}", ctype, IMMUTABLE_CACHE))
    return uploads, version


def s3_client(config: dict):
    import boto3  # deferred so dry-run works without credentials configured
    endpoint = os.environ.get(
        "PROMPTED_S3_ENDPOINT",
        f"https://{config['account_id']}.r2.cloudflarestorage.com",
    )
    return boto3.client("s3", endpoint_url=endpoint, region_name="auto")


def require_credentials() -> None:
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        sys.exit("error: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (R2 S3 token pair) "
                 "must be set in the environment.")


def get_json(s3, bucket: str, key: str) -> dict | None:
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None
    except s3.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return None
        raise


def catalog_diff(new_catalog: dict, old_catalog: dict | None) -> dict:
    """Poses added / changed / retired between two catalog payloads."""
    new_poses = {p["id"]: p for p in new_catalog["poses"]}
    old_poses = {p["id"]: p for p in (old_catalog or {"poses": []})["poses"]}
    added = sorted(set(new_poses) - set(old_poses))
    retired = sorted(pid for pid, p in new_poses.items()
                     if p["status"] == "retired"
                     and old_poses.get(pid, {}).get("status") == "active")
    changed = sorted(pid for pid in set(new_poses) & set(old_poses)
                     if new_poses[pid] != old_poses[pid] and pid not in retired)
    # Poses present before but absent now would mean a deletion — that is
    # never allowed to happen silently.
    dropped = sorted(set(old_poses) - set(new_poses))
    return {"added": added, "changed": changed, "retired": retired, "dropped": dropped}


def print_diff(diff: dict) -> None:
    print(f"Diff vs current prod: {len(diff['added'])} added, "
          f"{len(diff['changed'])} changed, {len(diff['retired'])} retired")
    for label in ("added", "changed", "retired"):
        ids = diff[label]
        if ids:
            shown = ", ".join(ids[:5]) + (" …" if len(ids) > 5 else "")
            print(f"  {label}: {shown}")
    if diff["dropped"]:
        print(f"  WARNING: {len(diff['dropped'])} poses present in prod are "
              f"missing from the promoted catalog: {diff['dropped'][:5]} — "
              f"records must be retired, never removed.")


def check_no_placeholders(catalog: dict, target: str) -> None:
    blocked = [p["id"] for p in catalog["poses"] if p.get("placeholder")]
    if blocked:
        sys.exit(f"error: refusing to publish to {target}: {len(blocked)} records "
                 f"have placeholder: true (first: {', '.join(blocked[:5])}...). "
                 f"Nothing generated ever ships.")


def promote(confirm: bool, tf_dir_dev: str | None, tf_dir_prod: str | None) -> int:
    dev = resolve_config("dev", tf_dir_dev)
    prod = resolve_config("prod", tf_dir_prod)
    require_credentials()
    s3 = s3_client(dev)
    if dev["account_id"] != prod["account_id"]:
        sys.exit("error: dev and prod resolve to different accounts; "
                 "server-side copy is not possible.")

    dev_latest = get_json(s3, dev["bucket"], "latest.json")
    if not dev_latest:
        sys.exit("error: dev has no latest.json — publish and verify dev first.")
    version = dev_latest["catalog_version"]
    catalog_key = dev_latest["path"]
    catalog = get_json(s3, dev["bucket"], catalog_key)
    if not catalog:
        sys.exit(f"error: dev latest.json points at missing {catalog_key}")

    check_no_placeholders(catalog, "prod")

    prod_latest = get_json(s3, prod["bucket"], "latest.json")
    prod_catalog = (get_json(s3, prod["bucket"], prod_latest["path"])
                    if prod_latest else None)
    print(f"Promoting dev catalog v{version} ({catalog_key}, "
          f"{len(catalog['poses'])} poses) -> bucket '{prod['bucket']}'")
    if prod_latest:
        print(f"Current prod: v{prod_latest['catalog_version']}")
    else:
        print("Current prod: empty (first promotion)")
    print_diff(catalog_diff(catalog, prod_catalog))

    image_keys = [k for p in catalog["poses"]
                  for k in (p["image"]["thumb"], p["image"]["detail"])]

    if not confirm:
        print(f"\nDRY RUN — no objects will be written. Would copy dev -> prod:")
        print(f"  COPY {catalog_key}  (byte-identical, no rebuild)")
        for key in image_keys[:10]:
            print(f"  COPY {key}")
        if len(image_keys) > 10:
            print(f"  ... {len(image_keys) - 10} more image keys")
        print(f"  PUT  latest.json -> {json.dumps({'path': catalog_key, 'catalog_version': version})}")
        print(f"\nRe-run with --confirm to promote.")
        return 0

    def exists(key: str) -> bool:
        try:
            s3.head_object(Bucket=prod["bucket"], Key=key)
            return True
        except s3.exceptions.ClientError:
            return False

    copied = skipped = 0
    for key in [catalog_key] + image_keys:
        if exists(key):
            skipped += 1
            continue
        s3.copy_object(
            Bucket=prod["bucket"], Key=key,
            CopySource={"Bucket": dev["bucket"], "Key": key},
            MetadataDirective="COPY",
        )
        copied += 1
    s3.put_object(
        Bucket=prod["bucket"], Key="latest.json",
        Body=json.dumps({"path": catalog_key, "catalog_version": version}).encode(),
        ContentType="application/json", CacheControl=LATEST_CACHE,
    )
    print(f"\nPromoted: {copied} objects copied, {skipped} already present, "
          f"prod latest.json -> v{version}.")
    print("Previous catalog versions are retained; nothing was deleted.")
    return 0


def rollback(version: int, confirm: bool, tf_dir: str | None) -> int:
    prod = resolve_config("prod", tf_dir)
    require_credentials()
    s3 = s3_client(prod)
    key = f"catalog/v{version}.json"
    catalog = get_json(s3, prod["bucket"], key)
    if not catalog:
        sys.exit(f"error: prod has no {key}; rollback targets must already exist.")
    current = get_json(s3, prod["bucket"], "latest.json")
    print(f"Rollback: prod latest.json "
          f"{'v' + str(current['catalog_version']) if current else '(none)'} -> v{version} "
          f"({len(catalog['poses'])} poses). Pointer flip only; no other object changes.")
    if not confirm:
        print("DRY RUN — re-run with --confirm to repoint.")
        return 0
    s3.put_object(
        Bucket=prod["bucket"], Key="latest.json",
        Body=json.dumps({"path": key, "catalog_version": version}).encode(),
        ContentType="application/json", CacheControl=LATEST_CACHE,
    )
    print(f"prod latest.json -> {key}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=("dev", "prod"), default="dev")
    parser.add_argument("--tf-dir", help="Override the Terraform directory to read outputs from")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually upload. Without this flag, dry-run only.")
    parser.add_argument("--promote", action="store_true",
                        help="Copy the catalog version currently live in dev to prod "
                             "(no rebuild). Prints the pose diff; needs --confirm.")
    parser.add_argument("--rollback-to", type=int, metavar="VERSION",
                        help="Repoint prod latest.json at an existing catalog version.")
    args = parser.parse_args()

    if args.promote:
        return promote(args.confirm, None, args.tf_dir)
    if args.rollback_to is not None:
        return rollback(args.rollback_to, args.confirm, args.tf_dir)

    config = resolve_config(args.env, args.tf_dir)
    endpoint = os.environ.get(
        "PROMPTED_S3_ENDPOINT",
        f"https://{config['account_id']}.r2.cloudflarestorage.com",
    )
    uploads, version = planned_uploads()
    if args.env == "prod":
        check_no_placeholders(json.loads((DIST_DIR / "catalog.json").read_text()), "prod")
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
