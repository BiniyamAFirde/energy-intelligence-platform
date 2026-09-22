"""python -m energy_platform.forecasting.plots

Six plots, each answering one specific question about the trained
forecasting models -- not a plot dump. Three (error-by-horizon, model
comparison, performance-by-building/site) are built entirely from the
already-computed training report JSON; the other three (actual-vs-predicted,
a representative building/day forecast, residual distribution) need raw
per-row predictions, so this script rebuilds the validation set once and
runs the selected model's pipeline over it.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from energy_platform.db.base import SessionLocal
from energy_platform.forecasting import dataset, train

REPORT_PATH = train.REPORT_PATH
PLOTS_DIR = Path("reports/forecasting")


def plot_model_comparison(comparison_table: list[dict], out_path: Path) -> None:
    df = pd.DataFrame(comparison_table).set_index("model")
    fig, ax = plt.subplots(figsize=(8, 5))
    x = range(len(df))
    width = 0.35
    ax.bar([i - width / 2 for i in x], df["validation_rmse"], width, label="Validation RMSE")
    ax.bar([i + width / 2 for i in x], df["test_rmse"], width, label="Test RMSE")
    ax.set_xticks(list(x))
    ax.set_xticklabels(df.index, rotation=30, ha="right")
    ax.set_ylabel("RMSE (kWh)")
    ax.set_title("Model comparison: baselines vs Ridge vs Random Forest vs HistGB")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_error_by_horizon(by_horizon: list[dict], model_name: str, out_path: Path) -> None:
    df = pd.DataFrame(by_horizon).dropna(subset=["rmse"])
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(df["horizon"], df["rmse"], marker="o", label="RMSE")
    ax.plot(df["horizon"], df["mae"], marker="s", label="MAE")
    ax.set_xlabel("Forecast horizon (hours ahead)")
    ax.set_ylabel("Error (kWh)")
    ax.set_title(f"{model_name}: error by forecast horizon (validation)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_performance_by_building_and_site(
    by_building: list[dict], by_site: list[dict], model_name: str, out_path: Path
) -> None:
    bb = pd.DataFrame(by_building).dropna(subset=["cv_rmse_pct"]).sort_values("cv_rmse_pct")
    bs = pd.DataFrame(by_site).dropna(subset=["cv_rmse_pct"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={"width_ratios": [3, 1]})
    ax1.hist(bb["cv_rmse_pct"], bins=20)
    ax1.set_xlabel("CV(RMSE) % (per building)")
    ax1.set_ylabel("Number of buildings")
    ax1.set_title(f"{model_name}: per-building relative error distribution (validation)")

    ax2.bar(bs["site_id"].astype(str), bs["cv_rmse_pct"])
    ax2.set_xlabel("Site ID")
    ax2.set_ylabel("CV(RMSE) %")
    ax2.set_title("By site")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_actual_vs_predicted(y_true, y_pred, model_name: str, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(y_true, y_pred, s=3, alpha=0.15)
    lims = [0, max(max(y_true), max(y_pred))]
    ax.plot(lims, lims, "r--", linewidth=1, label="Perfect prediction")
    ax.set_xlabel("Actual kWh")
    ax.set_ylabel("Predicted kWh")
    ax.set_title(f"{model_name}: actual vs. predicted (validation, all horizons)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_residual_distribution(y_true, y_pred, model_name: str, out_path: Path) -> None:
    residuals = y_pred - y_true
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(residuals, bins=60)
    ax.axvline(0, color="red", linewidth=1)
    ax.set_xlabel("Residual (predicted - actual, kWh)")
    ax.set_ylabel("Count")
    ax.set_title(f"{model_name}: residual distribution (validation)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_representative_building_forecast(
    val_df: pd.DataFrame, y_pred, model_name: str, out_path: Path
) -> None:
    df = val_df.copy()
    df["_pred"] = y_pred
    # pick the origin with the most complete 24-horizon block for one building
    counts = df.groupby(["building_id", "origin_ts"]).size()
    building_id, origin_ts = counts[counts == 24].index[0]
    day = df[(df.building_id == building_id) & (df.origin_ts == origin_ts)].sort_values("horizon")

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(day["horizon"], day["target_kwh"], marker="o", label="Actual")
    ax.plot(day["horizon"], day["_pred"], marker="s", label="Predicted")
    ax.set_xlabel("Forecast horizon (hours ahead)")
    ax.set_ylabel("kWh")
    ax.set_title(f"{model_name}: day-ahead forecast, building {building_id}, origin {origin_ts}")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main() -> None:
    report = json.loads(REPORT_PATH.read_text())
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    plot_model_comparison(report["comparison_table"], PLOTS_DIR / "04_model_comparison.png")

    selected = report["selected_model"]
    selected_results = report["full_results"][selected]["validation"]
    plot_error_by_horizon(selected_results["by_horizon"], selected, PLOTS_DIR / "03_error_by_horizon.png")
    plot_performance_by_building_and_site(
        selected_results["by_building"], selected_results["by_site"], selected,
        PLOTS_DIR / "06_performance_by_building_site.png",
    )

    # Plots needing raw predictions: rebuild the validation set once and run
    # the selected model's saved pipeline over it.
    import joblib

    session = SessionLocal()
    data = dataset.build_training_dataset(session)
    session.close()
    val = data["validation"]

    pipeline = joblib.load(train.MODELS_DIR / f"{selected}_{train.MODEL_VERSION}.joblib")
    y_pred = pipeline.predict(val[dataset.FEATURE_COLUMNS])
    y_true = val["target_kwh"].values

    plot_actual_vs_predicted(y_true, y_pred, selected, PLOTS_DIR / "01_actual_vs_predicted.png")
    plot_residual_distribution(y_true, y_pred, selected, PLOTS_DIR / "05_residual_distribution.png")
    plot_representative_building_forecast(val, y_pred, selected, PLOTS_DIR / "02_representative_forecast.png")

    print(f"Plots written to {PLOTS_DIR}/")


if __name__ == "__main__":
    main()
