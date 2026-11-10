#!/usr/bin/env python3
"""Content-addressed S3 artifact publisher with strict immutability checks.

No CloudFormation or Lambda changes. The caller must opt in with --upload.
Existing keys are accepted only when their SHA256 matches the local bytes.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path

def publish(s3, manifest, root, bucket, upload=False):
    results = []
    for name, item in sorted(manifest["artifacts"].items()):
        source = root / item["local_file"]
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != item["sha256_hex"] or len(data) != item["bytes"]:
            raise ValueError(f"{name}: manifest integrity mismatch")
        key = item["s3_key"]
        if not key.startswith(f"releases/sha256/{digest}/") or not key.endswith(f"/{name}.zip"):
            raise ValueError(f"{name}: invalid immutable object key")
        if not upload:
            results.append({"name": name, "key": key, "status": "LOCAL_VERIFIED"})
            continue
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
        except s3.exceptions.NoSuchKey:
            obj = None
        else:
            remote_hash = hashlib.sha256()
            body = obj["Body"]
            try:
                for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                    remote_hash.update(chunk)
            finally:
                body.close()
            if remote_hash.hexdigest() != digest:
                raise ValueError(f"{name}: existing S3 object has different bytes; refusing overwrite")
        if obj is None:
            s3.put_object(Bucket=bucket, Key=key, Body=data,
                          ContentType="application/zip",
                          Metadata={"sha256": digest},
                          IfNoneMatch="*")
            # Verify the actual object, not just ETag (multipart and encryption differ).
            remote = s3.get_object(Bucket=bucket, Key=key)
            remote_digest = hashlib.sha256()
            body = remote["Body"]
            try:
                for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                    remote_digest.update(chunk)
            finally:
                body.close()
            if remote_digest.hexdigest() != digest:
                raise RuntimeError(f"{name}: uploaded object verification failed")
        results.append({"name": name, "key": key,
                        "status": "EXISTING_VERIFIED" if obj else "UPLOADED_VERIFIED"})
    return results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--bucket")
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    if args.upload and not args.bucket:
        parser.error("--bucket required with --upload")
    manifest = json.loads(args.manifest.read_text())
    if manifest.get("format") != 1:
        raise ValueError("Unsupported manifest format")
    s3 = None
    if args.upload:
        import boto3
        s3 = boto3.client("s3", region_name="ap-southeast-2")
    for entry in publish(s3, manifest, args.root, args.bucket, args.upload):
        print(json.dumps(entry, sort_keys=True))
    print("NO CloudFormation or Lambda deployment performed.")
if __name__ == "__main__":
    main()
