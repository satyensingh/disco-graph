from pathlib import Path
from types import SimpleNamespace

from app.storage import GraphArtifactStorage


class FakeMinio:
    def __init__(self):
        self.buckets = set()
        self.uploads = []

    def bucket_exists(self, bucket):
        return bucket in self.buckets

    def make_bucket(self, bucket):
        self.buckets.add(bucket)

    def fput_object(self, bucket, object_name, file_path, content_type=None):
        self.uploads.append((bucket, object_name, Path(file_path).read_bytes(), content_type))

    def put_object(self, bucket, object_name, data, length, content_type=None):
        self.uploads.append((bucket, object_name, data.read(length), content_type))

    def presigned_get_object(self, bucket, object_name, expires):
        return f"http://localhost:9000/{bucket}/{object_name}"


def test_graph_artifacts_use_disco_graph_prefix(tmp_path: Path):
    graph_file = tmp_path / "graph.json"
    html_file = tmp_path / "graph.html"
    graph_file.write_text('{"nodes":[]}', encoding="utf-8")
    html_file.write_text("<html></html>", encoding="utf-8")
    client = FakeMinio()
    config = SimpleNamespace(
        minio_endpoint="minio:9000",
        minio_public_endpoint="localhost:9000",
        minio_access_key="access",
        minio_secret_key="secret",
        minio_bucket="disco-graph-artifacts",
        minio_prefix="disco-graph",
        minio_region="us-east-1",
        minio_secure=False,
        minio_required=True,
        minio_timeout_seconds=5,
        minio_url_expiry_seconds=3600,
    )

    result = GraphArtifactStorage(config, client).sync(
        "repo-id",
        graph_file,
        html_file,
        {"nodes": 0, "edges": 0},
    )

    object_names = [upload[1] for upload in client.uploads]
    assert object_names == [
        "disco-graph/repo-id/graphify-out/graph.json",
        "disco-graph/repo-id/graphify-out/graph.html",
        "disco-graph/repo-id/manifest.json",
    ]
    assert result["status"] == "uploaded"
    assert result["graph_html_url"].startswith("http://localhost:9000/")
