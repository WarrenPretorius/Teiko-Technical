"""Analyses for Parts 2-4, shared by the pipeline (run_analysis.py) and the API.

Each function takes an open database connection and returns plain pandas or
Python objects, so the same code produces the files in outputs/ and the data
behind the dashboard.
"""

import sqlite3
from dataclasses import dataclass, field

import pandas as pd

from teiko.config import COHORT
from teiko.stats import compare_groups

# --------------------------------------------------------------------------
# Part 2: relative frequency table
# --------------------------------------------------------------------------

# The Part 2 view joined to sample and subject metadata, so the table can be
# filtered. Ordered by population sort_order so rows read in panel order.
_FREQUENCY_BASE = """
    FROM sample_frequencies AS f
    JOIN samples     AS sa ON sa.sample_id  = f.sample
    JOIN subjects    AS su ON su.subject_id = sa.subject_id
    JOIN populations AS p  ON p.population  = f.population
"""

# Filterable columns, mapped from API parameter name to SQL column. Only
# names in this whitelist ever reach the SQL string; values are always bound.
FILTER_COLUMNS = {
    "project": "su.project_id",
    "condition": "su.condition",
    "treatment": "su.treatment",
    "response": "su.response",
    "sex": "su.sex",
    "sample_type": "sa.sample_type",
    "timepoint": "sa.time_from_treatment_start",
    "population": "f.population",
}

# Sortable columns of the Part 2 table.
SORT_COLUMNS = {
    "sample": "f.sample",
    "total_count": "f.total_count",
    "population": "p.sort_order",
    "count": "f.count",
    "percentage": "f.percentage",
}


@dataclass
class FrequencyQuery:
    """Filters, search and sort for the Part 2 table."""

    filters: dict[str, str] = field(default_factory=dict)
    search: str = ""  # prefix match on sample or subject id
    sort: str = "sample"
    descending: bool = False

    def where(self) -> tuple[str, list]:
        """Build the WHERE clause and its bound parameters."""
        clauses, params = [], []
        for name, value in self.filters.items():
            if value in (None, ""):
                continue
            column = FILTER_COLUMNS[name]  # KeyError on unknown filter is intended
            if name == "response" and value == "none":
                clauses.append(f"{column} IS NULL")
                continue
            clauses.append(f"{column} = ?")
            params.append(int(value) if name == "timepoint" else value)
        if self.search:
            clauses.append("(f.sample LIKE ? OR sa.subject_id LIKE ?)")
            params += [f"{self.search}%"] * 2
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def order_by(self) -> str:
        direction = "DESC" if self.descending else "ASC"
        column = SORT_COLUMNS.get(self.sort, SORT_COLUMNS["sample"])
        # Secondary keys keep paging stable when the primary key has ties.
        return f" ORDER BY {column} {direction}, f.sample, p.sort_order"


def frequency_table(
    conn: sqlite3.Connection,
    query: FrequencyQuery | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> pd.DataFrame:
    """Return the Part 2 table: sample, total_count, population, count, percentage."""
    query = query or FrequencyQuery()
    where, params = query.where()
    sql = (
        "SELECT f.sample, f.total_count, f.population, f.count, f.percentage"
        + _FREQUENCY_BASE
        + where
        + query.order_by()
    )
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [*params, limit, offset]
    return pd.read_sql_query(sql, conn, params=params)


def frequency_summary(conn: sqlite3.Connection, query: FrequencyQuery) -> dict:
    """Row and sample counts for a query, plus the mean count and percentage.

    The row count drives pagination. The means answer questions such as "what
    is the average B cell count in this subset?" without exporting the table.
    """
    where, params = query.where()
    rows, samples, mean_count, mean_percentage = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT f.sample), AVG(f.count), AVG(f.percentage)"
        + _FREQUENCY_BASE
        + where,
        params,
    ).fetchone()
    return {
        "rows": rows,
        "samples": samples,
        "mean_count": mean_count,
        "mean_percentage": mean_percentage,
    }


def sample_detail(conn: sqlite3.Connection, sample_id: str) -> dict | None:
    """Metadata for one sample plus the composition of all its subject's samples.

    Used by the dashboard drill-down to show how a subject's immune profile
    changes over the course of treatment.
    """
    meta = conn.execute(
        """
        SELECT sa.sample_id, sa.subject_id, sa.sample_type, sa.time_from_treatment_start,
               su.project_id, su.condition, su.age, su.sex, su.treatment, su.response
        FROM samples AS sa JOIN subjects AS su ON su.subject_id = sa.subject_id
        WHERE sa.sample_id = ?
        """,
        (sample_id,),
    ).fetchone()
    if meta is None:
        return None

    timeline = pd.read_sql_query(
        """
        SELECT f.sample, sa.time_from_treatment_start AS timepoint, f.population,
               f.count, f.percentage, f.total_count
        FROM sample_frequencies AS f
        JOIN samples     AS sa ON sa.sample_id = f.sample
        JOIN populations AS p  ON p.population = f.population
        WHERE sa.subject_id = ?
        ORDER BY sa.time_from_treatment_start, p.sort_order
        """,
        conn,
        params=(meta["subject_id"],),
    )
    return {"sample": dict(meta), "timeline": timeline}


# --------------------------------------------------------------------------
# Part 3: responders vs non-responders
# --------------------------------------------------------------------------

_COHORT_SQL = """
    SELECT f.sample, sa.subject_id AS subject, su.response,
           sa.time_from_treatment_start AS timepoint,
           f.population, f.percentage
    FROM sample_frequencies AS f
    JOIN samples     AS sa ON sa.sample_id  = f.sample
    JOIN subjects    AS su ON su.subject_id = sa.subject_id
    JOIN populations AS p  ON p.population  = f.population
    WHERE su.condition = :condition
      AND su.treatment = :treatment
      AND sa.sample_type = :sample_type
      AND su.response IS NOT NULL
    ORDER BY f.sample, p.sort_order
"""


def cohort_frequencies(conn: sqlite3.Connection) -> pd.DataFrame:
    """Relative frequencies for the Part 3 cohort (melanoma, miraclib, PBMC)."""
    return pd.read_sql_query(_COHORT_SQL, conn, params=COHORT)


def responder_comparison(cohort: pd.DataFrame, timepoint: int | None = None) -> pd.DataFrame:
    """Responder vs non-responder tests, on all samples or a single timepoint.

    Pooling all timepoints (the default) is the comparison the brief asks for,
    but counts each subject up to three times. Restricting to one timepoint
    gives exactly one sample per subject.
    """
    data = cohort if timepoint is None else cohort[cohort["timepoint"] == timepoint]
    return compare_groups(data)


def subject_level_comparison(cohort: pd.DataFrame) -> pd.DataFrame:
    """Robustness check: average each subject's samples, then compare.

    This gives one independent observation per subject, removing the
    repeated-measures issue in the pooled test.
    """
    per_subject = cohort.groupby(["subject", "response", "population"], as_index=False)[
        "percentage"
    ].mean()
    return compare_groups(per_subject)


def response_trends(cohort: pd.DataFrame) -> pd.DataFrame:
    """Mean frequency (with standard error) per population, timepoint and response."""
    return (
        cohort.groupby(["population", "timepoint", "response"])["percentage"]
        .agg(mean="mean", sem="sem", n="count")
        .reset_index()
    )


# --------------------------------------------------------------------------
# Part 4: baseline subset
# --------------------------------------------------------------------------

# Kept as named constants so the dashboard can show the exact SQL it ran.
BASELINE_SAMPLES_SQL = """\
SELECT sa.sample_id, sa.subject_id, su.project_id, su.response, su.sex, su.age
FROM samples  AS sa
JOIN subjects AS su ON su.subject_id = sa.subject_id
WHERE su.condition = 'melanoma'
  AND su.treatment = 'miraclib'
  AND sa.sample_type = 'PBMC'
  AND sa.time_from_treatment_start = 0
ORDER BY sa.sample_id;"""

BASELINE_SUMMARY_SQL = {
    "samples_per_project": """\
SELECT su.project_id AS label, COUNT(*) AS n
FROM samples  AS sa
JOIN subjects AS su ON su.subject_id = sa.subject_id
WHERE su.condition = 'melanoma' AND su.treatment = 'miraclib'
  AND sa.sample_type = 'PBMC' AND sa.time_from_treatment_start = 0
GROUP BY su.project_id
ORDER BY su.project_id;""",
    "subjects_by_response": """\
SELECT su.response AS label, COUNT(DISTINCT su.subject_id) AS n
FROM samples  AS sa
JOIN subjects AS su ON su.subject_id = sa.subject_id
WHERE su.condition = 'melanoma' AND su.treatment = 'miraclib'
  AND sa.sample_type = 'PBMC' AND sa.time_from_treatment_start = 0
GROUP BY su.response
ORDER BY su.response DESC;""",
    "subjects_by_sex": """\
SELECT su.sex AS label, COUNT(DISTINCT su.subject_id) AS n
FROM samples  AS sa
JOIN subjects AS su ON su.subject_id = sa.subject_id
WHERE su.condition = 'melanoma' AND su.treatment = 'miraclib'
  AND sa.sample_type = 'PBMC' AND sa.time_from_treatment_start = 0
GROUP BY su.sex
ORDER BY su.sex;""",
}


def baseline_samples(conn: sqlite3.Connection) -> pd.DataFrame:
    """Part 4.1: melanoma PBMC baseline samples from miraclib-treated patients."""
    return pd.read_sql_query(BASELINE_SAMPLES_SQL, conn)


def baseline_summary(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Part 4.2: counts by project, by response and by sex, among those samples."""
    return {
        name: {row["label"]: row["n"] for row in conn.execute(sql)}
        for name, sql in BASELINE_SUMMARY_SQL.items()
    }
