"""Shared paths and constants.

Everything that more than one module needs to agree on (file locations, the
list of immune populations, the cohort definition used in Part 3) lives here,
so there is exactly one place to change it.
"""

from pathlib import Path

# Repository root: this file lives in <root>/teiko/config.py.
ROOT_DIR = Path(__file__).resolve().parent.parent

CSV_PATH = ROOT_DIR / "cell-count.csv"
DB_PATH = ROOT_DIR / "teiko.db"
SCHEMA_PATH = ROOT_DIR / "teiko" / "schema.sql"
OUTPUT_DIR = ROOT_DIR / "outputs"

# Immune populations in the order they appear in the CSV, with display labels.
# The order is also the fixed colour order used by every chart.
POPULATIONS: dict[str, str] = {
    "b_cell": "B cell",
    "cd8_t_cell": "CD8 T cell",
    "cd4_t_cell": "CD4 T cell",
    "nk_cell": "NK cell",
    "monocyte": "Monocyte",
}

# Part 3 / Part 4 cohort: melanoma patients on miraclib, PBMC samples only.
COHORT = {
    "condition": "melanoma",
    "treatment": "miraclib",
    "sample_type": "PBMC",
}

# Significance threshold applied to Benjamini-Hochberg adjusted q-values.
ALPHA = 0.05
