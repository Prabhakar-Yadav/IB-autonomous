"""Shared connections for workers and the extraction engine."""
import os

import psycopg
from minio import Minio

BUCKET = "raw-documents"


def pg_connect(**kwargs):
    return psycopg.connect(
        host=os.environ.get("PGHOST", "postgres"),
        dbname=os.environ.get("PGDATABASE", "enterprise_intel"),
        user=os.environ.get("PGUSER", "vaultstack"),
        password=os.environ["PGPASSWORD"],
        **kwargs,
    )


def minio_client() -> Minio:
    return Minio(
        os.environ.get("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.environ.get("MINIO_ACCESS_KEY", "vaultstack"),
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )
