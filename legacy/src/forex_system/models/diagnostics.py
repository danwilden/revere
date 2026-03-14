"""
Signal quality diagnostics for OOS feature evaluation.

Use these after walk-forward training to answer:
  - Which features have predictive power that survives the train/test split?
  - Which features are dead (IC near zero)?
  - Which features hurt OOS performance when permuted?

Workflow:
    from forex_system.models.diagnostics import feature_oos_ic, permutation_importance_oos

    ic_df = feature_oos_ic(features_df, label_col="label_triple_barrier",
                           feature_cols=ML_FEATURE_COLS)
    print(ic_df[ic_df["dead"]])   # features to consider dropping

    perm_df = permutation_importance_oos(fold_model, X_test, y_test)
"""

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.inspection import permutation_importance as _sk_perm_importance


def information_coefficient(
    y_pred_proba: np.ndarray, y_true: np.ndarray
) -> float:
    """
    Spearman IC between predicted probabilities and binary outcomes.

    A model with zero edge produces IC ≈ 0.  Practically useful IC is > 0.02.
    Returns NaN if the correlation cannot be computed (e.g. constant inputs).
    """
    corr, _ = spearmanr(y_pred_proba, y_true)
    return float(corr)


def feature_oos_ic(
    features_df: pd.DataFrame,
    label_col: str,
    feature_cols: list[str],
    n_splits: int = 5,
    purge_bars: int = 60,
    embargo_bars: int = 10,
) -> pd.DataFrame:
    """
    Feature-by-feature OOS Information Coefficient using walk-forward splits.

    Replicates the same walk-forward split structure as WalkForwardTrainer so
    the IC is measured on genuine OOS windows (no look-ahead).

    For each OOS fold, computes the Spearman rank correlation between the raw
    feature values and the label.  Aggregates across folds to get mean IC,
    std IC, and a t-statistic for significance.

    Args:
        features_df: DataFrame containing both feature and label columns.
        label_col:   Name of the label column (e.g. "label_triple_barrier").
        feature_cols: List of feature column names to evaluate.
        n_splits:    Number of OOS folds (same as WalkForwardTrainer).
        purge_bars:  Bars purged from training tail (same as WalkForwardTrainer).
        embargo_bars: Additional buffer after purge.

    Returns:
        DataFrame sorted by |mean_ic| descending with columns:
          feature   | mean_ic | std_ic | t_stat | n_folds | dead
        dead = True if |mean_ic| < 0.02 (no actionable signal).
    """
    required = feature_cols + [label_col]
    clean = features_df.dropna(subset=required).copy()
    n = len(clean)
    if n == 0:
        return pd.DataFrame(
            columns=["feature", "mean_ic", "std_ic", "t_stat", "n_folds", "dead"]
        )

    fold_size = n // (n_splits + 1)
    ics: dict[str, list[float]] = {f: [] for f in feature_cols}

    for i in range(n_splits):
        test_start = (i + 1) * fold_size + embargo_bars
        test_end = min((i + 2) * fold_size, n)
        if test_start >= test_end:
            continue

        test = clean.iloc[test_start:test_end]
        y = test[label_col].values

        for feat in feature_cols:
            x = test[feat].values
            # Skip if feature is constant on this fold (IC is undefined)
            if np.nanstd(x) == 0:
                continue
            corr, _ = spearmanr(x, y, nan_policy="omit")
            if not np.isnan(corr):
                ics[feat].append(corr)

    rows = []
    for feat, vals in ics.items():
        if not vals:
            rows.append(
                {"feature": feat, "mean_ic": 0.0, "std_ic": 0.0,
                 "t_stat": 0.0, "n_folds": 0, "dead": True}
            )
            continue
        mean_ic = float(np.mean(vals))
        std_ic = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
        t_stat = (
            mean_ic / (std_ic / np.sqrt(len(vals)))
            if std_ic > 0 else 0.0
        )
        rows.append(
            {
                "feature": feat,
                "mean_ic": mean_ic,
                "std_ic": std_ic,
                "t_stat": t_stat,
                "n_folds": len(vals),
                "dead": abs(mean_ic) < 0.02,
            }
        )

    df = pd.DataFrame(rows)
    df = df.sort_values("mean_ic", key=abs, ascending=False).reset_index(drop=True)
    return df


def permutation_importance_oos(
    model,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    n_repeats: int = 20,
    scoring: str = "roc_auc",
    random_state: int = 42,
) -> pd.DataFrame:
    """
    OOS permutation importance via sklearn.

    Shuffles each feature column in turn and measures the drop in `scoring`
    on the OOS test set.  A large drop = feature is important; a zero or
    negative drop = feature adds no signal (or adds noise).

    Args:
        model:       Fitted model with a predict_proba method.
        X_test:      OOS feature DataFrame (columns = feature names).
        y_test:      OOS label Series.
        n_repeats:   Number of permutation shuffles per feature.
        scoring:     Metric passed to sklearn (default "roc_auc").
        random_state: Reproducibility seed.

    Returns:
        DataFrame sorted by mean_importance descending:
          feature | mean_importance | std_importance
    """
    result = _sk_perm_importance(
        model,
        X_test,
        y_test,
        n_repeats=n_repeats,
        scoring=scoring,
        random_state=random_state,
    )
    df = pd.DataFrame(
        {
            "feature": X_test.columns.tolist(),
            "mean_importance": result.importances_mean,
            "std_importance": result.importances_std,
        }
    )
    return df.sort_values("mean_importance", ascending=False).reset_index(drop=True)
