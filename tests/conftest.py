import socket
from pathlib import Path

import boto3
import pytest
import requests
from moto.server import ThreadedMotoServer

SITE_DIR = Path(__file__).resolve().parent.parent / "sample_site"
BUCKET = "test-site"
PREFIX = "site/"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def s3_endpoint():
    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
def s3(s3_endpoint, monkeypatch):
    """A fresh, versioned bucket seeded with the sample website."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    client = boto3.client(
        "s3",
        endpoint_url=s3_endpoint,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    requests.post(f"{s3_endpoint}/moto-api/reset", timeout=10)
    client.create_bucket(Bucket=BUCKET)
    client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
    for file in sorted(SITE_DIR.rglob("*")):
        if file.is_file():
            client.put_object(Bucket=BUCKET, Key=PREFIX + file.relative_to(SITE_DIR).as_posix(), Body=file.read_bytes())
    return client


@pytest.fixture
def settings(s3_endpoint, tmp_path):
    from app.core.config import Settings

    return Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        s3_endpoint_url=s3_endpoint,
        s3_access_key_id="testing",
        s3_secret_access_key="testing",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        mirror_dir=tmp_path / "mirror",
        log_json=False,
        log_level="WARNING",
    )
