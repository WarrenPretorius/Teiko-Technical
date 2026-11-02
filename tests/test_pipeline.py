"""Tests for the database load, the analyses and the dashboard server.

The database is built once into a temporary directory, so the tests never
touch the teiko.db in the repository root.
"""

import socket
import sqlite3

import pandas as pd
import pytest

import load_data
from serve import find_free_port
from teiko import analysis
from teiko.config import CSV_PATH, POPULATIONS, SCHEMA_PATH
from teiko.db import connect


@pytest.fixture(scope="session")
def conn(tmp_path_factory) -> sqlite3.Connection:
    """A freshly loaded database, shared across the test session."""
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    build = sqlite3.connect(db_path)
    build.executescript(SCHEMA_PATH.read_text())
    with build:
        load_data.load(build, load_data.read_rows(CSV_PATH))
    build.close()
    return connect(db_path)


# ---------- Part 1: loading ----------


def test_every_csv_row_is_loaded(conn):
    rows = load_data.read_rows(CSV_PATH)
    n_samples = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    n_counts = conn.execute("SELECT COUNT(*) FROM cell_counts").fetchone()[0]
    assert n_samples == len(rows)
    assert n_counts == len(rows) * len(POPULATIONS)


def test_counts_round_trip(conn):
    """A sample's counts in the database match the CSV exactly."""
    first = load_data.read_rows(CSV_PATH)[0]
    stored = dict(
        conn.execute(
            "SELECT population, count FROM cell_counts WHERE sample_id = ?", (first["sample"],)
        ).fetchall()
    )
    assert stored == {pop: int(first[pop]) for pop in POPULATIONS}


def test_inconsistent_subject_is_rejected():
    seen: dict = {}
    row = {"subject": "s1", "project": "p", "condition": "melanoma", "age": "50"}
    row |= {"sex": "M", "treatment": "miraclib", "response": "yes"}
    assert load_data.check_subject(seen, row) is True
    assert load_data.check_subject(seen, dict(row)) is False  # same subject again
    with pytest.raises(ValueError):
        load_data.check_subject(seen, row | {"response": "no"})


# ---------- Part 2: frequencies ----------


def test_frequency_table_shape(conn):
    table = analysis.frequency_table(conn)
    assert list(table.columns) == ["sample", "total_count", "population", "count", "percentage"]
    assert len(table) == 10_500 * len(POPULATIONS)


def test_percentages_sum_to_100(conn):
    table = analysis.frequency_table(conn)
    totals = table.groupby("sample")["percentage"].sum()
    assert totals.between(99.999, 100.001).all()


def test_filters_and_paging(conn):
    query = analysis.FrequencyQuery({"condition": "healthy", "response": "none"})
    summary = analysis.frequency_summary(conn, query)
    page = analysis.frequency_table(conn, query, limit=10, offset=0)
    assert summary["rows"] == 1_422 * len(POPULATIONS)
    assert summary["samples"] == 1_422
    assert len(page) == 10


def test_summary_mean_matches_pandas(conn):
    """The SQL mean agrees with the same subset computed from the raw CSV."""
    filters = {
        "condition": "melanoma",
        "sex": "M",
        "response": "yes",
        "timepoint": "0",
        "population": "b_cell",
    }
    summary = analysis.frequency_summary(conn, analysis.FrequencyQuery(filters))
    raw = pd.read_csv(CSV_PATH)
    subset = raw[
        (raw.condition == "melanoma")
        & (raw.sex == "M")
        & (raw.response == "yes")
        & (raw.time_from_treatment_start == 0)
    ]
    assert summary["samples"] == len(subset)
    assert summary["mean_count"] == pytest.approx(subset["b_cell"].mean())


# ---------- Part 3: responders vs non-responders ----------


def test_cohort_is_melanoma_miraclib_pbmc(conn):
    cohort = analysis.cohort_frequencies(conn)
    assert cohort["sample"].nunique() == 1_968
    assert set(cohort["response"]) == {"yes", "no"}


def test_stats_table(conn):
    stats = analysis.responder_comparison(analysis.cohort_frequencies(conn))
    assert list(stats["population"]) == list(POPULATIONS)
    assert (stats["q_value"] >= stats["p_value"]).all()  # BH never lowers a p-value
    assert stats["p_value"].between(0, 1).all()


# ---------- Part 4: baseline subset ----------


def test_baseline_summary(conn):
    summary = analysis.baseline_summary(conn)
    n_samples = len(analysis.baseline_samples(conn))
    assert sum(summary["samples_per_project"].values()) == n_samples
    # One baseline sample per subject, so subject counts add up to the same total.
    assert sum(summary["subjects_by_response"].values()) == n_samples
    assert sum(summary["subjects_by_sex"].values()) == n_samples


# ---------- Dashboard server ----------


def test_port_fallback_skips_busy_port():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        taken = busy.getsockname()[1]
        assert find_free_port("127.0.0.1", taken) != taken


def test_api_endpoints():
    """Smoke test against the real database built by `make pipeline`."""
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from api.app import app

    client = TestClient(app)
    assert client.get("/api/overview").json()["totals"]["samples"] == 10_500
    assert client.get("/api/frequencies?page_size=5").json()["total"] == 52_500
    assert client.get("/api/responders?timepoint=0").json()["n_subjects"] == 656
    assert client.get("/api/baseline").json()["n_samples"] == 656
    assert client.get("/api/responders?timepoint=3").status_code == 422
    assert client.get("/api/samples/nope").status_code == 404
