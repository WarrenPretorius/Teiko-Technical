"""Parts 2-4: run every analysis against teiko.db and write the results to outputs/.

Usage:
    python run_analysis.py      (after python load_data.py)

Outputs:
    outputs/frequency_table.csv     Part 2 relative frequency table
    outputs/responder_stats.csv     Part 3 tests, pooled and per timepoint
    outputs/responder_boxplot.png   Part 3 boxplot
    outputs/baseline_samples.csv    Part 4.1 sample list
    outputs/baseline_summary.json   Part 4.2 counts
"""

import json
import sys

import matplotlib

matplotlib.use("Agg")  # render to file; no display needed
import matplotlib.pyplot as plt
import pandas as pd

from teiko import analysis
from teiko.config import ALPHA, OUTPUT_DIR, POPULATIONS
from teiko.db import DatabaseMissingError, connect

# Responder colours match the dashboard: Teiko coral vs a deep blue.
RESPONDER_COLOR = "#E5322D"
NON_RESPONDER_COLOR = "#2F5D9E"


def save_boxplot(cohort: pd.DataFrame, path) -> None:
    """Part 3 boxplot: one pair of boxes (responders / non-responders) per population."""
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    positions, data, colors = [], [], []
    for i, population in enumerate(POPULATIONS):
        subset = cohort[cohort["population"] == population]
        for offset, response, color in (
            (-0.2, "yes", RESPONDER_COLOR),
            (0.2, "no", NON_RESPONDER_COLOR),
        ):
            positions.append(i + offset)
            data.append(subset.loc[subset["response"] == response, "percentage"])
            colors.append(color)

    boxes = ax.boxplot(
        data,
        positions=positions,
        widths=0.32,
        patch_artist=True,
        medianprops={"color": "#111111", "linewidth": 1.5},
        flierprops={"marker": "o", "markersize": 2.5, "alpha": 0.4},
    )
    for patch, color in zip(boxes["boxes"], colors, strict=True):
        patch.set_facecolor(color)
        patch.set_alpha(0.85)
        patch.set_edgecolor(color)

    ax.set_xticks(range(len(POPULATIONS)), POPULATIONS.values())
    ax.set_ylabel("Relative frequency (% of total cells)")
    ax.set_title("Melanoma, miraclib, PBMC: responders vs non-responders", loc="left")
    ax.legend(
        [boxes["boxes"][0], boxes["boxes"][1]],
        ["Responders", "Non-responders"],
        frameon=False,
        loc="upper right",
    )
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#E6E4E0", linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def print_stats(title: str, table: pd.DataFrame) -> None:
    """Print a compact stats table to the console."""
    print(f"\n{title}")
    for row in table.itertuples():
        flag = "  *significant*" if row.significant else ""
        print(
            f"  {row.label:<11} median diff {row.median_difference:+6.2f} pp   "
            f"p = {row.p_value:.4f}   q = {row.q_value:.4f}{flag}"
        )


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    conn = connect()

    # Part 2
    frequencies = analysis.frequency_table(conn)
    frequencies.to_csv(OUTPUT_DIR / "frequency_table.csv", index=False)
    print(f"Part 2: {len(frequencies):,} rows -> outputs/frequency_table.csv")

    # Part 3: pooled, each timepoint, and the subject-averaged robustness check
    cohort = analysis.cohort_frequencies(conn)
    scopes = {"all": analysis.responder_comparison(cohort)}
    for day in sorted(cohort["timepoint"].unique()):
        scopes[f"day_{day}"] = analysis.responder_comparison(cohort, int(day))
    scopes["subject_average"] = analysis.subject_level_comparison(cohort)

    stats_table = pd.concat(
        [table.assign(scope=scope) for scope, table in scopes.items()], ignore_index=True
    )
    stats_table.to_csv(OUTPUT_DIR / "responder_stats.csv", index=False)
    save_boxplot(cohort, OUTPUT_DIR / "responder_boxplot.png")

    n_samples, n_subjects = cohort["sample"].nunique(), cohort["subject"].nunique()
    print(f"\nPart 3: {n_samples:,} samples from {n_subjects} subjects")
    print_stats("All timepoints (pooled):", scopes["all"])
    print_stats("Subject-averaged (robustness check):", scopes["subject_average"])
    significant = scopes["all"].loc[scopes["all"]["significant"], "label"].tolist()
    print(
        f"\n  Significant at q < {ALPHA}: {', '.join(significant) if significant else 'none'}"
        "\n  -> outputs/responder_stats.csv, outputs/responder_boxplot.png"
    )

    # Part 4
    baseline = analysis.baseline_samples(conn)
    baseline.to_csv(OUTPUT_DIR / "baseline_samples.csv", index=False)
    summary = analysis.baseline_summary(conn)
    (OUTPUT_DIR / "baseline_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    print(f"\nPart 4: {len(baseline)} baseline samples")
    for name, counts in summary.items():
        print(f"  {name.replace('_', ' ')}: {counts}")
    print("  -> outputs/baseline_samples.csv, outputs/baseline_summary.json")

    conn.close()


if __name__ == "__main__":
    try:
        main()
    except DatabaseMissingError as exc:
        sys.exit(str(exc))
