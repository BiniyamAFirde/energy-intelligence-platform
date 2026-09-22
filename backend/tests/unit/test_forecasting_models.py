import numpy as np
import pandas as pd
import pytest

from energy_platform.forecasting import models
from energy_platform.forecasting.dataset import FEATURE_COLUMNS


def _synthetic_frame(n=200, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({col: rng.normal(size=n) for col in FEATURE_COLUMNS if col != "primary_use"})
    df["primary_use"] = rng.choice(["Office", "Education", None], size=n)
    df.loc[rng.choice(n, size=10, replace=False), "occupants"] = np.nan
    df.loc[rng.choice(n, size=10, replace=False), "number_of_floors"] = np.nan
    target = df["lag_1"] * 2 + rng.normal(scale=0.1, size=n)
    return df, target


@pytest.mark.parametrize("builder", [
    models.build_ridge_pipeline,
    models.build_random_forest_pipeline,
    models.build_hist_gb_pipeline,
])
def test_pipeline_fits_and_predicts_on_synthetic_data(builder):
    X, y = _synthetic_frame()
    pipeline = builder()
    pipeline.fit(X, y)
    preds = pipeline.predict(X)
    assert len(preds) == len(y)
    assert np.isfinite(preds).all()


def test_ridge_pipeline_handles_unseen_categorical_at_predict_time():
    X_train, y_train = _synthetic_frame(seed=1)
    pipeline = models.build_ridge_pipeline()
    pipeline.fit(X_train, y_train)

    X_test, _ = _synthetic_frame(seed=2)
    X_test["primary_use"] = "NeverSeenBefore"
    preds = pipeline.predict(X_test)  # must not raise, thanks to handle_unknown="ignore"
    assert np.isfinite(preds).all()


def test_imputer_is_fit_only_on_training_data():
    """Leakage-adjacent check: the fitted median must come from X_train's
    distribution, not be silently recomputed against X_test."""
    X_train, y_train = _synthetic_frame(seed=3)
    X_train["occupants"] = 10.0  # constant -> median is exactly 10.0
    pipeline = models.build_ridge_pipeline()
    pipeline.fit(X_train, y_train)

    imputer = pipeline.named_steps["preprocess"].named_transformers_["numeric"].named_steps["impute"]
    occupants_idx = models.NUMERIC_FEATURES.index("occupants")
    assert imputer.statistics_[occupants_idx] == 10.0
