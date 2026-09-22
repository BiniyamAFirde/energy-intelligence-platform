"""Model pipelines. Each is a single sklearn Pipeline: impute -> encode ->
estimator. Fitting a Pipeline on the training split fits every step
(imputer median, one-hot categories, Ridge's scaler) on training data only
-- val/test only ever go through .transform(), never .fit() -- which is
exactly the "scaler/encoder fit only on training data" leakage rule from
the Phase 7 spec, enforced by sklearn's Pipeline machinery rather than
hand-rolled bookkeeping.

Pooled, not per-building: one model per algorithm trained across all 60
buildings together (building identity/metadata enters as features), not 60
separate models. At ~15-17k rows/building after warm-up and splitting, a
per-building model would be far more prone to overfitting than a pooled
one, and static features like area_sqm/primary_use only carry information
in a pooled setting -- in a per-building model they'd be constants.
"""

from __future__ import annotations

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from energy_platform.forecasting.dataset import (
    CALENDAR_FEATURES,
    HORIZON_FEATURE,
    LAG_FEATURES,
    ROLLING_FEATURES,
    STATIC_CATEGORICAL_FEATURES,
    STATIC_NUMERIC_IMPUTED,
    STATIC_NUMERIC_REQUIRED,
)

NUMERIC_FEATURES = (
    CALENDAR_FEATURES + LAG_FEATURES + ROLLING_FEATURES
    + STATIC_NUMERIC_REQUIRED + STATIC_NUMERIC_IMPUTED + HORIZON_FEATURE
)
CATEGORICAL_FEATURES = STATIC_CATEGORICAL_FEATURES


def build_preprocessor(scale_numeric: bool) -> ColumnTransformer:
    numeric_steps = [("impute", SimpleImputer(strategy="median"))]
    if scale_numeric:
        numeric_steps.append(("scale", StandardScaler()))
    numeric_pipe = Pipeline(numeric_steps)

    categorical_pipe = Pipeline([
        ("impute", SimpleImputer(strategy="most_frequent")),
        ("onehot", OneHotEncoder(handle_unknown="ignore")),
    ])

    return ColumnTransformer([
        ("numeric", numeric_pipe, NUMERIC_FEATURES),
        ("categorical", categorical_pipe, CATEGORICAL_FEATURES),
    ])


def build_ridge_pipeline(alpha: float = 1.0, seed: int = 42) -> Pipeline:
    return Pipeline([
        ("preprocess", build_preprocessor(scale_numeric=True)),
        ("model", Ridge(alpha=alpha, random_state=seed)),
    ])


def build_random_forest_pipeline(seed: int = 42) -> Pipeline:
    # Reasonable initial configuration, not a search: capped depth keeps
    # training time/memory bounded on ~684k rows without materially
    # hurting a random forest's typical performance profile.
    return Pipeline([
        ("preprocess", build_preprocessor(scale_numeric=False)),
        ("model", RandomForestRegressor(
            n_estimators=100, max_depth=20, min_samples_leaf=5,
            n_jobs=-1, random_state=seed,
        )),
    ])


def build_hist_gb_pipeline(seed: int = 42) -> Pipeline:
    return Pipeline([
        ("preprocess", build_preprocessor(scale_numeric=False)),
        ("model", HistGradientBoostingRegressor(random_state=seed)),
    ])
