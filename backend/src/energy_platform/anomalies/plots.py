"""python -m energy_platform.anomalies.plots

Seven plots, each answering one specific question about Phase 8's anomaly
detection system -- not a plot dump. Four are built entirely from the
already-computed evaluation report JSON (data/processed/
anomaly_evaluation_report.json); the other three need the live database
(real alert counts/timing from detect.py's production run, and one
concrete real example with its surrounding time series).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sqlalchemy import func, select

from energy_platform.anomalies.evaluate import REPORT_PATH
from energy_platform.db.base import SessionLocal
from energy_platform.db.models import Alert, EnergyMeasurement

PLOTS_DIR = Path("reports/anomalies")

DETECTOR_LABELS = {
    "data_quality": "A: Data quality",
    "behavioral": "B: Behavioral",
    "forecast_residual": "C: Forecast residual",
    "isolation_forest": "D: Isolation Forest",
}


def plot_detector_comparison(report: dict, out_path: Path) -> None:
    detectors = list(DETECTOR_LABELS)
    val = report["splits"]["validation"]["detectors"]
    test = report["splits"]["test"]["detectors"]

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
    for ax, metric in zip(axes, ["precision", "recall", "f1"]):
        x = np.arange(len(detectors))
        width = 0.35
        val_vals = [val[d][metric] or 0 for d in detectors]
        test_vals = [test[d][metric] or 0 for d in detectors]
        ax.bar(x - width / 2, val_vals, width, label="Validation")
        ax.bar(x + width / 2, test_vals, width, label="Test (frozen threshold)")
        ax.set_xticks(x)
        ax.set_xticklabels([DETECTOR_LABELS[d] for d in detectors], rotation=30, ha="right")
        ax.set_title(metric.upper())
        ax.set_ylim(0, max(max(val_vals), max(test_vals)) * 1.2 + 0.01)
    axes[0].legend()
    fig.suptitle("Detector comparison: precision / recall / F1 against synthetic injections")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_threshold_sweep(report: dict, out_path: Path) -> None:
    sweeps = report["threshold_selection"]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
    for ax, (key, label) in zip(
        axes,
        [("behavioral", "B: |z| threshold"), ("forecast_residual", "C: |z| threshold"), ("isolation_forest", "D: score threshold")],
    ):
        sweep = pd.DataFrame(sweeps[key]["sweep"])
        best = sweeps[key]["best"]
        ax.plot(sweep["threshold"], sweep["f1"], marker="o", label="F1 (validation)")
        ax.axvline(best["threshold"], color="red", linestyle="--", label=f"selected = {best['threshold']:.3f}")
        ax.set_xlabel(label)
        ax.set_ylabel("F1")
        ax.set_title(DETECTOR_LABELS[key])
        ax.legend(fontsize=8)
    fig.suptitle("Threshold selection on VALIDATION only, then frozen for TEST")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_class_balance(report: dict, out_path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    for ax, split in zip(axes, ["validation", "test"]):
        counts = report["splits"][split]["class_balance"]["counts_by_type"]
        types = sorted(counts, key=counts.get, reverse=True)
        ax.bar(types, [counts[t] for t in types])
        ax.set_xticks(range(len(types)))
        ax.set_xticklabels(types, rotation=30, ha="right")
        rate = report["splits"][split]["class_balance"]["anomaly_rate_pct"]
        ax.set_title(f"{split.capitalize()}: {sum(counts.values())} injected ({rate}% of all slots)")
        ax.set_ylabel("Injected count")
    fig.suptitle("Synthetic ground-truth class balance (injection.py)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_recall_heatmap(report: dict, out_path: Path) -> None:
    detectors = list(DETECTOR_LABELS)
    val = report["splits"]["validation"]["detectors"]
    all_types = sorted({t for d in detectors for t in val[d]["recall_by_anomaly_type"]})

    matrix = np.array([
        [val[d]["recall_by_anomaly_type"].get(t, {"recall": None})["recall"] or 0.0 for t in all_types]
        for d in detectors
    ])

    fig, ax = plt.subplots(figsize=(9, 4.5))
    im = ax.imshow(matrix, cmap="viridis", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(all_types)))
    ax.set_xticklabels(all_types, rotation=30, ha="right")
    ax.set_yticks(range(len(detectors)))
    ax.set_yticklabels([DETECTOR_LABELS[d] for d in detectors])
    for i in range(len(detectors)):
        for j in range(len(all_types)):
            ax.text(j, i, f"{matrix[i, j]:.2f}", ha="center", va="center", color="white", fontsize=8)
    fig.colorbar(im, ax=ax, label="Recall")
    ax.set_title("Recall by injected anomaly type, per detector (validation)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_alerts_by_method_severity(session, out_path: Path) -> None:
    rows = session.execute(
        select(Alert.method, Alert.severity, func.count()).group_by(Alert.method, Alert.severity)
    ).all()
    df = pd.DataFrame(rows, columns=["method", "severity", "count"])
    pivot = df.pivot(index="method", columns="severity", values="count").fillna(0)
    pivot = pivot.reindex([m for m in DETECTOR_LABELS if m in pivot.index])

    fig, ax = plt.subplots(figsize=(8, 5))
    bottom = np.zeros(len(pivot))
    for severity in ["low", "medium", "high"]:
        if severity not in pivot.columns:
            continue
        ax.bar([DETECTOR_LABELS[m] for m in pivot.index], pivot[severity], bottom=bottom, label=severity)
        bottom += pivot[severity].to_numpy()
    ax.set_ylabel("Alert count (real production run, full 2016-2018 dataset)")
    ax.set_title("Alerts by detector and severity (detect.py, real data)")
    ax.legend(title="severity")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_alerts_over_time(session, out_path: Path) -> None:
    rows = session.execute(select(Alert.method, Alert.ts)).all()
    df = pd.DataFrame(rows, columns=["method", "ts"])
    df["month"] = pd.to_datetime(df["ts"]).dt.tz_localize(None).dt.to_period("M").dt.to_timestamp()
    pivot = df.groupby(["month", "method"]).size().unstack(fill_value=0)
    pivot = pivot.reindex(columns=[m for m in DETECTOR_LABELS if m in pivot.columns])

    fig, ax = plt.subplots(figsize=(11, 4.5))
    bottom = np.zeros(len(pivot))
    for method in pivot.columns:
        ax.bar(pivot.index, pivot[method], bottom=bottom, width=20, label=DETECTOR_LABELS[method])
        bottom += pivot[method].to_numpy()
    ax.set_ylabel("Alert count")
    ax.set_title("Alerts over time, by detector (real production run)")
    ax.legend(fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_example_anomaly(session, sensor_id: int, center_ts: pd.Timestamp, alert_explanation: str, out_path: Path) -> None:
    window_start = center_ts - pd.Timedelta(hours=12)
    window_end = center_ts + pd.Timedelta(hours=12)
    rows = session.execute(
        select(EnergyMeasurement.ts, EnergyMeasurement.consumption_kwh)
        .where(
            EnergyMeasurement.sensor_id == sensor_id,
            EnergyMeasurement.ts >= window_start,
            EnergyMeasurement.ts <= window_end,
        )
        .order_by(EnergyMeasurement.ts)
    ).all()
    df = pd.DataFrame(rows, columns=["ts", "consumption_kwh"])

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(df["ts"], df["consumption_kwh"].astype(float), marker="o", label="consumption_kwh")
    ax.scatter(
        [center_ts], df.loc[df["ts"] == center_ts, "consumption_kwh"].astype(float),
        color="red", s=120, zorder=5, label="Flagged point",
    )
    ax.set_ylabel("kWh")
    ax.set_title(f"Detector A example -- sensor {sensor_id}, {center_ts}\n{alert_explanation}", fontsize=9, wrap=True)
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main() -> None:
    if not REPORT_PATH.exists():
        raise SystemExit(f"No evaluation report at {REPORT_PATH}. Run `python -m energy_platform.anomalies.evaluate` first.")
    report = json.loads(REPORT_PATH.read_text())
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    plot_detector_comparison(report, PLOTS_DIR / "01_detector_comparison.png")
    plot_threshold_sweep(report, PLOTS_DIR / "02_threshold_sweep.png")
    plot_class_balance(report, PLOTS_DIR / "03_class_balance.png")
    plot_recall_heatmap(report, PLOTS_DIR / "04_recall_by_type_heatmap.png")

    session = SessionLocal()
    plot_alerts_by_method_severity(session, PLOTS_DIR / "05_alerts_by_method_severity.png")
    plot_alerts_over_time(session, PLOTS_DIR / "06_alerts_over_time.png")

    example = session.execute(
        select(Alert).where(Alert.anomaly_type == "isolated_spike").order_by(Alert.score.desc())
    ).scalars().first()
    if example is not None:
        plot_example_anomaly(
            session, example.sensor_id, pd.Timestamp(example.ts),
            example.explanation, PLOTS_DIR / "07_example_anomaly.png",
        )
    session.close()

    print(f"Plots written to {PLOTS_DIR}/")


if __name__ == "__main__":
    main()
