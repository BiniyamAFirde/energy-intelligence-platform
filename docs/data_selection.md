# MVP data subset: selection method and result

## Rule (agreed scope)
2-3 sites, ~30-60 buildings total, electricity meters only, excluding
buildings with excessive missingness in `electricity_cleaned.csv`.

## Algorithm (`energy_platform/ingestion/selection.py::select_subset`)
1. Filter `metadata.csv` to buildings flagged `electricity == 'Yes'` (1,578
   of 1,636 buildings).
2. Compute each building's missing-value rate over the full two-year hourly
   series in `electricity_cleaned.csv`.
3. Drop any building at or above **5% missing** (`MAX_MISSING_RATE = 0.05`).
4. Rank the remaining sites by qualifying building count, ascending (smaller
   sites first, to fit more distinct sites under the building ceiling),
   breaking ties by mean missingness, ascending (prefer the cleaner site).
5. Greedily add whole sites while under 3 sites and 60 buildings. If adding a
   whole site would exceed 60, take only that site's lowest-missingness
   buildings up to the remaining budget, then stop.

This is deterministic and reproducible: re-running it against the same raw
files always yields the same subset.

## Result (actual run against the verified BDG2 files)

| Site | Timezone | Qualifying buildings (<5% missing) | Selected |
|---|---|---|---|
| Moose | US/Eastern | 9 | 9 (all) |
| Gator | US/Eastern | 21 | 21 (all) |
| Peacock | US/Eastern | 36 | 30 (capped to fill the 60-building ceiling; the 30 with lowest missingness) |

**Total: 60 buildings across 3 sites.** Missing-rate range across selected
buildings: 0.0% - 4.94%.

Primary-use breakdown of the selected 60 buildings: Education (20), Public
services (11), Lodging/residential (9), Entertainment/public assembly (9),
Office (7), Other (3), Warehouse/storage (1) -- enough category variety to
make "consumption by building type" analysis meaningful.

## Known limitation
All three selected sites happen to share the same timezone (US/Eastern),
because the algorithm optimizes purely for building count and data quality,
not geographic/climate diversity. A future iteration could add a diversity
term to the site ranking (e.g. require at least one non-US/Eastern site) if
the weather-correlation analysis would benefit from wider climate variation.
This is a deliberate scope decision, not an oversight: for the MVP, data
quality took priority over geographic spread.
