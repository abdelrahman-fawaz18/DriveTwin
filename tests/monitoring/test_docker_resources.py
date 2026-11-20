import http.server
import json
import socketserver
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.monitoring import docker_resources

PROJECT = "evdt-e2c-" + "a" * 12
IDENTITY = "b" * 64


@pytest.fixture
def engine(tmp_path):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.server.paths.append(self.path)
            status = self.server.status
            if self.path == "/version":
                value = self.server.versions
            elif self.path.startswith("/v1.56/containers/json?"):
                filters = json.loads(parse_qs(urlsplit(self.path).query)["filters"][0])
                assert filters == {
                    "label": [
                        "com.docker.compose.project=" + PROJECT,
                        "com.docker.compose.oneoff=False",
                    ]
                }
                value = [{"Id": IDENTITY, "Labels": self.server.labels}]
            elif self.path == "/v1.56/containers/" + IDENTITY + "/json":
                status = self.server.inspect_status or status
                value = {
                    "Id": self.server.inspected_id,
                    "State": {
                        "Running": True,
                        "Pid": 123,
                        "OOMKilled": False,
                        "ExitCode": 0,
                        "StartedAt": "start",
                    },
                    "RestartCount": 0,
                    "Config": {"Labels": self.server.labels},
                }
            else:
                raise AssertionError(self.path)
            payload = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

        def get_request(self):
            self.accepts += 1
            return super().get_request()

    path = tmp_path / "s"
    server = Server(str(path), Handler)
    server.paths = []
    server.accepts = 0
    server.status = 200
    server.inspect_status = None
    server.inspected_id = IDENTITY
    server.versions = {"ApiVersion": "1.56", "MinAPIVersion": "1.40"}
    server.labels = {
        "com.docker.compose.project": PROJECT,
        "com.docker.compose.oneoff": "False",
        "com.docker.compose.service": "source",
    }
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield server, path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_project_metadata_uses_one_read_only_persistent_local_connection(engine):
    server, path = engine
    client = docker_resources.DockerReadClient(PROJECT, path)
    try:
        for _ in range(2):
            ids = client.containers()
            assert ids == [IDENTITY]
            assert client.inspect(ids)[0]["State"]["Pid"] == 123
        assert client.inspect([]) == []
        assert client.api_version == "1.56" and server.accepts == 1 and len(server.paths) == 5
    finally:
        client.close()


def test_only_container_inspection_404_signals_replacement(engine):
    server, path = engine
    client = docker_resources.DockerReadClient(PROJECT, path)
    try:
        ids = client.containers()
        server.inspect_status = 404
        with pytest.raises(docker_resources.ContainerDisappeared):
            client.inspect(ids)
        server.status = 404
        with pytest.raises(ValueError, match="HTTP status 404") as error:
            client.containers()
        assert type(error.value) is ValueError
    finally:
        client.close()


@pytest.mark.parametrize(
    "failure", ["list_owner", "inspect_identity", "unknown_id", "http_error", "oversized"]
)
def test_metadata_errors_never_become_zero_or_unowned_resource_samples(
    engine, monkeypatch, failure
):
    server, path = engine
    client = docker_resources.DockerReadClient(PROJECT, path)
    try:
        if failure == "list_owner":
            server.labels["com.docker.compose.project"] = "other"
            with pytest.raises(ValueError, match="ownership"):
                client.containers()
        elif failure == "inspect_identity":
            client.containers()
            server.inspected_id = "c" * 64
            with pytest.raises(ValueError, match="identity"):
                client.inspect([IDENTITY])
        elif failure == "unknown_id":
            with pytest.raises(ValueError, match="unknown"):
                client.inspect([IDENTITY])
            assert len(server.paths) == 1
        elif failure == "http_error":
            server.status = 500
            with pytest.raises(ValueError, match="HTTP status"):
                client.containers()
        else:
            monkeypatch.setattr(docker_resources, "MAXIMUM_RESPONSE_BYTES", 16)
            with pytest.raises(ValueError, match="bound"):
                client.containers()
    finally:
        client.close()


@pytest.mark.parametrize(
    "versions",
    [
        {"ApiVersion": "bad", "MinAPIVersion": "1.40"},
        {"ApiVersion": "1.60", "MinAPIVersion": "1.59"},
    ],
)
def test_invalid_or_incompatible_docker_protocol_is_refused(engine, versions):
    server, path = engine
    server.versions = versions
    with pytest.raises(ValueError):
        docker_resources.DockerReadClient(PROJECT, path)
