#!/usr/bin/env python3
"""Verify the published catalog: latest.json resolves, the versioned catalog
it points at exists, and every image key the catalog references is present
in the bucket. Read-only; uses the same config resolution as publish.py."""
from __future__ import annotations

import argparse
import json
import os
import sys

from publish import resolve_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=("dev", "prod"), default="dev")
    parser.add_argument("--tf-dir")
    args = parser.parse_args()

    config = resolve_config(args.env, args.tf_dir)
    endpoint = os.environ.get(
        "PROMPTED_S3_ENDPOINT",
        f"https://{config['account_id']}.r2.cloudflarestorage.com",
    )
    import boto3
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="auto")
    bucket = config["bucket"]

    latest = json.loads(s3.get_object(Bucket=bucket, Key="latest.json")["Body"].read())
    print(f"latest.json -> {latest['path']} (catalog_version {latest['catalog_version']})")
    catalog = json.loads(s3.get_object(Bucket=bucket, Key=latest["path"])["Body"].read())
    print(f"catalog: {len(catalog['poses'])} poses, "
          f"schema_version {catalog['schema_version']}")

    known = set()
    token = {}
    while True:
        page = s3.list_objects_v2(Bucket=bucket, Prefix="poses/", **token)
        known.update(o["Key"] for o in page.get("Contents", []))
        if not page.get("IsTruncated"):
            break
        token = {"ContinuationToken": page["NextContinuationToken"]}

    missing = [k for p in catalog["poses"]
               for k in (p["image"]["thumb"], p["image"]["detail"])
               if k not in known]
    if missing:
        print(f"MISSING {len(missing)} referenced image keys, e.g. {missing[:5]}")
        return 1
    print(f"All {2 * len(catalog['poses'])} referenced image keys present "
          f"({len(known)} objects under poses/).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
