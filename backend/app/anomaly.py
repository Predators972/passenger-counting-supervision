## @file anomaly.py
#  @brief Anomaly detection logic: computes vehicle, door, SAE and GPS
#  status from metrics/door_counts DataFrames already fetched from the
#  database. Contains no SQL and no FastAPI dependency.

from datetime import datetime, timedelta
import pandas as pd

from app.config import (
    VEHICLE_ANOMALY_THRESHOLD_HOURS, DOOR_ANOMALY_THRESHOLD_HOURS,
    EXPLOITATION_STATES, UNRELIABLE_OPERATION_STATES, SAE_SILENCE_THRESHOLD_HOURS, SAE_MISSING_RATIO_THRESHOLD,
    GPS_SILENCE_THRESHOLD_HOURS, GPS_MISSING_RATIO_THRESHOLD,
)
from app.database import get_door_columns, utc_now, to_local_iso


def get_last_exploitation_time(metrics_df: pd.DataFrame):
    """!
    @brief Find the most recent timestamp at which a single vehicle had an
    operation_state considered "in service".

    @param metrics_df DataFrame of metrics rows for one vehicle, with an
    "operation_state" column and a "timestamp" column.
    @return The most recent matching timestamp, or None if no row matches.
    """
    if "operation_state" not in metrics_df.columns:
        return None
    in_service = metrics_df[
        metrics_df["operation_state"].isin(EXPLOITATION_STATES) & metrics_df["timestamp"].notna()
    ]
    if in_service.empty:
        return None
    return in_service["timestamp"].max()


def get_exploitation_case(metrics_df: pd.DataFrame):
    """!
    @brief Classify a single vehicle's exploitation reference into one of
    three cases.

    "known": at least one row has a reliable in-service operation_state, so
    a real last_exploitation timestamp is available. "stale": operation_state
    has been reliably reported at least once but never with an in-service
    value. "unknown": operation_state has never been reliably reported at
    all (only unreliable values, or none).

    @param metrics_df DataFrame of metrics rows for one vehicle, with
    "operation_state" and "timestamp" columns.
    @return Tuple (case, last_exploitation) where case is one of "known",
    "stale", "unknown", and last_exploitation is a timestamp only for the
    "known" case (None otherwise).
    """
    last_exploitation = get_last_exploitation_time(metrics_df)
    if last_exploitation is not None:
        return "known", last_exploitation
    if "operation_state" in metrics_df.columns:
        reliable = metrics_df["operation_state"].notna() & ~metrics_df["operation_state"].isin(UNRELIABLE_OPERATION_STATES)
        if reliable.any():
            return "stale", None
    return "unknown", None


def get_last_exploitation_per_vehicle(metrics_df: pd.DataFrame) -> dict:
    """!
    @brief Compute the last in-service timestamp for every vehicle present
    in a metrics DataFrame.

    @param metrics_df DataFrame of metrics rows for the whole fleet.
    @return Dict mapping num_parc to its last in-service timestamp.
    """
    if "operation_state" not in metrics_df.columns:
        return {}
    in_service = metrics_df[
        metrics_df["operation_state"].isin(EXPLOITATION_STATES) & metrics_df["timestamp"].notna()
    ]
    if in_service.empty:
        return {}
    return in_service.groupby("num_parc")["timestamp"].max().to_dict()


def get_exploitation_case_per_vehicle(metrics_df: pd.DataFrame) -> dict:
    """!
    @brief Classify the exploitation reference of every vehicle present in
    a metrics DataFrame.

    @param metrics_df DataFrame of metrics rows for the whole fleet.
    @return Dict mapping num_parc to a tuple (case, last_exploitation), as
    returned by get_exploitation_case.
    """
    if "operation_state" not in metrics_df.columns:
        return {}

    result = {}
    reliable_mask = metrics_df["operation_state"].notna() & ~metrics_df["operation_state"].isin(UNRELIABLE_OPERATION_STATES)
    has_reliable_state = metrics_df.assign(_reliable=reliable_mask).groupby("num_parc")["_reliable"].any()
    last_exploitation_map = get_last_exploitation_per_vehicle(metrics_df)

    for num_parc in metrics_df["num_parc"].dropna().unique():
        last_exp = last_exploitation_map.get(num_parc)
        if last_exp is not None:
            result[num_parc] = ("known", last_exp)
        elif has_reliable_state.get(num_parc, False):
            result[num_parc] = ("stale", None)
        else:
            result[num_parc] = ("unknown", None)

    return result


def get_vehicle_overview(metrics_df: pd.DataFrame, now: datetime = None) -> list[dict]:
    """!
    @brief Build the fleet overview: one entry per vehicle with its last
    report time and status.

    @param metrics_df DataFrame of metrics rows for the whole fleet (or a
    single vehicle).
    @param now Reference time to compute elapsed hours from; defaults to
    the current UTC time.
    @return List of dicts with keys num_parc, last_seen,
    hours_since_last_seen, status ("fonctionnel" or "anomalie"), sorted by
    num_parc.
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
    minimum_doors: int = 0,
    skip_silence_check: bool = False,
) -> list[dict]:
    """!
    @brief Build the per-door status list for one vehicle.

    A door is "anomalie" if it has no data at all, or if its last report is
    more than DOOR_ANOMALY_THRESHOLD_HOURS before reference_time. A door
    that reported at or after reference_time is always "fonctionnel".

    @param door_counts_df DataFrame of door_counts rows (any vehicle;
    filtered internally by num_parc).
    @param num_parc Vehicle number to build the door list for.
    @param reference_time Timestamp each door's last report is compared
    against.
    @param expected_doors Optional fixed list of door numbers to report on;
    when None, the door list is inferred dynamically (see minimum_doors).
    @param minimum_doors When expected_doors is None, minimum number of
    doors (1..minimum_doors) always included even with no data, in
    addition to any door number that has reported at least once.
    @param skip_silence_check When true, any door with at least one report
    is "fonctionnel" regardless of how old that report is; only a door
    with zero data stays "anomalie".
    @return List of dicts with keys porte, last_seen, hours_since_last_seen
    (relative to now), status, sorted by door number.
    """
    vehicle_df = door_counts_df[door_counts_df["num_parc"] == num_parc]

    if expected_doors is not None:
        candidate_doors = expected_doors
    else:
        candidate_doors = sorted(set(get_door_columns(vehicle_df)) | set(range(1, minimum_doors + 1)))

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
        door_last_seen = {
            d: ts for d, ts in door_last_seen.items()
            if ts is not None or d <= minimum_doors
        }

    now = utc_now()
    results = []
    for door_num, last_ts in sorted(door_last_seen.items()):
        if last_ts is None:
            status = "anomalie"
            hours_since_now = None
        elif skip_silence_check:
            status = "fonctionnel"
            hours_since_now = (now - last_ts).total_seconds() / 3600
        else:
            hours_behind_reference = (reference_time - last_ts).total_seconds() / 3600
            status = "anomalie" if hours_behind_reference > DOOR_ANOMALY_THRESHOLD_HOURS else "fonctionnel"
            hours_since_now = (now - last_ts).total_seconds() / 3600

        results.append({
            "porte": door_num,
            "last_seen": to_local_iso(last_ts),
            "hours_since_last_seen": round(hours_since_now, 2) if hours_since_now is not None else None,
            "status": status,
        })

    return results


def _field_status(group: pd.DataFrame, now, reference_time, present_mask: pd.Series, silence_threshold: float, ratio_threshold: float, force_fonctionnel: bool = False):
    """!
    @brief Compute the status of a single field (SAE or GPS) for one
    vehicle, combining a silence check and a missing-ratio check.

    @param group DataFrame of metrics rows for one vehicle.
    @param now Reference time used to compute the displayed duration.
    @param reference_time Timestamp the field's last presence is compared
    against for the silence check.
    @param present_mask Boolean Series, same length as group, true where
    the field is present on that row.
    @param silence_threshold Number of hours behind reference_time beyond
    which the field is considered silent.
    @param ratio_threshold Maximum acceptable share (0-1) of rows missing
    the field before it is considered degraded.
    @param force_fonctionnel When true, the field is always reported
    "fonctionnel" regardless of the two checks above.
    @return Dict with keys last_seen, hours_since_last_seen (relative to
    now), missing_ratio (percentage), status.
    """
    total_rows = len(group)
    missing_ratio = 1 - (present_mask.sum() / total_rows) if total_rows else 1.0

    if present_mask.any():
        last_present = group.loc[present_mask, "timestamp"].max()
        hours_behind_reference = (reference_time - last_present).total_seconds() / 3600
        hours_since_now = (now - last_present).total_seconds() / 3600
    else:
        last_present = None
        hours_behind_reference = None
        hours_since_now = None

    if force_fonctionnel:
        status = "fonctionnel"
    else:
        is_silent = last_present is None or (hours_behind_reference is not None and hours_behind_reference > silence_threshold)
        is_degraded = missing_ratio > ratio_threshold
        status = "anomalie" if (is_silent or is_degraded) else "fonctionnel"

    return {
        "last_seen": to_local_iso(last_present),
        "hours_since_last_seen": round(hours_since_now, 1) if hours_since_now is not None else None,
        "missing_ratio": round(missing_ratio * 100, 1),
        "status": status,
    }


def get_sae_gps_status(metrics_df: pd.DataFrame, now: datetime = None) -> list[dict]:
    """!
    @brief Build the SAE and GPS status for every vehicle present in a
    metrics DataFrame.

    @param metrics_df DataFrame of metrics rows including num_parc_sae,
    latitude, longitude and operation_state, for the whole fleet.
    @param now Reference time to compute elapsed hours from; defaults to
    the current UTC time.
    @return List of dicts with keys num_parc, last_seen,
    hours_since_last_seen, last_exploitation, hours_since_last_exploitation,
    exploitation_case, sae, gps (the last two being the dicts returned by
    _field_status), sorted by num_parc.
    """
    now = now or utc_now()
    valid = metrics_df.dropna(subset=["timestamp"])
    if valid.empty:
        return []

    results = []
    for num_parc, group in valid.groupby("num_parc"):
        last_seen = group["timestamp"].max()
        hours_since_last_seen = (now - last_seen).total_seconds() / 3600

        exploitation_case, last_exploitation = get_exploitation_case(group)
        reference_time = last_exploitation or now
        hours_since_exploitation = (
            (now - last_exploitation).total_seconds() / 3600 if last_exploitation is not None else None
        )

        force_fonctionnel = exploitation_case == "stale"

        sae_present = group["num_parc_sae"].notna()
        gps_present = group["latitude"].notna() & group["longitude"].notna()

        sae = _field_status(group, now, reference_time, sae_present, SAE_SILENCE_THRESHOLD_HOURS, SAE_MISSING_RATIO_THRESHOLD, force_fonctionnel=force_fonctionnel)
        gps = _field_status(group, now, reference_time, gps_present, GPS_SILENCE_THRESHOLD_HOURS, GPS_MISSING_RATIO_THRESHOLD, force_fonctionnel=force_fonctionnel)

        results.append({
            "num_parc": num_parc,
            "last_seen": to_local_iso(last_seen),
            "hours_since_last_seen": round(hours_since_last_seen, 1),
            "last_exploitation": to_local_iso(last_exploitation),
            "hours_since_last_exploitation": round(hours_since_exploitation, 1) if hours_since_exploitation is not None else None,
            "exploitation_case": exploitation_case,
            "sae": sae,
            "gps": gps,
        })

    return sorted(results, key=lambda v: v["num_parc"])
