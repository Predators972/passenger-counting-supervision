"""
Anomaly detection logic.

Implements CDC sections 4.1 (vehicle-level) and 4.2 (door-level).
Sections 4.3 (inconsistent reporting) and 4.4 (GPS anomaly) are not yet
implemented - thresholds/rules still need to be defined with the business.
"""

from datetime import datetime, timedelta
import pandas as pd

from app.config import (
    VEHICLE_ANOMALY_THRESHOLD_HOURS, DOOR_ANOMALY_THRESHOLD_HOURS,
    EXPLOITATION_STATES,
)
from app.database import get_door_columns, utc_now, to_local_iso


def get_last_exploitation_time(metrics_df: pd.DataFrame):
    """
    Return the most recent timestamp at which this vehicle had
    operation_state 1 (service commercial) or 2 (HLP) - i.e. genuinely in
    service, as opposed to idle at the depot. Returns None if no such row
    is found in the fetched window (in that case callers should fall back
    to "now" as the reference point).
    """
    if "operation_state" not in metrics_df.columns:
        return None
    in_service = metrics_df[
        metrics_df["operation_state"].isin(EXPLOITATION_STATES) & metrics_df["timestamp"].notna()
    ]
    if in_service.empty:
        return None
    return in_service["timestamp"].max()


def get_last_exploitation_per_vehicle(metrics_df: pd.DataFrame) -> dict:
    """
    Same as get_last_exploitation_time, but for every vehicle in metrics_df
    at once (used by the global fleet view). Returns {num_parc: timestamp}.
    """
    if "operation_state" not in metrics_df.columns:
        return {}
    in_service = metrics_df[
        metrics_df["operation_state"].isin(EXPLOITATION_STATES) & metrics_df["timestamp"].notna()
    ]
    if in_service.empty:
        return {}
    return in_service.groupby("num_parc")["timestamp"].max().to_dict()


def get_vehicle_overview(metrics_df: pd.DataFrame, now: datetime = None) -> list[dict]:
    """
    Build the "vue globale du parc" (CDC 3.1): one row per vehicle with its
    last report time and status (fonctionnel / anomalie).
    """
    now = now or utc_now()

    valid = metrics_df.dropna(subset=["timestamp"])
    if valid.empty:
        return []

    idx = valid.groupby("num_parc")["timestamp"].idxmax()
    last_rows = valid.loc[idx]

    results = []
    for _, row in last_rows.iterrows():
        hours_since = (now - row["timestamp"]).total_seconds() / 3600
        status = "anomalie" if hours_since > VEHICLE_ANOMALY_THRESHOLD_HOURS else "fonctionnel"
        results.append({
            "num_parc": row["num_parc"],
            "last_seen": row["timestamp"],
            "hours_since_last_seen": round(hours_since, 1),
            "status": status,
        })

    return sorted(results, key=lambda v: v["num_parc"])


def get_door_status_for_vehicle(
    door_counts_df: pd.DataFrame,
    num_parc,
    reference_time: datetime,
    expected_doors: list[int] | None = None,
) -> list[dict]:
    """
    Build the door-level detail for one vehicle (CDC 3.2 / 4.2).

    A door is flagged as anomaly only if its last report is MORE THAN
    DOOR_ANOMALY_THRESHOLD_HOURS *before* `reference_time` - i.e. it went
    silent before (or during) the vehicle's last known exploitation and
    never reported again since. A door that reported AFTER reference_time
    is never an anomaly, even if that's a while ago: it proves the door
    still works (e.g. a maintainer walking past a sensor while the vehicle
    was idle at the depot is completely normal and not a fault).

    reference_time should normally be the vehicle's last known genuine
    exploitation time (operation_state 1 or 2 - see routes/vehicles.py),
    NOT simply "now". Comparing doors only to "now" is unreliable: a
    vehicle idle at the depot for repairs can go quiet for days without
    that being a real problem.

    Passenger volume values (PX_IN / PX_OUT) are used only to detect the
    last non-null timestamp per door - they are never included in the output.
    """
    vehicle_df = door_counts_df[door_counts_df["num_parc"] == num_parc]

    candidate_doors = expected_doors if expected_doors is not None else get_door_columns(vehicle_df)
    door_last_seen = {}

    for door_num in candidate_doors:
        in_col = f"P{door_num}_IN"
        out_col = f"P{door_num}_OUT"
        cols_present = [c for c in (in_col, out_col) if c in vehicle_df.columns]
        if not cols_present or vehicle_df.empty:
            door_last_seen[door_num] = None
            continue

        has_value = vehicle_df[cols_present].notna().any(axis=1)
        reported_rows = vehicle_df[has_value]
        door_last_seen[door_num] = reported_rows["timestamp"].max() if not reported_rows.empty else None

    if expected_doors is None:
        # Fallback mode: drop doors that never reported in the fetched window
        # (see docstring - we can't yet tell "silent" from "doesn't exist").
        door_last_seen = {d: ts for d, ts in door_last_seen.items() if ts is not None}

    now = utc_now()
    results = []
    for door_num, last_ts in sorted(door_last_seen.items()):
        if last_ts is None:
            status = "anomalie"
            hours_since_now = None
        else:
            # Positive = door's last report is BEFORE reference_time (stale).
            # Negative or zero = door reported at/after reference_time, i.e.
            # it's definitely not silent - always "fonctionnel" in that case.
            hours_behind_reference = (reference_time - last_ts).total_seconds() / 3600
            status = "anomalie" if hours_behind_reference > DOOR_ANOMALY_THRESHOLD_HOURS else "fonctionnel"
            # Displayed duration is always relative to "now", for consistency
            # with the rest of the UI - the status decision above is the only
            # place that uses reference_time (last exploitation).
            hours_since_now = (now - last_ts).total_seconds() / 3600

        results.append({
            "porte": door_num,
            "last_seen": to_local_iso(last_ts),
            "hours_since_last_seen": round(hours_since_now, 2) if hours_since_now is not None else None,
            "status": status,
        })

    return results
