"""Part 1: build the SQLite database from cell-count.csv.

Usage:
    python load_data.py

Creates (or rebuilds) teiko.db in the repository root using the schema in
teiko/schema.sql, then loads every row of cell-count.csv into it.

The script deliberately uses only the Python standard library, so it runs
straight after cloning, before any dependencies are installed.
"""

import csv
import sqlite3
import sys
from pathlib import Path

from teiko.config import CSV_PATH, DB_PATH, POPULATIONS, SCHEMA_PATH

# Columns that describe the subject rather than an individual sample. These
# must be identical on every row for a given subject; see check_subject().
SUBJECT_FIELDS = ("project", "condition", "age", "sex", "treatment", "response")


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read the CSV into a list of dicts, failing early on a missing column."""
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        expected = {"subject", "sample", "sample_type", "time_from_treatment_start"}
        expected |= set(SUBJECT_FIELDS) | set(POPULATIONS)
        missing = expected - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{csv_path.name} is missing columns: {sorted(missing)}")
        return list(reader)


def check_subject(seen: dict[str, tuple], row: dict[str, str]) -> bool:
    """Record a subject's attributes and return True the first time it is seen.

    Raises if a later row for the same subject disagrees: that would mean the
    subject-level table is the wrong model for the data.
    """
    attrs = tuple(row[f] for f in SUBJECT_FIELDS)
    previous = seen.setdefault(row["subject"], attrs)
    if previous != attrs:
        raise ValueError(f"Subject {row['subject']} has inconsistent attributes across samples")
    return previous is attrs


def load(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> None:
    """Split each wide CSV row into the normalised tables."""
    conn.executemany(
        "INSERT INTO populations (population, label, sort_order) VALUES (?, ?, ?)",
        [(key, label, i) for i, (key, label) in enumerate(POPULATIONS.items())],
    )

    projects: set[str] = set()
    subjects, samples, counts = [], [], []
    seen: dict[str, tuple] = {}

    for row in rows:
        projects.add(row["project"])
        if check_subject(seen, row):
            subjects.append(
                (
                    row["subject"],
                    row["project"],
                    row["condition"],
                    int(row["age"]),
                    row["sex"],
                    row["treatment"],
                    row["response"] or None,  # blank for untreated controls
                )
            )
        samples.append(
            (
                row["sample"],
                row["subject"],
                row["sample_type"],
                int(row["time_from_treatment_start"]),
            )
        )
        counts.extend((row["sample"], pop, int(row[pop])) for pop in POPULATIONS)

    conn.executemany("INSERT INTO projects VALUES (?)", [(p,) for p in sorted(projects)])
    conn.executemany("INSERT INTO subjects VALUES (?, ?, ?, ?, ?, ?, ?)", subjects)
    conn.executemany("INSERT INTO samples VALUES (?, ?, ?, ?)", samples)
    conn.executemany("INSERT INTO cell_counts VALUES (?, ?, ?)", counts)


def main() -> None:
    rows = read_rows(CSV_PATH)

    # Build into a temporary file and swap it in at the end, so a failed load
    # never leaves a half-written database behind.
    tmp_path = DB_PATH.with_suffix(".db.tmp")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        with conn:  # single transaction
            load(conn, rows)
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("projects", "subjects", "samples", "cell_counts")
        }
    finally:
        conn.close()

    tmp_path.replace(DB_PATH)

    summary = ", ".join(f"{n:,} {table}" for table, n in counts.items())
    print(f"Created {DB_PATH.name}: {summary}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, sqlite3.Error) as exc:
        sys.exit(f"load_data.py failed: {exc}")
