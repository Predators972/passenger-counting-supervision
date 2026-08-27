## @file vehicles.py
#  @brief API endpoints for the fleet overview, vehicle detail, reporting
#  history, and SAE/GPS status. Orchestrates database.py (data access) and
#  anomaly.py (status computation) and shapes the JSON responses consumed
#  by the front-end.

from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, date, timedelta
import pandas as pd

from app.database import (
    fetch_metrics, fetch_metrics_for_vehicle, fetch_door_counts_for_vehicle,
    fetch_door_last_seen_aggregate, fetch_metrics_sae_gps, fetch_metrics_sae_gps_for_vehicle,
    get_door_columns, utc_now, to_local_iso,
)
from app.config import DOOR_ANOMALY_THRESHOLD_HOURS
from app.anomaly import (
    get_vehicle_overview, get_door_status_for_vehicle, get_last_exploitation_time,
    get_last_exploitation_per_vehicle, get_sae_gps_status, get_exploitation_case,
    get_exploitation_case_per_vehicle,
)
from app.fleet_reference import get_rolling_stock, get_physical_door_number, is_known_vehicle

router = APIRouter(prefix="/api", tags=["vehicles"])


@router.get("/vehicles")
def list_vehicles(status: str | None = Query(default=None, description="fonctionnel | anomalie")):
    """!
    @brief Return the fleet overview: one entry per known vehicle with its
    oldest door signal, last exploitation reference and overall status.

    For each vehicle, "last_seen" is the oldest last-seen timestamp among
    its doors, "last_exploitation" is the last time operation_state showed
    the vehicle in service, and "status" is "anomalie" if at least one door
    is currently in anomaly (see the per-door rules applied below,
    consistent with get_door_status_for_vehicle).

    @param status Optional filter, "fonctionnel" or "anomalie".
    @return JSON object with a "vehicles" list; each entry has num_parc,
    last_seen, hours_since_last_seen, last_exploitation,
    hours_since_last_exploitation, exploitation_case, status,
    rolling_stock_category, rolling_stock_type, door_count_functional,
    door_count_total, status_warning (true when a vehicle not seen in
    commercial service for over 30 days has a door with no data at all -
    shown as "fonctionnel" but flagged for a distinct visual treatment).
    """
    metrics_df = fetch_metrics()
    vehicles = get_vehicle_overview(metrics_df)

    vehicles = [v for v in vehicles if is_known_vehicle(v["num_parc"])]

    for v in vehicles:
        rolling_stock = get_rolling_stock(v["num_parc"])
        v["rolling_stock_category"] = rolling_stock["category"]
        v["rolling_stock_type"] = f"{rolling_stock['category']} - {rolling_stock['type']}"

    now = utc_now()

    exploitation_case_map = get_exploitation_case_per_vehicle(metrics_df)
    door_agg_df = fetch_door_last_seen_aggregate()
    door_agg_by_vehicle = {row["num_parc"]: row for _, row in door_agg_df.iterrows()}

    for v in vehicles:
        exploitation_case, last_exp = exploitation_case_map.get(v["num_parc"], ("unknown", None))
        v["hours_since_last_exploitation"] = (
            round((now - last_exp).total_seconds() / 3600, 1) if last_exp is not None else None
        )
        v["last_exploitation"] = to_local_iso(last_exp)
        v["exploitation_case"] = exploitation_case

        rolling_stock = get_rolling_stock(v["num_parc"])
        agg_row = door_agg_by_vehicle.get(v["num_parc"])

        minimum_doors = rolling_stock["minimum_doors"] if rolling_stock else 0
        if rolling_stock and rolling_stock["door_count"] is not None:
            candidate_doors = range(1, rolling_stock["door_count"] + 1)
        elif agg_row is not None:
            reported_doors = {n for n in range(1, 17) if pd.notna(agg_row.get(f"p{n}_last"))}
            candidate_doors = sorted(reported_doors | set(range(1, minimum_doors + 1)))
        else:
            candidate_doors = range(1, minimum_doors + 1)

        door_last_seen = {}
        for n in candidate_doors:
            ts = agg_row.get(f"p{n}_last") if agg_row is not None else None
            door_last_seen[n] = pd.Timestamp(ts) if pd.notna(ts) else None

        door_count_functional = 0
        has_missing_door = False
        for n, ts in door_last_seen.items():
            if ts is None:
                if exploitation_case == "stale":
                    has_missing_door = True
                continue
            if exploitation_case == "stale":
                door_count_functional += 1
            elif exploitation_case == "unknown" or last_exp is None:
                hours_since_now = (now - ts).total_seconds() / 3600
                if hours_since_now <= DOOR_ANOMALY_THRESHOLD_HOURS:
                    door_count_functional += 1
            else:
                hours_behind_exploitation = (last_exp - ts).total_seconds() / 3600
                if hours_behind_exploitation <= DOOR_ANOMALY_THRESHOLD_HOURS:
                    door_count_functional += 1

        door_count_total = len(door_last_seen)
        v["door_count_functional"] = door_count_functional
        v["door_count_total"] = door_count_total

        door_timestamps = [ts for ts in door_last_seen.values() if ts is not None]
        oldest_door_ts = min(door_timestamps) if door_timestamps else None

        v["last_seen"] = to_local_iso(oldest_door_ts)
        v["hours_since_last_seen"] = (
            round((now - oldest_door_ts).total_seconds() / 3600, 1) if oldest_door_ts is not None else None
        )

        if exploitation_case == "stale" and has_missing_door:
            # A door with zero data at all would normally make the vehicle
            # "anomalie", but for a vehicle not seen in commercial service
            # for over 30 days, this is ambiguous rather than a confirmed
            # fault - shown as "fonctionnel" with a distinct visual warning
            # instead of a hard anomaly.
            v["status"] = "fonctionnel"
            v["status_warning"] = True
        else:
            v["status"] = "anomalie" if door_count_functional < door_count_total else "fonctionnel"
            v["status_warning"] = False

    if status:
        vehicles = [v for v in vehicles if v["status"] == status]

    return {"vehicles": vehicles}


@router.get("/vehicles/{num_parc}")
def get_vehicle_detail(num_parc: int):
    """!
    @brief Return the detailed status of one vehicle, including per-door
    status, rolling stock type and functional/total door count.

    The overall "status" is "anomalie" if the vehicle itself hasn't
    reported recently, or if at least one door is in anomaly.

    @param num_parc Vehicle number.
    @return JSON object with num_parc, last_seen, hours_since_last_seen,
    status, last_exploitation, hours_since_last_exploitation,
    exploitation_case, rolling_stock_type, door_count_functional,
    door_count_total, doors (list of per-door status dicts).
    @exception HTTPException 404 if the vehicle number is unknown or has
    no data in the lookback window.
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
    expected_doors = None
    if rolling_stock and rolling_stock["door_count"] is not None:
        expected_doors = list(range(1, rolling_stock["door_count"] + 1))

    exploitation_case, last_exploitation = get_exploitation_case(metrics_df)
    reference_time = last_exploitation or utc_now()

    doors = get_door_status_for_vehicle(
        door_df, num_parc, reference_time,
        expected_doors=expected_doors,
        minimum_doors=rolling_stock["minimum_doors"] if rolling_stock else 0,
        skip_silence_check=(exploitation_case == "stale"),
    )
    functional_count = sum(1 for d in doors if d["status"] == "fonctionnel")

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
        "exploitation_case": exploitation_case,
        "rolling_stock_type": f"{rolling_stock['category']} - {rolling_stock['type']}" if rolling_stock else "Type inconnu (non configuré)",
        "door_count_functional": functional_count,
        "door_count_total": len(doors),
        "doors": doors,
    }


@router.get("/vehicles/{num_parc}/live")
def check_vehicle_live(num_parc: int):
    """!
    @brief Lightweight re-check of one vehicle's detail, used to confirm a
    door has recovered after a repair.

    @param num_parc Vehicle number.
    @return Same response shape as get_vehicle_detail.
    """
    return get_vehicle_detail(num_parc)


@router.get("/history/{num_parc}")
def get_vehicle_history(
    num_parc: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    door: int | None = Query(default=None, description="Numéro de porte physique (ex: 11, 31...)"),
):
    """!
    @brief Return the door reporting history of one vehicle over a period.

    Each entry represents a single door's report; a door_counts row with
    several doors reporting at once produces several entries sharing the
    same timestamp.

    @param num_parc Vehicle number.
    @param start_date Optional inclusive lower bound on the report date.
    @param end_date Optional inclusive upper bound on the report date.
    @param door Optional physical door number to filter on.
    @return JSON object with num_parc and a "reports" list of
    {timestamp, porte} entries, sorted from the most recent to the oldest.
    @exception HTTPException 404 if the vehicle number is unknown.
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


@router.get("/vehicles-sae-gps")
def list_vehicles_sae_gps():
    """!
    @brief Return the SAE and GPS status of every known vehicle.

    @return JSON object with a "vehicles" list; each entry has num_parc,
    last_seen, hours_since_last_seen, last_exploitation,
    hours_since_last_exploitation, exploitation_case, rolling_stock_category, rolling_stock_type,
    sae and gps (status dicts as returned by anomaly.get_sae_gps_status).
    """
    metrics_df = fetch_metrics_sae_gps()
    vehicles = get_sae_gps_status(metrics_df)

    vehicles = [v for v in vehicles if is_known_vehicle(v["num_parc"])]
    for v in vehicles:
        rolling_stock = get_rolling_stock(v["num_parc"])
        v["rolling_stock_category"] = rolling_stock["category"]
        v["rolling_stock_type"] = f"{rolling_stock['category']} - {rolling_stock['type']}"

    return {"vehicles": vehicles}


@router.get("/history-sae-gps/{num_parc}")
def get_vehicle_history_sae_gps(
    num_parc: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
):
    """!
    @brief Return the SAE/GPS presence history of one vehicle over a
    period, one entry per metrics report.

    @param num_parc Vehicle number.
    @param start_date Optional inclusive lower bound on the report date.
    @param end_date Optional inclusive upper bound on the report date.
    @return JSON object with num_parc and a "reports" list of
    {timestamp, sae_present, gps_present} entries, sorted from the most
    recent to the oldest.
    @exception HTTPException 404 if the vehicle number is unknown.
    """
    if not is_known_vehicle(num_parc):
        raise HTTPException(status_code=404, detail="Véhicule introuvable")

    df = fetch_metrics_sae_gps_for_vehicle(num_parc)

    if start_date:
        df = df[df["timestamp"].dt.date >= start_date]
    if end_date:
        df = df[df["timestamp"].dt.date <= end_date]

    df = df.dropna(subset=["timestamp"]).sort_values("timestamp", ascending=False)

    reports = [
        {
            "timestamp": to_local_iso(row["timestamp"]),
            "sae_present": bool(pd.notna(row["num_parc_sae"])),
            "gps_present": bool(pd.notna(row["latitude"]) and pd.notna(row["longitude"])),
        }
        for _, row in df.iterrows()
    ]

    return {"num_parc": num_parc, "reports": reports}
