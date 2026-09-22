import pandas as pd
import pytest

from energy_platform.ingestion.selection import select_subset


def _metadata(rows):
    return pd.DataFrame(rows, columns=["building_id", "site_id", "electricity"])


def _electricity(building_ids, missing_rates):
    """Build a tiny synthetic wide electricity frame with controllable
    missing rates per building (20 hourly rows)."""
    n = 20
    data = {"timestamp": pd.date_range("2016-01-01", periods=n, freq="h")}
    for b, rate in zip(building_ids, missing_rates):
        n_missing = round(rate * n)
        values = [None] * n_missing + [1.0] * (n - n_missing)
        data[b] = values
    return pd.DataFrame(data)


def test_selects_smallest_sites_first_and_respects_ceiling():
    # site A: 2 clean buildings, site B: 2 clean buildings, site C: 3 clean buildings
    building_ids = ["a1", "a2", "b1", "b2", "c1", "c2", "c3"]
    meta = _metadata([
        (bid, site, "Yes")
        for bid, site in zip(building_ids, ["A", "A", "B", "B", "C", "C", "C"])
    ])
    elec = _electricity(building_ids, [0.0] * 7)

    import energy_platform.ingestion.selection as sel
    old_min = sel.MIN_BUILDINGS
    sel.MIN_BUILDINGS = 2
    try:
        result = select_subset(meta, elec)
    finally:
        sel.MIN_BUILDINGS = old_min

    # all sites are small enough that all 7 buildings fit under the default
    # MAX_BUILDINGS ceiling, so every site should be selected
    assert set(result.site_codes) == {"A", "B", "C"}
    assert len(result.building_codes) == 7


def test_excludes_buildings_above_missing_threshold():
    building_ids = ["clean1", "clean2", "dirty1"]
    meta = _metadata([(b, "A", "Yes") for b in building_ids])
    elec = _electricity(building_ids, [0.0, 0.01, 0.5])  # dirty1 way above 5%

    import energy_platform.ingestion.selection as sel
    old_min = sel.MIN_BUILDINGS
    sel.MIN_BUILDINGS = 2
    try:
        result = select_subset(meta, elec)
    finally:
        sel.MIN_BUILDINGS = old_min

    assert "dirty1" not in result.building_codes
    assert set(result.building_codes) == {"clean1", "clean2"}


def test_ignores_buildings_without_electricity_flag():
    meta = _metadata([("a1", "A", "Yes"), ("a2", "A", "No"), ("a3", "A", None)])
    elec = _electricity(["a1", "a2", "a3"], [0.0, 0.0, 0.0])

    import energy_platform.ingestion.selection as sel
    old_min = sel.MIN_BUILDINGS
    sel.MIN_BUILDINGS = 1
    try:
        result = select_subset(meta, elec)
    finally:
        sel.MIN_BUILDINGS = old_min

    assert result.building_codes == ["a1"]


def test_raises_when_below_minimum_buildings():
    meta = _metadata([("a1", "A", "Yes")])
    elec = _electricity(["a1"], [0.0])
    with pytest.raises(ValueError):
        select_subset(meta, elec)  # default MIN_BUILDINGS=30, only 1 available
