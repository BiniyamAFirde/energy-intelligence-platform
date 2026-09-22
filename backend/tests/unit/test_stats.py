import math
from decimal import Decimal

from energy_platform.services.stats import (
    average_period_intensity,
    coefficient_of_variation,
    energy_intensity,
    pearson_correlation,
)


def test_coefficient_of_variation_basic():
    assert coefficient_of_variation(mean=20.0, stddev=5.0) == 0.25


def test_coefficient_of_variation_none_when_mean_zero():
    assert coefficient_of_variation(mean=0.0, stddev=5.0) is None


def test_coefficient_of_variation_none_when_inputs_missing():
    assert coefficient_of_variation(None, 5.0) is None
    assert coefficient_of_variation(5.0, None) is None


def test_energy_intensity_basic():
    assert energy_intensity(total_kwh=1000.0, area_sqm=100.0) == 10.0


def test_energy_intensity_none_when_area_missing_or_zero():
    assert energy_intensity(1000.0, None) is None
    assert energy_intensity(1000.0, 0.0) is None


def test_average_period_intensity_divides_mean_of_periods_by_area():
    # mean(10, 20, 30) = 20; 20 / area(2) = 10
    assert average_period_intensity([10.0, 20.0, 30.0], area_sqm=2.0) == 10.0


def test_average_period_intensity_none_for_empty_periods():
    assert average_period_intensity([], area_sqm=100.0) is None


def test_pearson_correlation_perfect_positive():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [2.0, 4.0, 6.0, 8.0, 10.0]
    corr, n = pearson_correlation(xs, ys)
    assert n == 5
    assert math.isclose(corr, 1.0, abs_tol=1e-9)


def test_pearson_correlation_perfect_negative():
    xs = [1.0, 2.0, 3.0]
    ys = [3.0, 2.0, 1.0]
    corr, n = pearson_correlation(xs, ys)
    assert n == 3
    assert math.isclose(corr, -1.0, abs_tol=1e-9)


def test_pearson_correlation_drops_pairs_with_missing_values():
    xs = [1.0, 2.0, None, 4.0]
    ys = [2.0, 4.0, 6.0, None]
    corr, n = pearson_correlation(xs, ys)
    # only (1,2) and (2,4) are complete pairs
    assert n == 2
    assert math.isclose(corr, 1.0, abs_tol=1e-9)


def test_pearson_correlation_returns_none_with_fewer_than_two_pairs():
    corr, n = pearson_correlation([1.0], [2.0])
    assert corr is None
    assert n == 1


def test_pearson_correlation_returns_none_for_zero_variance_series():
    xs = [5.0, 5.0, 5.0]
    ys = [1.0, 2.0, 3.0]
    corr, n = pearson_correlation(xs, ys)
    assert corr is None
    assert n == 3


def test_pearson_correlation_handles_nan_values_from_pandas():
    xs = [1.0, float("nan"), 3.0]
    ys = [1.0, 2.0, 3.0]
    corr, n = pearson_correlation(xs, ys)
    assert n == 2


# --- Regression tests: SQLAlchemy returns decimal.Decimal for NUMERIC
# columns (every aggregate here -- avg, sum, stddev_samp, area_sqm), and
# mixing float/Decimal division raises TypeError. These functions must
# accept Decimal inputs from real query results, not just plain floats. ---

def test_coefficient_of_variation_accepts_decimal_inputs():
    result = coefficient_of_variation(mean=Decimal("20.0"), stddev=Decimal("5.0"))
    assert result == 0.25


def test_energy_intensity_accepts_decimal_inputs():
    result = energy_intensity(total_kwh=Decimal("1000.0"), area_sqm=Decimal("100.0"))
    assert result == 10.0


def test_average_period_intensity_accepts_decimal_area_with_float_totals():
    # mirrors real usage: per-period totals cast to float, area still Decimal
    result = average_period_intensity([10.0, 20.0, 30.0], area_sqm=Decimal("2.0"))
    assert result == 10.0


def test_pearson_correlation_accepts_decimal_inputs():
    xs = [Decimal("1.0"), Decimal("2.0"), Decimal("3.0")]
    ys = [Decimal("2.0"), Decimal("4.0"), Decimal("6.0")]
    corr, n = pearson_correlation(xs, ys)
    assert n == 3
    assert math.isclose(corr, 1.0, abs_tol=1e-9)
