"""Pure logic tests for split_dataset/drop_incomplete_rows -- no DB needed,
these operate on plain DataFrames."""

import numpy as np
import pandas as pd

from energy_platform.forecasting.dataset import (
    FEATURE_COLUMNS,
    HORIZONS,
    TEST_END_EXCL,
    TRAIN_END_EXCL,
    TRAIN_START,
    VALIDATION_END_EXCL,
    drop_incomplete_rows,
    split_dataset,
)


def _make_row(origin_ts, horizon=1, **overrides):
    row = {col: 1.0 for col in FEATURE_COLUMNS}
    row["primary_use"] = "Office"  # categorical, not really 1.0-able but harmless as placeholder
    row["origin_ts"] = origin_ts
    row["horizon"] = horizon
    row["target_ts"] = origin_ts + pd.Timedelta(hours=horizon)
    row["target_kwh"] = 5.0
    row.update(overrides)
    return row


def _origin_block(origin_ts, horizons=HORIZONS):
    """All 24 rows for one forecast origin -- what a real (origin, horizon)
    expansion produces for a single day-ahead forecast."""
    return [_make_row(origin_ts, h) for h in horizons]


def test_split_dataset_assigns_rows_to_correct_window_by_origin():
    df = pd.DataFrame([
        _make_row(TRAIN_START + pd.Timedelta(hours=1)),
        _make_row(TRAIN_END_EXCL - pd.Timedelta(hours=1)),
        _make_row(TRAIN_END_EXCL),  # first validation origin (exclusive train boundary)
        _make_row(VALIDATION_END_EXCL - pd.Timedelta(hours=1)),
        _make_row(VALIDATION_END_EXCL),  # first test origin
        _make_row(TEST_END_EXCL - pd.Timedelta(hours=1)),
    ])
    train, val, test = split_dataset(df)
    assert len(train) == 2
    assert len(val) == 2
    assert len(test) == 2


def test_split_dataset_excludes_rows_outside_all_windows():
    df = pd.DataFrame([
        _make_row(TRAIN_START - pd.Timedelta(hours=1)),  # before everything
        _make_row(TEST_END_EXCL),  # at/after test end
    ])
    train, val, test = split_dataset(df)
    assert len(train) == 0 and len(val) == 0 and len(test) == 0


def test_split_windows_are_contiguous_and_non_overlapping():
    assert TRAIN_START < TRAIN_END_EXCL < VALIDATION_END_EXCL < TEST_END_EXCL


# --- Regression tests for the origin_ts-based split correction ---
#
# Previously split_dataset filtered on target_ts. Because one origin
# produces 24 rows (h=1..24), an origin within 24h of a boundary could have
# some horizons' targets fall before the boundary and others after -- e.g.
# an origin at local midnight June 30 has targets spanning June 30 01:00
# through July 1 00:00, so h=24 alone landed on the validation side of a
# July 1 boundary while h=1..23 stayed in train. Splitting by origin_ts
# instead guarantees a whole 24-hour forecast is never split across a
# boundary.

def test_no_origin_ts_appears_in_more_than_one_split():
    origins = [
        TRAIN_START, TRAIN_END_EXCL - pd.Timedelta(days=1),
        TRAIN_END_EXCL, VALIDATION_END_EXCL - pd.Timedelta(days=1),
        VALIDATION_END_EXCL, TEST_END_EXCL - pd.Timedelta(days=1),
    ]
    df = pd.DataFrame([row for origin in origins for row in _origin_block(origin)])
    train, val, test = split_dataset(df)

    train_origins = set(train["origin_ts"])
    val_origins = set(val["origin_ts"])
    test_origins = set(test["origin_ts"])
    assert train_origins.isdisjoint(val_origins)
    assert train_origins.isdisjoint(test_origins)
    assert val_origins.isdisjoint(test_origins)


def test_boundary_straddling_origin_lands_entirely_in_one_split():
    """The exact scenario the user identified: an origin at local midnight
    the day before a split boundary has targets that span across that
    boundary (h=1..23 before it, h=24 at/after it). All 24 rows must end
    up in the SAME split as their origin, not split 23/1."""
    boundary_eve_origin = TRAIN_END_EXCL - pd.Timedelta(days=1)  # "June 30 00:00"
    df = pd.DataFrame(_origin_block(boundary_eve_origin))

    # sanity check this origin really does straddle the boundary in target_ts terms
    assert (df["target_ts"] < TRAIN_END_EXCL).sum() == 23
    assert (df["target_ts"] >= TRAIN_END_EXCL).sum() == 1

    train, val, test = split_dataset(df)
    assert len(train) == 24  # all 24 horizons, not 23
    assert len(val) == 0
    assert len(test) == 0


def test_drop_incomplete_rows_drops_nan_feature():
    df = pd.DataFrame(_origin_block(pd.Timestamp("2016-01-08", tz="UTC"), horizons=range(1, 4)))
    df.loc[1, "lag_168"] = np.nan
    result = drop_incomplete_rows(df)
    assert len(result) == 2


def test_drop_incomplete_rows_drops_nan_target():
    df = pd.DataFrame(_origin_block(pd.Timestamp("2016-01-08", tz="UTC"), horizons=range(1, 4)))
    df.loc[0, "target_kwh"] = np.nan
    result = drop_incomplete_rows(df)
    assert len(result) == 2
