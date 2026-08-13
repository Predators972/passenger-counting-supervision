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

from app.config import VEHICLE_ANOMALY_THRESHOLD_HOURS
from app.database import fetch_metrics, fetch_metrics_for_vehicle, fetch_door_counts_for_vehicle, fetch_door_last_seen_aggregate, get_door_columns
from app.anomaly import get_vehicle_overview, get_door_status_for_vehicle, get_last_exploitation_time, get_last_exploitation_per_vehicle
from app.fleet_reference import get_rolling_stock, get_physical_door_number, is_known_vehicle

router = APIRouter(prefix="/api", tags=["vehicles"])


@router.get("/vehicles")
def list_vehicles(status: str | None = Query(default=None, description="fonctionnel | anomalie")):
    """
    CDC 3.1 + 5: global fleet view, optionally filtered by status.

    "Dernière remontée" reflects whichever is OLDER between the vehicle's
    own last report (metrics) and the oldest last-seen among its doors
    (door_counts) - so a single dead door among many working ones still
    surfaces here as an anomaly, not just a fully silent WEBOX.
    """
    metrics_df = fetch_metrics()
    vehicles = get_vehicle_overview(metrics_df)

    # Vehicle numbers outside all configured rolling stock ranges are treated
    # as a BDD3 data quality issue and excluded entirely (business decision).
    vehicles = [v for v in vehicles if is_known_vehicle(v["num_parc"])]

    for v in vehicles:
        v["rolling_stock_type"] = get_rolling_stock(v["num_parc"])["type"]

    now = datetime.now()

    last_exploitation_map = get_last_exploitation_per_vehicle(metrics_df)
    for v in vehicles:
        last_exp = last_exploitation_map.get(v["num_parc"])
        if last_exp is not None:
            v["last_exploitation"] = last_exp.isoformat()
            v["hours_since_last_exploitation"] = round((now - last_exp).total_seconds() / 3600, 1)
        else:
            v["last_exploitation"] = None
            v["hours_since_last_exploitation"] = None

    door_agg_df = fetch_door_last_seen_aggregate()
    door_agg_by_vehicle = {row["num_parc"]: row for _, row in door_agg_df.iterrows()}
    door_cols = [f"p{n}_last" for n in range(1, 17)]

    for v in vehicles:
        agg_row = door_agg_by_vehicle.get(v["num_parc"])
        if agg_row is None:
            continue

        door_timestamps = [pd.Timestamp(agg_row[c]) for c in door_cols if pd.notna(agg_row.get(c))]
        if not door_timestamps:
            continue

        oldest_door_ts = min(door_timestamps).to_pydatetime()
        vehicle_ts = datetime.fromisoformat(v["last_seen"])
        worst_ts = min(vehicle_ts, oldest_door_ts)

        if worst_ts < vehicle_ts:
            hours_since = (now - worst_ts).total_seconds() / 3600
            v["last_seen"] = worst_ts.isoformat()
            v["hours_since_last_seen"] = round(hours_since, 1)
            if hours_since > VEHICLE_ANOMALY_THRESHOLD_HOURS:
                v["status"] = "anomalie"

    if status:
        vehicles = [v for v in vehicles if v["status"] == status]

    return {"vehicles": vehicles}


@router.get("/vehicles/{num_parc}")
def get_vehicle_detail(num_parc: int, since: datetime | None = Query(default=None)):
    """
    CDC 3.2: detailed view for one vehicle, including per-door status,
    rolling stock type and functional/total door count.

    "since" is an optional optimization: if the caller already knows this
    vehicle's last known report time (e.g. from the global view, already
    loaded in the front-end), passing it here narrows the query instead of
    always scanning the full lookback window - see database.py.
    """
    if not is_known_vehicle(num_parc):
        raise HTTPException(status_code=404, detail="Véhicule introuvable")

    metrics_df = fetch_metrics_for_vehicle(num_parc, since=since)
    door_df = fetch_door_counts_for_vehicle(num_parc, since=since)

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
    reference_time = last_exploitation or datetime.now()

    doors = get_door_status_for_vehicle(door_df, num_parc, reference_time, expected_doors=expected_doors)
    functional_count = sum(1 for d in doors if d["status"] == "fonctionnel")

    door_scheme = rolling_stock["door_scheme"] if rolling_stock else None
    for d in doors:
        d["porte_physique"] = get_physical_door_number(door_scheme, d["porte"])

    now = datetime.now()
    last_exploitation_hours = (now - last_exploitation).total_seconds() / 3600 if last_exploitation else None

    return {
        "num_parc": num_parc,
        "last_seen": vehicle["last_seen"],
        "hours_since_last_seen": vehicle["hours_since_last_seen"],
        "status": vehicle["status"],
        "last_exploitation": last_exploitation.isoformat() if last_exploitation else None,
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
    "vérification post-intervention" mode. Only looks back 2 days, since
    we're specifically watching for a brand-new report after a repair.
    """
    since = datetime.now() - timedelta(days=2)
    return get_vehicle_detail(num_parc, since=since)


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
        {"timestamp": e["timestamp"].strftime("%Y-%m-%dT%H:%M:%S"), "porte": e["porte"]}
        for e in entries
    ]

    return {"num_parc": num_parc, "reports": reports}
