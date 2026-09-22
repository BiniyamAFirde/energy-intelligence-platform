"""Deterministic site/building subset selection for the MVP.

Rule (agreed with the project owner): 2-3 sites, ~30-60 buildings total,
electricity meters only, excluding buildings with excessive missingness in
electricity_cleaned.csv.

Algorithm (deterministic, so re-running always produces the same subset):
  1. Filter metadata to buildings flagged electricity == 'Yes'.
  2. Compute each building's missing-value rate in electricity_cleaned.csv.
  3. Drop buildings at or above MAX_MISSING_RATE.
  4. Rank the remaining sites by qualifying building count ascending (smaller
     sites first -- this maximizes how many distinct sites fit under the
     MAX_BUILDINGS ceiling), breaking ties by mean missingness ascending
     (prefer the cleaner site).
  5. Greedily add whole sites while under MAX_SITES and MAX_BUILDINGS. If
     adding a whole site would exceed MAX_BUILDINGS, take only that site's
     lowest-missingness buildings up to the remaining budget, then stop.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MAX_MISSING_RATE = 0.05
MIN_BUILDINGS = 30
MAX_BUILDINGS = 60
MAX_SITES = 3


@dataclass
class SelectionResult:
    building_codes: list[str]
    site_codes: list[str]
    per_site_counts: dict[str, int]
    per_building_missing_rate: dict[str, float]
    max_missing_rate: float


def select_subset(metadata_df: pd.DataFrame, electricity_df: pd.DataFrame) -> SelectionResult:
    building_cols = [c for c in electricity_df.columns if c != "timestamp"]
    missing_rate = electricity_df[building_cols].isna().mean()

    candidates = metadata_df[metadata_df["electricity"] == "Yes"].copy()
    candidates["missing_rate"] = candidates["building_id"].map(missing_rate)
    candidates = candidates.dropna(subset=["missing_rate"])
    candidates = candidates[candidates["missing_rate"] < MAX_MISSING_RATE]

    site_stats = (
        candidates.groupby("site_id")["missing_rate"]
        .agg(count="count", mean_missing="mean")
        .sort_values(by=["count", "mean_missing"], ascending=[True, True])
    )

    selected_building_codes: list[str] = []
    selected_sites: list[str] = []
    per_site_counts: dict[str, int] = {}

    for site_id, row in site_stats.iterrows():
        if len(selected_sites) >= MAX_SITES:
            break
        remaining_budget = MAX_BUILDINGS - len(selected_building_codes)
        if remaining_budget <= 0:
            break

        site_buildings = candidates[candidates["site_id"] == site_id].sort_values("missing_rate")
        take = site_buildings.head(remaining_budget)

        selected_building_codes.extend(take["building_id"].tolist())
        selected_sites.append(site_id)
        per_site_counts[site_id] = len(take)

    if len(selected_building_codes) < MIN_BUILDINGS:
        raise ValueError(
            f"Selection produced only {len(selected_building_codes)} buildings, "
            f"below the minimum of {MIN_BUILDINGS}. Loosen MAX_MISSING_RATE or MAX_SITES."
        )

    return SelectionResult(
        building_codes=selected_building_codes,
        site_codes=selected_sites,
        per_site_counts=per_site_counts,
        per_building_missing_rate={
            b: round(float(missing_rate[b]), 4) for b in selected_building_codes
        },
        max_missing_rate=MAX_MISSING_RATE,
    )
