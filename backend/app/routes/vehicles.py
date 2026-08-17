"""
API routes for vehicle supervision.

CDC mapping:
- GET /api/vehicles        -> 3.1 Vue globale du parc
- GET /api/vehicles/{id}   -> 3.2 Consultation détaillée d'un véhicule
- GET /api/history/{id}    -> 3.3 Historique des remontées
- GET /api/vehicles/{id}/live -> targeted post-intervention check (short polling)
"""

from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, date, timedelta
import pandas as pd

from app.database import (
    fetch_metrics, fetch_metrics_for_vehicle, fetch_door_counts_for_vehicle,
    fetch_door_last_seen_aggregate, get_door_columns, utc_now, to_local_iso,
)
from app.config import DOOR_ANOMALY_THRESHOLD_HOURS
from app.anomaly import get_vehicle_overview, get_door_status_for_vehicle, get_last_exploitation_time, get_last_exploitation_per_vehicle
from app.fleet_reference import get_rolling_stock, get_physical_door_number, is_known_vehicle

router = APIRouter(prefix="/api", tags=["vehicles"])


@router.get("/vehicles")
def list_vehicles(status: str | None = Query(default=None, description="fonctionnel | anomalie")):
    """
    CDC 3.1 + 5: global fleet view, optionally filtered by status.

    Per vehicle, this exposes exactly three pieces of information:
    1. "last_seen" = the OLDEST last-seen timestamp among all its doors
       (the "weakest link" - if any door is stale, this reflects it).
    2. "last_exploitation" = the last time operation_state was 1 or 2
       (genuinely in service).
    3. "status": anomalie if (last_exploitation - last_seen) > 48h, i.e.
       the door(s) went silent more than 2 days before the vehicle's last
       real exploitation and never reported again since. If last_seen is
       AFTER last_exploitation (e.g. a maintainer walking past a sensor
       while the vehicle was idle at the depot), that's never an anomaly,
       no matter how long ago that report was - it proves the door works.

    The door lookup only scans a 30-day window (not the full 60-day floor
    used elsewhere) as a cost/thoroughness compromise for a fleet-wide
    query - see fetch_door_last_seen_aggregate.
    """
    metrics_df = fetch_metrics()
    vehicles = get_vehicle_overview(metrics_df)

    # Vehicle numbers outside all configured rolling stock ranges are treated
    # as a BDD3 data quality issue and excluded entirely (business decision).
    vehicles = [v for v in vehicles if is_known_vehicle(v["num_parc"])]

    for v in vehicles:
        v["rolling_stock_type"] = get_rolling_stock(v["num_parc"])["type"]

    now = utc_now()

    last_exploitation_map = get_last_exploitation_per_vehicle(metrics_df)
    door_agg_df = fetch_door_last_seen_aggregate()
    door_agg_by_vehicle = {row["num_parc"]: row for _, row in door_agg_df.iterrows()}

    for v in vehicles:
        last_exp = last_exploitation_map.get(v["num_parc"])
        v["hours_since_last_exploitation"] = (
            round((now - last_exp).total_seconds() / 3600, 1) if last_exp is not None else None
        )
        v["last_exploitation"] = to_local_iso(last_exp)

        rolling_stock = get_rolling_stock(v["num_parc"])
        agg_row = door_agg_by_vehicle.get(v["num_parc"])

        # Known door count -> we can tell "missing" from "doesn't exist".
        # Unknown (e.g. buses) -> doors within the known minimum floor are
        # always checked (even with zero data, per fleet_reference.py -
        # every bus has at least that many doors), doors above the floor
        # are only checked if they've reported at least once (same fallback
        # limitation as the detail view for anything beyond the floor).
        minimum_doors = rolling_stock["minimum_doors"] if rolling_stock else 0
        if rolling_stock and rolling_stock["door_count"] is not None:
            candidate_doors = range(1, rolling_stock["door_count"] + 1)
        elif agg_row is not None:
            reported_doors = {n for n in range(1, 17) if pd.notna(agg_row.get(f"p{n}_last"))}
            candidate_doors = sorted(reported_doors | set(range(1, minimum_doors + 1)))
        else:
            candidate_doors = range(1, minimum_doors + 1)

        door_timestamps = []
        has_missing_door = False
        for n in candidate_doors:
            ts = agg_row.get(f"p{n}_last") if agg_row is not None else None
            if pd.isna(ts):
                has_missing_door = True
            else:
                door_timestamps.append(pd.Timestamp(ts))

        oldest_door_ts = min(door_timestamps) if door_timestamps else None

        v["last_seen"] = to_local_iso(oldest_door_ts)
        v["hours_since_last_seen"] = (
            round((now - oldest_door_ts).total_seconds() / 3600, 1) if oldest_door_ts is not None else None
        )

        if oldest_door_ts is None:
            # No door data at all within the 30-day window: can't have been
            # in genuine service recently either way - anomalie.
            v["status"] = "anomalie"
        elif has_missing_door:
            # At least one expected door has zero data in the window - a
            # door that's been silent for over 30 days is certainly stale
            # relative to any exploitation within that window.
            v["status"] = "anomalie"
        elif last_exp is None:
            # No exploitation reference available in the fetched window -
            # fall back to comparing the oldest door report to now.
            hours_since_now = (now - oldest_door_ts).total_seconds() / 3600
            v["status"] = "anomalie" if hours_since_now > DOOR_ANOMALY_THRESHOLD_HOURS else "fonctionnel"
        else:
            hours_behind_exploitation = (last_exp - oldest_door_ts).total_seconds() / 3600
            v["status"] = "anomalie" if hours_behind_exploitation > DOOR_ANOMALY_THRESHOLD_HOURS else "fonctionnel"

    if status:
        vehicles = [v for v in vehicles if v["status"] == status]

    return {"vehicles": vehicles}


@router.get("/vehicles/{num_parc}")
def get_vehicle_detail(num_parc: int):
    """
    CDC 3.2: detailed view for one vehicle, including per-door status,
    rolling stock type and functional/total door count.

    The overall "status" is "anomalie" if either the vehicle itself (WEBOX)
    hasn't reported recently, OR at least one door is in anomaly - a door
    with zero data at all must never be silently counted as "fonctionnel".

    Always fetches the full lookback window (metrics AND door_counts) for
    this one vehicle - already fast since both queries are filtered to a
    single num_parc in SQL. An earlier "since" narrowing optimization was
    removed: it caused doors with real, older-than-"since" data to
    incorrectly show "Aucune donnée" when navigating from the global view,
    since the hint value wasn't always old enough to cover them.
    """
    if not is_known_vehicle(num_parc):
        raise HTTPException(status_code=404, detail="Véhicule introuvable")

    metrics_df = fetch_metrics_for_vehicle(num_parc)
    door_df = fetch_door_counts_for_vehicle(num_parc)

    overview = get_vehicle_overview(metrics_df)
    vehicle = overview[0] if overview else None

    if vehicle is None:
        raise HTTPException(status_code=404, detail="Véhicule introuvable")

    rolling_stock = get_rolling_stock(num_parc)
    # door_count can be None (e.g. buses, whose door count varies by model) -
    # in that case we fall back to dynamic door-count detection.
    expected_doors = None
    if rolling_stock and rolling_stock["door_count"] is not None:
        expected_doors = list(range(1, rolling_stock["door_count"] + 1))

    # Compare each door's last report to the vehicle's last genuine
    # exploitation, not to "now" - see anomaly.get_door_status_for_vehicle.
    last_exploitation = get_last_exploitation_time(metrics_df)
    reference_time = last_exploitation or utc_now()

    doors = get_door_status_for_vehicle(
        door_df, num_parc, reference_time,
        expected_doors=expected_doors,
        minimum_doors=rolling_stock["minimum_doors"] if rolling_stock else 0,
    )
    functional_count = sum(1 for d in doors if d["status"] == "fonctionnel")

    # A door with zero data, or genuinely stale relative to reference_time,
    # must make the whole vehicle "anomalie" - it was previously possible
    # for the vehicle-level status (WEBOX-only) to say "fonctionnel" while
    # a door showed "Aucune donnée".
    overall_status = vehicle["status"]
    if any(d["status"] == "anomalie" for d in doors):
        overall_status = "anomalie"

    door_scheme = rolling_stock["door_scheme"] if rolling_stock else None
    for d in doors:
        d["porte_physique"] = get_physical_door_number(door_scheme, d["porte"])

    now = utc_now()
    last_exploitation_hours = (now - last_exploitation).total_seconds() / 3600 if last_exploitation else None

    return {
        "num_parc": num_parc,
        "last_seen": to_local_iso(pd.Timestamp(vehicle["last_seen"])),
        "hours_since_last_seen": vehicle["hours_since_last_seen"],
        "status": overall_status,
        "last_exploitation": to_local_iso(last_exploitation),
        "hours_since_last_exploitation": round(last_exploitation_hours, 1) if last_exploitation_hours is not None else None,
        "rolling_stock_type": rolling_stock["type"] if rolling_stock else "Type inconnu (non configuré)",
        "door_count_functional": functional_count,
        "door_count_total": len(doors),
        "doors": doors,
    }


@router.get("/vehicles/{num_parc}/live")
def check_vehicle_live(num_parc: int):
    """
    Lightweight, on-demand check used by the front-end's
    "vérification post-intervention" mode. Just re-runs the (already fast,
    per-vehicle) detail lookup - no separate narrowing needed.
    """
    return get_vehicle_detail(num_parc)


@router.get("/history/{num_parc}")
def get_vehicle_history(
    num_parc: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    door: int | None = Query(default=None, description="Numéro de porte physique (ex: 11, 31...)"),
):
    """
    CDC 3.3: reporting history over a given period (max 2 months of raw
    data available). Each entry is a single door's report (a row in
    door_counts can have several doors reporting at once, in which case it
    produces several entries with the same timestamp). Sorted from the
    most recent to the oldest.

    "door" filters to a single physical door number (as shown in the UI,
    e.g. 11, 31...), useful to investigate a specific door known to be
    faulty without the noise of the other doors' reports.
    """
    if not is_known_vehicle(num_parc):
        raise HTTPException(status_code=404, detail="Véhicule introuvable")

    door_df = fetch_door_counts_for_vehicle(num_parc)

    if start_date:
        door_df = door_df[door_df["timestamp"].dt.date >= start_date]
    if end_date:
        door_df = door_df[door_df["timestamp"].dt.date <= end_date]

    door_df = door_df.dropna(subset=["timestamp"])

    rolling_stock = get_rolling_stock(num_parc)
    door_scheme = rolling_stock["door_scheme"] if rolling_stock else None
    door_numbers = (
        list(range(1, rolling_stock["door_count"] + 1))
        if rolling_stock and rolling_stock["door_count"] is not None
        else get_door_columns(door_df)
    )

    entries = []
    for door_num in door_numbers:
        physical_door = get_physical_door_number(door_scheme, door_num)
        if door is not None and physical_door != door:
            continue

        cols = [c for c in (f"P{door_num}_IN", f"P{door_num}_OUT") if c in door_df.columns]
        if not cols:
            continue
        reported = door_df[door_df[cols].notna().any(axis=1)]
        for ts in reported["timestamp"]:
            entries.append({"timestamp": ts, "porte": physical_door})

    entries.sort(key=lambda e: e["timestamp"], reverse=True)

    reports = [
        {"timestamp": to_local_iso(e["timestamp"]), "porte": e["porte"]}
        for e in entries
    ]

    return {"num_parc": num_parc, "reports": reports}
