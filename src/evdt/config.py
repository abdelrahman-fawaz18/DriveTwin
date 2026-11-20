import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def db_options():
    return {
        "host": os.environ.get("DB_HOST", "db"),
        "dbname": "evdt",
        "user": "evdt",
        "password": Path(os.environ["DB_PASSWORD_FILE"]).read_text().strip(),
        "connect_timeout": 5,
    }


def run_id():
    return os.environ["EVDT_RUN_ID"]
