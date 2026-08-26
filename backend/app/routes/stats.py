## @file stats.py
#  @brief API endpoint for the "anomalies qui traînent" section of the
#  statistics tab: re-verifies ambiguous or vanished vehicles against a
#  wider 60-day window.

from fastapi import APIRouter
from app.database import fetch_metrics, fetch_metrics_for_vehicle, fetch_door_counts_for_vehicle, utc_now
from app.anomaly import (
    get_vehicle_overview, get_exploitation_case_per_vehicle, get_exploitation_case,
    get_door_status_for_vehicle,
)
from app.fleet_reference import get_rolling_stock, is_known_vehicle

router = APIRouter(prefix="/api/stats", tags=["stats"])

## Number of days to look back when re-verifying lingering-anomaly
#  candidates, wider than the standard lookback window used elsewhere.
EXTENDED_LOOKBACK_DAYS = 60


@router.get("/lingering")
def get_lingering_anomalies():
    """!
    @brief Identify vehicles with a long-standing anomaly, re-verified over
    a 60-day window.

    Candidates are vehicles whose exploitation reference is ambiguous
    ("stale" or "unknown") in the standard window, plus vehicles present in
    the 30-60 day window but absent from the standard one. Each candidate
    is re-evaluated with the wider window and excluded if it turns out
    "fonctionnel".

    @return JSON object with a "vehicles" list of vehicle numbers confirmed
    to still be in anomaly.
    """
    metrics_30 = fetch_metrics()
    primary_overview = [v for v in get_vehicle_overview(metrics_30) if is_known_vehicle(v["num_parc"])]
    primary_num_parcs = {v["num_parc"] for v in primary_overview}

    exploitation_case_map = get_exploitation_case_per_vehicle(metrics_30)
    candidates = {
        num_parc for num_parc, (case, _) in exploitation_case_map.items()
        if case in ("stale", "unknown") and num_parc in primary_num_parcs
    }

    metrics_60 = fetch_metrics(days_back=EXTENDED_LOOKBACK_DAYS)
    all_known_60 = {n for n in metrics_60["num_parc"].dropna().unique() if is_known_vehicle(n)}
    invisible = all_known_60 - primary_num_parcs

    all_candidates = candidates | invisible

    # num_parc values here come from pandas (.unique() / dict keys built
    # from a DataFrame) and are numpy.int64, not native Python int - cast
    # to plain int so they can be used as SQL query parameters.
    lingering = []
    for num_parc in sorted(int(n) for n in all_candidates):
        metrics_v = fetch_metrics_for_vehicle(num_parc, max_days_back=EXTENDED_LOOKBACK_DAYS)
        door_v = fetch_door_counts_for_vehicle(num_parc, max_days_back=EXTENDED_LOOKBACK_DAYS)

        overview = get_vehicle_overview(metrics_v)
        if not overview:
            lingering.append(int(num_parc))
            continue
        vehicle = overview[0]

        rolling_stock = get_rolling_stock(num_parc)
        expected_doors = None
        if rolling_stock and rolling_stock["door_count"] is not None:
            expected_doors = list(range(1, rolling_stock["door_count"] + 1))
        minimum_doors = rolling_stock["minimum_doors"] if rolling_stock else 0

        exploitation_case, last_exploitation = get_exploitation_case(metrics_v)
        reference_time = last_exploitation or utc_now()

        doors = get_door_status_for_vehicle(
            door_v, num_parc, reference_time,
            expected_doors=expected_doors, minimum_doors=minimum_doors,
            skip_silence_check=(exploitation_case == "stale"),
        )
        status = "anomalie" if any(d["status"] == "anomalie" for d in doors) else vehicle["status"]

        if status == "anomalie":
            lingering.append(int(num_parc))

    return {"vehicles": lingering}
