"""Bound database concurrency before opening a socket; retain transaction semantics."""

import os
import threading
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

# Single-process local services share the existing 20-slot PostgreSQL instance.
# Keep application admission below 17 ordinary slots, including test helpers.
ROLE_LIMITS = {
    "evdt_read": 4,
    "evdt_registry": 4,
    "evdt_ingest": 2,
    "evdt_replay": 2,
    "evdt_admin": 2,
    "evdt_forecast": 1,
}
_slots = {role: threading.BoundedSemaphore(limit) for role, limit in ROLE_LIMITS.items()}
WAIT_SECONDS = 5


class AdmissionTicket:
    def __init__(self, slots):
        self.slots = slots
        self.lock = threading.Lock()
        self.released = False

    def release(self):
        with self.lock:
            if not self.released:
                self.released = True
                self.slots.release()


class AdmissionLifecycle:
    """Return admission once, for explicit close and driver context cleanup."""

    def close(self):
        try:
            return super().close()
        finally:
            ticket = getattr(self, "_admission_ticket", None)
            if ticket is not None:
                ticket.release()

    def __enter__(self):
        try:
            return super().__enter__()
        except BaseException:
            self.close()
            raise

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class BudgetConnection(AdmissionLifecycle, psycopg.Connection):
    """A real Psycopg connection with bounded, close-bound admission."""


def connection():
    role = os.environ["DB_USER"]
    if role not in _slots:
        raise ValueError("Database role has no declared connection budget")
    slots = _slots[role]
    if not slots.acquire(timeout=WAIT_SECONDS):
        raise TimeoutError("Database connection admission deadline exceeded")
    ticket = AdmissionTicket(slots)
    try:
        conn = BudgetConnection.connect(
            host=os.environ.get("DB_HOST", "db"),
            dbname="evdt",
            user=role,
            password=Path(os.environ["DB_PASSWORD_FILE"]).read_text().strip(),
            connect_timeout=5,
            row_factory=dict_row,
        )
        conn._admission_ticket = ticket
        return conn
    except BaseException:
        ticket.release()
        raise
