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
    EXPLOITATION_STATES, SAE_SILENCE_THRESHOLD_HOURS, SAE_MISSING_RATIO_THRESHOLD,
    GPS_SILENCE_THRESHOLD_HOURS, GPS_MISSING_RATIO_THRESHOLD,
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


def get_exploitation_case(metrics_df: pd.DataFrame):
    """
    Determine which of three cases applies to a vehicle's exploitation
    reference, based on operation_state over the fetched window:

    - "known": at least one row has operation_state 1 or 2 -> a real
      last_exploitation timestamp is available, used as normal.
    - "stale": operation_state HAS been reported (e.g. 6 = hors service
      dépôt) but never 1 or 2 within the window - the vehicle almost
      certainly last ran before the window started. We know for a fact the
      SAE trame itself is being received (operation_state came from it),
      just not a comparable "since when" reference - so silence-based
      checks (doors, SAE, GPS) must be skipped entirely for this vehicle;
      only "zero data at all" and the SAE/GPS missing-ratio (where still
      meaningful) remain valid signals.
    - "unknown": operation_state has never been reported at all in the
      window - genuine SAE silence, not just a long depot stay. Existing
      "compare to now" fallback behavior still applies.

    Returns (case, last_exploitation) - last_exploitation is only non-None
    for the "known" case.
    """
    last_exploitation = get_last_exploitation_time(metrics_df)
    if last_exploitation is not None:
        return "known", last_exploitation
    if "operation_state" in metrics_df.columns and metrics_df["operation_state"].notna().any():
        return "stale", None
    return "unknown", None


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


def get_exploitation_case_per_vehicle(metrics_df: pd.DataFrame) -> dict:
    """
    Same as get_exploitation_case, but for every vehicle in metrics_df at
    once (used by the global fleet view). Returns
    {num_parc: (case, last_exploitation)}.
    """
    if "operation_state" not in metrics_df.columns:
        return {}

    result = {}
    has_state = metrics_df.groupby("num_parc")["operation_state"].apply(lambda s: s.notna().any())
    last_exploitation_map = get_last_exploitation_per_vehicle(metrics_df)

    for num_parc in metrics_df["num_parc"].dropna().unique():
        last_exp = last_exploitation_map.get(num_parc)
        if last_exp is not None:
            result[num_parc] = ("known", last_exp)
        elif has_state.get(num_parc, False):
            result[num_parc] = ("stale", None)
        else:
            result[num_parc] = ("unknown", None)

    return result


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
    minimum_doors: int = 0,
    skip_silence_check: bool = False,
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

    skip_silence_check: set when get_exploitation_case() returned "stale"
    (operation_state was reported, e.g. depot, but never 1/2 in the window -
    so there's no usable reference_time). In that case ANY door with at
    least one report in the window is "fonctionnel" regardless of how old -
    only a door with ZERO data at all stays "anomalie". A vehicle parked for
    a long time shouldn't have its doors flagged just because it hasn't
    driven; once a maintainer triggers a genuinely dead door, it will show
    up immediately without needing the vehicle back in service first.

    Passenger volume values (PX_IN / PX_OUT) are used only to detect the
    last non-null timestamp per door - they are never included in the output.
    """
    vehicle_df = door_counts_df[door_counts_df["num_parc"] == num_parc]

    if expected_doors is not None:
        candidate_doors = expected_doors
    else:
        # Dynamic mode (rolling stock type without a fixed door_count, e.g.
        # buses): a door that never reported in the fetched window is
        # ambiguous - it might not exist on this vehicle, or it might be
        # genuinely dead. minimum_doors is a known floor (e.g. "every bus
        # has at least 2 doors") that resolves this for the doors we're
        # SURE exist: they're always included below, even with zero data,
        # rather than being silently dropped as if they didn't exist.
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
        # Fallback mode: drop doors that never reported AND are above the
        # guaranteed minimum floor (see docstring - still can't tell
        # "silent" from "doesn't exist" for those beyond the known floor).
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


def _field_status(group: pd.DataFrame, now, reference_time, present_mask: pd.Series, silence_threshold: float, ratio_threshold: float, force_fonctionnel: bool = False):
    """
    Shared logic for get_sae_gps_status: given a vehicle's metrics rows and a
    boolean mask of which rows have the field present, compute:
    - last_present: last timestamp where the field was present (None if never)
    - hours_since_last_seen: displayed duration, always relative to "now"
      (for consistency with the rest of the UI)
    - missing_ratio: share of rows (over the fetched window) missing the field
    - status: "anomalie" if EITHER:
        - the field's last presence is more than `silence_threshold` hours
          BEFORE reference_time (the vehicle's last genuine exploitation) -
          same pattern as door anomaly detection: a field present again
          AFTER reference_time is never an anomaly, no matter how long ago,
        - OR the missing ratio over the window exceeds `ratio_threshold`
          (degraded mode).

    force_fonctionnel: set when get_exploitation_case() returned "stale" -
    the vehicle hasn't been in genuine service within the window (e.g. long
    depot stay), so neither the silence check nor the missing-ratio check
    is meaningful (not enough - or not representative - in-service data to
    judge). Always "fonctionnel" in that case; last_seen/missing_ratio are
    still computed and returned for information.
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
    """
    CDC 4.3 (absence de trame SAE) / 4.4 (anomalie GPS) - one entry per
    vehicle, combining a silence-duration check (relative to the vehicle's
    last genuine exploitation, not to "now" - see _field_status) and a
    "degraded mode" ratio check for each of num_parc_sae (SAE) and
    latitude/longitude (GPS).

    metrics_df must include num_parc_sae, latitude, longitude AND
    operation_state - see database.fetch_metrics_sae_gps (a separate,
    dedicated query from the one used by the global fleet view).
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

        # "stale": operation_state seen (e.g. depot) but never 1/2 in the
        # window - not enough/representative in-service data to judge either
        # field, so both are forced "fonctionnel" (see _field_status).
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
