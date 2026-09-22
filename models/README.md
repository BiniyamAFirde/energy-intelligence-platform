# models/

Trained model artifacts (`joblib`), one per model name: `ridge_v1.joblib`,
`random_forest_v1.joblib`, `hist_gradient_boosting_v1.joblib`. Regenerable
at any time via `python -m energy_platform.forecasting.train` -- gitignored
(the Random Forest artifact alone is ~550MB). The `model_versions` database
table is the durable record of what configuration produced each one; this
directory is just the cache.
