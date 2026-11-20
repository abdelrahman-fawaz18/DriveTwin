import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pytest

from evdt.monitoring import database, registration_client


def configure(monkeypatch, tmp_path, count=2):
    password = tmp_path / "credential"
    password.write_text("synthetic")
    monkeypatch.setenv("DB_USER", "evdt_read")
    monkeypatch.setenv("DB_PASSWORD_FILE", str(password))
    slots = threading.BoundedSemaphore(count)
    monkeypatch.setitem(database._slots, "evdt_read", slots)
    return slots


def test_concurrent_callers_never_open_more_connections_than_budget(monkeypatch, tmp_path):
    slots = configure(monkeypatch, tmp_path)
    active, maximum, exits = 0, 0, []
    lock = threading.Lock()

    class FakeDriver:
        def __init__(self):
            nonlocal active, maximum
            self.closed = False
            with lock:
                active += 1
                maximum = max(maximum, active)

        def __enter__(self):
            return self

        def __exit__(self, kind, value, trace):
            with lock:
                exits.append("rollback" if kind else "commit")

        def close(self):
            nonlocal active
            with lock:
                if not self.closed:
                    self.closed = True
                    active -= 1

    class FakeConnection(database.AdmissionLifecycle, FakeDriver):
        pass

    monkeypatch.setattr(
        database.BudgetConnection, "connect", staticmethod(lambda **options: FakeConnection())
    )

    def work(index):
        try:
            with database.connection():
                time.sleep(0.02)
                if index % 3 == 0:
                    raise RuntimeError("controlled transaction failure")
        except RuntimeError:
            pass

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(work, range(12)))
    assert maximum == 2 and active == 0
    assert exits.count("commit") == 8 and exits.count("rollback") == 4
    assert slots.acquire(blocking=False) and slots.acquire(blocking=False)
    assert not slots.acquire(blocking=False)
    slots.release()
    slots.release()


@pytest.mark.parametrize("fault", ["connect", "enter", "exit"])
def test_connection_and_close_failures_release_admission(monkeypatch, tmp_path, fault):
    slots = configure(monkeypatch, tmp_path, count=1)

    class FakeDriver:
        def __enter__(self):
            if fault == "enter":
                raise OSError("controlled entry failure")
            return self

        def __exit__(self, *args):
            raise OSError("controlled close failure")

        def close(self):
            if fault == "exit":
                raise OSError("controlled close failure")

    class FakeConnection(database.AdmissionLifecycle, FakeDriver):
        pass

    def connect(**options):
        if fault == "connect":
            raise OSError("controlled connect failure")
        return FakeConnection()

    monkeypatch.setattr(database.BudgetConnection, "connect", staticmethod(connect))
    with pytest.raises(OSError):
        with database.connection():
            pass
    assert slots.acquire(blocking=False)
    slots.release()


def test_admission_timeout_never_opens_a_database_socket(monkeypatch, tmp_path):
    slots = configure(monkeypatch, tmp_path, count=1)
    slots.acquire()
    monkeypatch.setattr(database, "WAIT_SECONDS", 0.01)

    def unexpected(**options):
        pytest.fail("A timed-out caller must not open a database connection")

    monkeypatch.setattr(database.BudgetConnection, "connect", staticmethod(unexpected))
    with pytest.raises(TimeoutError):
        with database.connection():
            pass
    slots.release()


def test_bare_connection_execute_commit_and_repeated_close_preserve_budget(monkeypatch, tmp_path):
    slots = configure(monkeypatch, tmp_path, count=1)
    actions = []

    class FakeDriver:
        def execute(self, query):
            actions.append(query)
            return self

        def commit(self):
            actions.append("commit")

        def close(self):
            actions.append("close")

    class FakeConnection(database.AdmissionLifecycle, FakeDriver):
        pass

    monkeypatch.setattr(
        database.BudgetConnection, "connect", staticmethod(lambda **options: FakeConnection())
    )
    conn = database.connection()
    assert not slots.acquire(blocking=False)
    assert conn.execute("SELECT 1") is conn
    conn.commit()
    conn.close()
    conn.close()
    assert actions == ["SELECT 1", "commit", "close", "close"]
    assert slots.acquire(blocking=False)
    slots.release()


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_metadata_errors_retry_identical_bytes(monkeypatch, tmp_path, status):
    credential = tmp_path / "credential"
    credential.write_text("synthetic")
    client = registration_client.RegistrationClient({}, credential)
    attempts, pauses, errors = [], [], []
    raw = b'{"controlled":"same original bytes"}'

    def request(value, timeout):
        attempts.append(value.data)
        if len(attempts) < 3:
            error = HTTPError(
                value.full_url, status, "controlled transient failure", {}, io.BytesIO()
            )
            errors.append(error)
            raise error
        return io.BytesIO(b'{"accepted":true}')

    monkeypatch.setattr(registration_client, "urlopen", request)
    monkeypatch.setattr(registration_client.time, "sleep", pauses.append)
    assert client.post("/v1/clock-events", raw) == {"accepted": True}
    assert attempts == [raw] * 3 and pauses == [0.2, 0.4]
    assert all(error.fp.closed for error in errors)


@pytest.mark.parametrize("status", [400, 401, 403, 409, 413, 422])
def test_metadata_semantic_rejection_is_never_retried(monkeypatch, tmp_path, status):
    credential = tmp_path / "credential"
    credential.write_text("synthetic")
    client = registration_client.RegistrationClient({}, credential)
    attempts = []

    def request(value, timeout):
        attempts.append(value.data)
        raise HTTPError(value.full_url, status, "controlled refusal", {}, io.BytesIO())

    monkeypatch.setattr(registration_client, "urlopen", request)
    with pytest.raises(RuntimeError, match="admission refused"):
        client.post("/v1/clock-events", b"original")
    assert len(attempts) == 1


def test_transient_metadata_retry_exhaustion_stops_source(monkeypatch, tmp_path):
    credential = tmp_path / "credential"
    credential.write_text("synthetic")
    client = registration_client.RegistrationClient({}, credential)
    attempts = []

    def request(value, timeout):
        attempts.append(value.data)
        raise HTTPError(value.full_url, 503, "controlled unavailability", {}, io.BytesIO())

    monkeypatch.setattr(registration_client, "urlopen", request)
    monkeypatch.setattr(registration_client.time, "sleep", lambda duration: None)
    with pytest.raises(RuntimeError, match="source must stop"):
        client.post("/v1/clock-events", b"original")
    assert len(attempts) == 3
