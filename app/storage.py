from __future__ import annotations

import io
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from minio import Minio
import urllib3

from .config import Settings, settings


class GraphArtifactStorage:
    """Store the current graph artifacts in an S3-compatible MinIO bucket."""

    def __init__(self, config: Settings | None = None, client: Any | None = None) -> None:
        config = config or settings
        self.endpoint = config.minio_endpoint.strip()
        self.bucket = config.minio_bucket
        self.prefix = config.minio_prefix.strip("/") or "disco-graph"
        self.public_endpoint = config.minio_public_endpoint.strip()
        self.region = getattr(config, "minio_region", "us-east-1") or None
        self.required = config.minio_required
        self.timeout_seconds = max(1, config.minio_timeout_seconds)
        self.url_expiry = max(60, config.minio_url_expiry_seconds)
        self.enabled = bool(self.endpoint and config.minio_access_key and config.minio_secret_key)
        self.client = (
            client
            if client is not None
            else Minio(
                self.endpoint,
                access_key=config.minio_access_key,
                secret_key=config.minio_secret_key,
                secure=config.minio_secure,
                region=self.region,
                http_client=self._http_client(),
            )
            if self.enabled
            else None
        )
        self.signing_client = self.client
        if self.enabled and self.public_endpoint and client is None:
            self.signing_client = Minio(
                self.public_endpoint,
                access_key=config.minio_access_key,
                secret_key=config.minio_secret_key,
                secure=config.minio_secure,
                region=self.region,
                http_client=self._http_client(),
            )

    def _http_client(self) -> urllib3.PoolManager:
        return urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=min(2, self.timeout_seconds), read=self.timeout_seconds),
            retries=False,
        )

    def _object_name(self, repo_id: str, filename: str) -> str:
        return f"{self.prefix}/{repo_id}/graphify-out/{filename}"

    def _manifest_name(self, repo_id: str) -> str:
        return f"{self.prefix}/{repo_id}/manifest.json"

    def _ensure_bucket(self) -> None:
        if self.client is None:
            raise RuntimeError("MinIO storage is not configured")
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def _presigned_url(self, object_name: str) -> str | None:
        if not self.enabled or self.signing_client is None:
            return None
        return str(
            self.signing_client.presigned_get_object(
                self.bucket,
                object_name,
                expires=timedelta(seconds=self.url_expiry),
            )
        )

    def sync(
        self,
        repo_id: str,
        graph_file: Path,
        html_file: Path,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "status": "disabled"}

        graph_object = self._object_name(repo_id, "graph.json")
        html_object = self._object_name(repo_id, "graph.html")
        manifest_object = self._manifest_name(repo_id)
        try:
            self._ensure_bucket()
            self.client.fput_object(
                self.bucket,
                graph_object,
                str(graph_file),
                content_type="application/json",
            )
            self.client.fput_object(
                self.bucket,
                html_object,
                str(html_file),
                content_type="text/html; charset=utf-8",
            )
            manifest = {
                "repo_id": repo_id,
                "prefix": f"{self.prefix}/{repo_id}/graphify-out",
                "graph_object": graph_object,
                "html_object": html_object,
                "summary": summary,
                "modified_epoch": graph_file.stat().st_mtime,
            }
            encoded = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
            self.client.put_object(
                self.bucket,
                manifest_object,
                io.BytesIO(encoded),
                len(encoded),
                content_type="application/json",
            )
            return {
                "enabled": True,
                "status": "uploaded",
                "provider": "minio",
                "bucket": self.bucket,
                "prefix": manifest["prefix"],
                "objects": {
                    "graph": graph_object,
                    "html": html_object,
                    "manifest": manifest_object,
                },
                "graph_html_url": self._presigned_url(html_object),
            }
        except Exception as exc:
            if self.required:
                raise RuntimeError(f"MinIO graph artifact upload failed: {exc}") from exc
            return {
                "enabled": True,
                "status": "error",
                "provider": "minio",
                "bucket": self.bucket,
                "prefix": f"{self.prefix}/{repo_id}/graphify-out",
                "error": str(exc),
            }

    def restore(self, repo_id: str, graph_file: Path, html_file: Path) -> bool:
        if not self.enabled or self.client is None:
            return False
        try:
            self._ensure_bucket()
            graph_file.parent.mkdir(parents=True, exist_ok=True)
            self.client.fget_object(
                self.bucket,
                self._object_name(repo_id, "graph.json"),
                str(graph_file),
            )
            if not html_file.exists():
                html_file.parent.mkdir(parents=True, exist_ok=True)
                self.client.fget_object(
                    self.bucket,
                    self._object_name(repo_id, "graph.html"),
                    str(html_file),
                )
            return graph_file.exists()
        except Exception:
            return False

    def describe(self, repo_id: str) -> dict[str, Any]:
        if not self.enabled or self.client is None:
            return {"enabled": False, "status": "disabled"}
        manifest_object = self._manifest_name(repo_id)
        try:
            self.client.stat_object(self.bucket, manifest_object)
            graph_object = self._object_name(repo_id, "graph.json")
            html_object = self._object_name(repo_id, "graph.html")
            return {
                "enabled": True,
                "status": "available",
                "provider": "minio",
                "bucket": self.bucket,
                "prefix": f"{self.prefix}/{repo_id}/graphify-out",
                "objects": {
                    "graph": graph_object,
                    "html": html_object,
                    "manifest": manifest_object,
                },
                "graph_html_url": self._presigned_url(html_object),
            }
        except Exception as exc:
            return {
                "enabled": True,
                "status": "unavailable",
                "provider": "minio",
                "bucket": self.bucket,
                "prefix": f"{self.prefix}/{repo_id}/graphify-out",
                "error": str(exc),
            }


def get_graph_artifact_storage() -> GraphArtifactStorage:
    return GraphArtifactStorage()
