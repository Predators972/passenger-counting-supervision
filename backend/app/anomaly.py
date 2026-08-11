"""
Anomaly detection logic.

Implements CDC sections 4.1 (vehicle-level) and 4.2 (door-level).
Sections 4.3 (inconsistent reporting) and 4.4 (GPS anomaly) are not yet
implemented - thresholds/rules still need to be defined with the business.
"""

from datetime import datetime, timedelta
import pandas as pd

from app.config import VEHICLE_ANOMALY_THRESHOLD_HOURS, DOOR_ANOMALY_THRESHOLD_HOURS
from app.database import get_door_columns


def get_vehicle_overview(metrics_df: pd.DataFrame, now: datetime = None) -> list[dict]:
    """
    Build the "vue globale du parc" (CDC 3.1): one row per vehicle with its
    last report time and status (fonctionnel / anomalie).
    """
    now = now or datetime.now()

    last_seen = (
        metrics_df.dropna(subset=["timestamp"])
        .groupby("num_parc")["timestamp"]
        .max()
        .reset_index()
    )

    results = []
    for _, row in last_seen.iterrows():
        hours_since = (now - row["timestamp"]).total_seconds() / 3600
        status = "anomalie" if hours_since > VEHICLE_ANOMALY_THRESHOLD_HOURS else "fonctionnel"
        results.append({
            "num_parc": row["num_parc"],
            "last_seen": row["timestamp"].isoformat(),
            "hours_since_last_seen": round(hours_since, 1),
            "status": status,
        })

    return sorted(results, key=lambda v: v["num_parc"])


def get_door_status_for_vehicle(door_counts_df: pd.DataFrame, num_parc, now: datetime = None) -> list[dict]:
    """
    Build the door-level detail for one vehicle (CDC 3.2 / 4.2).
    A door is flagged as anomaly if it hasn't reported within the threshold
    while at least one other door on the same vehicle has more recent data.
    Passenger volume values (PX_IN / PX_OUT) are used only to detect the
    last non-null timestamp per door - they are never included in the output.
    """
    now = now or datetime.now()

    vehicle_df = door_counts_df[door_counts_df["num_parc"] == num_parc]
    if vehicle_df.empty:
        return []

    door_numbers = get_door_columns(vehicle_df)
    door_last_seen = {}

    for door_num in door_numbers:
        in_col = f"P{door_num}_IN"
        out_col = f"P{door_num}_OUT"
        cols_present = [c for c in (in_col, out_col) if c in vehicle_df.columns]
        if not cols_present:
            continue

        has_value = vehicle_df[cols_present].notna().any(axis=1)
        reported_rows = vehicle_df[has_value]
        if reported_rows.empty:
            door_last_seen[door_num] = None
        else:
            door_last_seen[door_num] = reported_rows["timestamp"].max()

    known_timestamps = [ts for ts in door_last_seen.values() if ts is not None]
    if not known_timestamps:
        return []

    most_recent_on_vehicle = max(known_timestamps)

    results = []
    for door_num, last_ts in sorted(door_last_seen.items()):
        if last_ts is None:
            status = "anomalie"
            hours_since = None
        else:
            hours_since = (now - last_ts).total_seconds() / 3600
            other_doors_more_recent = last_ts < most_recent_on_vehicle
            status = (
                "anomalie"
                if hours_since > DOOR_ANOMALY_THRESHOLD_HOURS and other_doors_more_recent
                else "fonctionnel"
            )

        results.append({
            "porte": door_num,
            "last_seen": last_ts.isoformat() if last_ts is not None else None,
            "hours_since_last_seen": round(hours_since, 1) if hours_since is not None else None,
            "status": status,
        })

    return results
