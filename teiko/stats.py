"""Statistical tests for comparing responders with non-responders.

Method
------
For each immune population we compare relative frequencies between the two
response groups with a two-sided Mann-Whitney U test. It is rank-based, so it
makes no assumption that percentages are normally distributed and is robust to
outliers.

Five populations are tested at once, which inflates the chance of a false
positive. P-values are therefore adjusted with the Benjamini-Hochberg
procedure, and a population is only called significant when its adjusted
q-value is below ALPHA.
"""

import pandas as pd
from scipy import stats

from teiko.config import ALPHA, POPULATIONS


def compare_groups(
    df: pd.DataFrame,
    value: str = "percentage",
    group: str = "response",
    positive: str = "yes",
    negative: str = "no",
) -> pd.DataFrame:
    """Run a Mann-Whitney U test per population, with BH correction.

    Args:
        df: Long-format data with `population`, `group` and `value` columns.
        value: Column to compare.
        group: Column holding the group label.
        positive, negative: The two group labels to compare.

    Returns:
        One row per population (in canonical order) with group sizes, medians,
        the median difference (positive minus negative), raw p, BH q and a
        significance flag.
    """
    results = []
    for population in POPULATIONS:
        subset = df[df["population"] == population]
        pos = subset.loc[subset[group] == positive, value]
        neg = subset.loc[subset[group] == negative, value]
        test = stats.mannwhitneyu(pos, neg, alternative="two-sided")
        results.append(
            {
                "population": population,
                "label": POPULATIONS[population],
                "n_responders": len(pos),
                "n_non_responders": len(neg),
                "median_responders": pos.median(),
                "median_non_responders": neg.median(),
                "median_difference": pos.median() - neg.median(),
                "u_statistic": test.statistic,
                "p_value": test.pvalue,
            }
        )

    table = pd.DataFrame(results)
    table["q_value"] = stats.false_discovery_control(table["p_value"], method="bh")
    table["significant"] = table["q_value"] < ALPHA
    return table
