"""Upload the sample website to the configured S3-compatible bucket.

    python -m scripts.seed_s3                 # create bucket (versioned) and upload everything
    python -m scripts.seed_s3 --touch css/trails.css   # re-upload one file to simulate an update
    python -m scripts.seed_s3 --delete gear.html       # delete one object to simulate a deletion
"""

import argparse
import asyncio
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import aioboto3
from botocore.exceptions import ClientError

from app.core.config import get_settings

SITE_DIR = Path(__file__).resolve().parent.parent / "sample_site"


def _client(settings):
    return aioboto3.Session().client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
    )


async def ensure_bucket(client, bucket: str, versioning: bool) -> None:
    try:
        await client.head_bucket(Bucket=bucket)
    except ClientError:
        await client.create_bucket(Bucket=bucket)
    if versioning:
        await client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})


def touched_body(path: str) -> bytes:
    """The file with a harmless, unique change that keeps it valid for its type.

    The timestamp makes every touch produce a new ETag, so it is detected even
    when bucket versioning is off.
    """
    original = (SITE_DIR / path).read_bytes()
    stamp = datetime.now(timezone.utc).isoformat()
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".json":
        data = json.loads(original)
        data["_touched_at"] = stamp
        return json.dumps(data, indent=2).encode()
    if suffix in (".css", ".js"):
        return original + f"\n/* touched {stamp} */\n".encode()
    if suffix in (".html", ".htm", ".svg"):
        return original + f"\n<!-- touched {stamp} -->\n".encode()
    raise SystemExit(f"--touch supports text assets only (html, css, js, json, svg), not {path}")


async def upload(client, bucket: str, prefix: str, path: str, body: bytes | None = None) -> None:
    body = (SITE_DIR / path).read_bytes() if body is None else body
    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    await client.put_object(Bucket=bucket, Key=f"{prefix}{path}", Body=body, ContentType=content_type)
    print(f"uploaded s3://{bucket}/{prefix}{path}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--touch", metavar="PATH", help="re-upload a file with a harmless change")
    parser.add_argument("--delete", metavar="PATH", help="delete a file from the bucket")
    parser.add_argument("--no-versioning", action="store_true", help="leave bucket versioning off")
    args = parser.parse_args()

    settings = get_settings()
    prefix = settings.normalized_prefix
    async with _client(settings) as client:
        await ensure_bucket(client, settings.s3_bucket, versioning=not args.no_versioning)
        if args.touch:
            await upload(client, settings.s3_bucket, prefix, args.touch, touched_body(args.touch))
        elif args.delete:
            await client.delete_object(Bucket=settings.s3_bucket, Key=f"{prefix}{args.delete}")
            print(f"deleted s3://{settings.s3_bucket}/{prefix}{args.delete}")
        else:
            for file in sorted(SITE_DIR.rglob("*")):
                if file.is_file():
                    await upload(client, settings.s3_bucket, prefix, file.relative_to(SITE_DIR).as_posix())


if __name__ == "__main__":
    asyncio.run(main())
