"""Exercise the actual internal HTTP boundary, including failure responses."""

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from evdt.monitoring.metrics import start


def test_metrics_authentication_identity_and_failed_provider(monkeypatch, tmp_path):
    credential = tmp_path / "metrics"
    credential.write_text("test-only-metrics-credential")
    monkeypatch.setenv("METRICS_TOKEN_FILE", str(credential))
    monkeypatch.setenv("EVDT_RUN_ID", "00000000-0000-0000-0000-000000000001")
    state = {"fail": False}

    def snapshot():
        if state["fail"]:
            raise RuntimeError("Controlled provider failure")
        return {"broker_connected": False}

    server = start(snapshot)
    try:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            with pytest.raises(HTTPError) as error:
                urlopen(Request("http://127.0.0.1:9100/metrics", headers=headers), timeout=2)
            assert error.value.code == 401
        request = Request(
            "http://127.0.0.1:9100/metrics",
            headers={"Authorization": "Bearer " + credential.read_text()},
        )
        with urlopen(request, timeout=2) as response:
            body = json.load(response)
            assert body["run_id"] == "00000000-0000-0000-0000-000000000001"
            assert body["broker_connected"] is False
            assert response.headers["Cache-Control"] == "no-store"
        state["fail"] = True
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=2)
        assert error.value.code == 503
    finally:
        server.shutdown()
        server.server_close()
