from energy_platform.ingestion.quality_report import expected_hourly_grid_size


def test_expected_hourly_grid_size_covers_2016_and_2017():
    # 2016 is a leap year (366 days), 2017 is not (365 days), hourly readings.
    assert expected_hourly_grid_size() == (366 + 365) * 24 == 17544
