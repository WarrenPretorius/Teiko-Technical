"""Database connection helpers."""

import sqlite3
from pathlib import Path

from teiko.config import DB_PATH


class DatabaseMissingError(FileNotFoundError):
    """Raised when the database has not been built yet."""


def connect(db_path: Path = DB_PATH, read_only: bool = True) -> sqlite3.Connection:
    """Open the analysis database.

    Analysis code only ever reads, so connections default to read-only. That
    also means a typo in a query can never modify the data.
    """
    if not db_path.exists():
        raise DatabaseMissingError(f"{db_path.name} not found. Run `make pipeline` first.")

    mode = "ro" if read_only else "rw"
    # check_same_thread=False lets the web server share a connection across
    # its worker threads; safe here because the connection is read-only.
    conn = sqlite3.connect(f"file:{db_path}?mode={mode}", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
