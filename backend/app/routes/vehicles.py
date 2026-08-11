"""
API routes for vehicle supervision.

CDC mapping:
- GET /api/vehicles        -> 3.1 Vue globale du parc
- GET /api/vehicles/{id}   -> 3.2 Consultation détaillée d'un véhicule
- GET /api/history/{id}    -> 3.3 Historique des remontées
- GET /api/vehicles/{id}/live -> targeted post-intervention check (short polling)
"""

from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, date

from app.database import fetch_metrics, fetch_door_counts
from app.anomaly import get_vehicle_overview, get_door_status_for_vehicle

router = APIRouter(prefix="/api", tags=["vehicles"])


@router.get("/vehicles")
def list_vehicles(status: str | None = Query(default=None, description="fonctionnel | anomalie")):
    """CDC 3.1 + 5: global fleet view, optionally filtered by status."""
    metrics_df = fetch_metrics()
    vehicles = get_vehicle_overview(metrics_df)

    if status:
        vehicles = [v for v in vehicles if v["status"] == status]

    return {"vehicles": vehicles}


@router.get("/vehicles/{num_parc}")
def get_vehicle_detail(num_parc: int):
    """CDC 3.2: detailed view for one vehicle, including per-door status."""
    metrics_df = fetch_metrics()
    door_df = fetch_door_counts()

    overview = get_vehicle_overview(metrics_df)
    vehicle = next((v for v in overview if v["num_parc"] == num_parc), None)

    if vehicle is None:
        raise HTTPException(status_code=404, detail="Véhicule introuvable")

    doors = get_door_status_for_vehicle(door_df, num_parc)

    return {
        "num_parc": num_parc,
        "last_seen": vehicle["last_seen"],
        "status": vehicle["status"],
        "doors": doors,
    }


@router.get("/vehicles/{num_parc}/live")
def check_vehicle_live(num_parc: int):
    """
    Lightweight, on-demand check used by the front-end's
    "vérification post-intervention" mode. Not used for the global view.
    """
    return get_vehicle_detail(num_parc)


@router.get("/history/{num_parc}")
def get_vehicle_history(
    num_parc: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
):
    """CDC 3.3: reporting history over a given period (max 2 months of raw data available)."""
    door_df = fetch_door_counts()
    vehicle_df = door_df[door_df["num_parc"] == num_parc].copy()

    if start_date:
        vehicle_df = vehicle_df[vehicle_df["timestamp"].dt.date >= start_date]
    if end_date:
        vehicle_df = vehicle_df[vehicle_df["timestamp"].dt.date <= end_date]

    vehicle_df = vehicle_df.dropna(subset=["timestamp"]).sort_values("timestamp")

    # Only report *that* a reading happened at a given time, never the counts themselves.
    timestamps = vehicle_df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist()

    return {"num_parc": num_parc, "reports": timestamps}
