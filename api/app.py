"""FastAPI application: JSON endpoints for the dashboard, plus the static frontend.

All endpoints read from the database built by `make pipeline`. The data is
static while the server runs, so the heavier Part 3 / Part 4 results are
computed once and cached.
"""

from functools import lru_cache
from typing import Annotated, Literal

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from teiko import analysis
from teiko.config import ALPHA, COHORT, POPULATIONS, ROOT_DIR
from teiko.db import connect

WEB_DIR = ROOT_DIR / "web"

# No generated API docs: the dashboard is the only intended client.
app = FastAPI(title="Loblaw Bio Immune Profiling", docs_url=None, redoc_url=None, openapi_url=None)

# One read-only connection shared by all requests (see teiko.db.connect).
conn = connect()

Timepoint = Literal["all", "0", "7", "14"]


def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame to JSON-ready records, with NaN mapped to null."""
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


def csv_download(df: pd.DataFrame, filename: str) -> Response:
    """Serve a DataFrame as a CSV file attachment.

    The CSV is built in one go (at most ~3 MB) rather than streamed, which is
    far faster than letting the server iterate it line by line.
    """
    return Response(
        df.to_csv(index=False),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------
# Overview and Part 2
# --------------------------------------------------------------------------


@app.get("/api/overview")
@lru_cache
def overview() -> dict:
    """Headline counts and the distinct values used to build the filter menus."""

    def distinct(sql: str) -> list:
        return [row[0] for row in conn.execute(sql)]

    totals = conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM projects),
               (SELECT COUNT(*) FROM subjects),
               (SELECT COUNT(*) FROM samples),
               (SELECT SUM(count) FROM cell_counts)
        """
    ).fetchone()
    return {
        "totals": dict(zip(("projects", "subjects", "samples", "cells"), totals, strict=True)),
        "populations": [{"key": k, "label": v} for k, v in POPULATIONS.items()],
        "filters": {
            "project": distinct("SELECT project_id FROM projects ORDER BY 1"),
            "condition": distinct("SELECT DISTINCT condition FROM subjects ORDER BY 1"),
            "treatment": distinct("SELECT DISTINCT treatment FROM subjects ORDER BY 1"),
            "sex": distinct("SELECT DISTINCT sex FROM subjects ORDER BY 1"),
            "sample_type": distinct("SELECT DISTINCT sample_type FROM samples ORDER BY 1"),
            "timepoint": distinct(
                "SELECT DISTINCT time_from_treatment_start FROM samples ORDER BY 1"
            ),
        },
    }


def frequency_query(
    project: str | None = None,
    condition: str | None = None,
    treatment: str | None = None,
    response: str | None = None,
    sex: str | None = None,
    sample_type: str | None = None,
    timepoint: int | None = None,
    population: str | None = None,
    search: str = "",
    sort: str = "sample",
    desc: bool = False,
) -> analysis.FrequencyQuery:
    """Shared query parameters for the table and its CSV export."""
    if sort not in analysis.SORT_COLUMNS:
        raise HTTPException(422, f"sort must be one of {sorted(analysis.SORT_COLUMNS)}")
    filters = {
        "project": project,
        "condition": condition,
        "treatment": treatment,
        "response": response,
        "sex": sex,
        "sample_type": sample_type,
        "timepoint": timepoint,
        "population": population,
    }
    return analysis.FrequencyQuery(filters, search.strip(), sort, desc)


# Filters and sort shared by the table endpoint and its CSV export.
TableQuery = Annotated[analysis.FrequencyQuery, Depends(frequency_query)]


@app.get("/api/frequencies")
def frequencies(
    query: TableQuery,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    """One page of the Part 2 relative frequency table, plus a summary of all matching rows."""
    summary = analysis.frequency_summary(conn, query)
    rows = analysis.frequency_table(conn, query, limit=page_size, offset=(page - 1) * page_size)
    return {
        "total": summary["rows"],
        "summary": summary,
        "page": page,
        "page_size": page_size,
        "rows": records(rows),
    }


@app.get("/api/frequencies.csv")
def frequencies_csv(query: TableQuery) -> Response:
    """The full filtered Part 2 table as a CSV download."""
    return csv_download(analysis.frequency_table(conn, query), "cell_frequencies.csv")


@app.get("/api/samples/{sample_id}")
def sample(sample_id: str) -> dict:
    """Drill-down: one sample's metadata and its subject's profile over time."""
    detail = analysis.sample_detail(conn, sample_id)
    if detail is None:
        raise HTTPException(404, f"Sample {sample_id} not found")
    return {"sample": detail["sample"], "timeline": records(detail["timeline"])}


# --------------------------------------------------------------------------
# Part 3
# --------------------------------------------------------------------------


@lru_cache
def cohort() -> pd.DataFrame:
    return analysis.cohort_frequencies(conn)


@app.get("/api/responders")
@lru_cache
def responders(timepoint: Timepoint = "all") -> dict:
    """Boxplot values and test results for the chosen timepoint scope."""
    data = cohort() if timepoint == "all" else cohort().query(f"timepoint == {timepoint}")
    stats = analysis.responder_comparison(data)

    # Raw values grouped by population and response, for client-side boxplots.
    values = {
        population: {
            response: group["percentage"].round(3).tolist()
            for response, group in subset.groupby("response")
        }
        for population, subset in data.groupby("population")
    }
    return {
        "cohort": COHORT,
        "timepoint": timepoint,
        "alpha": ALPHA,
        "n_samples": int(data["sample"].nunique()),
        "n_subjects": int(data["subject"].nunique()),
        "values": values,
        "stats": records(stats),
        # Robustness check is always computed on the full cohort.
        "subject_average": records(analysis.subject_level_comparison(cohort())),
    }


@app.get("/api/trends")
@lru_cache
def trends() -> dict:
    """Mean frequency per population over time, by response group."""
    return {"rows": records(analysis.response_trends(cohort()))}


# --------------------------------------------------------------------------
# Part 4
# --------------------------------------------------------------------------


@app.get("/api/baseline")
@lru_cache
def baseline() -> dict:
    """Baseline subset: counts plus the exact SQL used to produce them."""
    samples = analysis.baseline_samples(conn)
    return {
        "n_samples": len(samples),
        "summary": analysis.baseline_summary(conn),
        "sql": {"samples": analysis.BASELINE_SAMPLES_SQL, **analysis.BASELINE_SUMMARY_SQL},
    }


@app.get("/api/baseline.csv")
def baseline_csv() -> Response:
    """Part 4.1 sample list as a CSV download."""
    return csv_download(analysis.baseline_samples(conn), "baseline_samples.csv")


# --------------------------------------------------------------------------
# Frontend
# --------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="static")
