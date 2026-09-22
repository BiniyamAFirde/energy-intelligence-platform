# reports/

Generated output, regenerable from the database at any time -- gitignored
(except this file and each subdirectory's `.gitkeep`), kept out of the raw
data pipeline (`data/`) since these are analysis artifacts, not source or
processed data.

| Directory | Regenerate with |
|---|---|
| `reports/eda/*.png` | `python -m energy_platform.analytics.report` |
| `reports/forecasting/*.png` | `python -m energy_platform.forecasting.plots` |
| `reports/anomalies/*.png` | `python -m energy_platform.anomalies.plots` |
