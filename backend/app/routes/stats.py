"""
API routes for the "Statistiques" tab.

Sections 1, 2, 3, 5 (état du parc, répartition par type, nouvelles
anomalies, durée moyenne) are computed entirely client-side from data
already loaded by the Vue globale / SAE-GPS tabs - no dedicated backend
route needed for those.

Section 4 ("anomalies qui traînent") is the exception: it needs a wider
60-day lookback (vs the standard 30-day floor used everywhere else) to
re-verify ambiguous vehicles and to catch vehicles that vanished from the
30-day window entirely - hence this dedicated endpoint.
"""

from fastapi import APIRouter
from app.database import fetch_metrics, fetch_metrics_for_vehicle, fetch_door_counts_for_vehicle, utc_now
from app.anomaly import (
    get_vehicle_overview, get_exploitation_case_per_vehicle, get_exploitation_case,
    get_door_status_for_vehicle,
)
from app.fleet_reference import get_rolling_stock, is_known_vehicle

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Extended lookback used only for re-verifying "qui traînent" candidates -
# NOT the standard HISTORY_LOOKBACK_DAYS (30d) used everywhere else.
EXTENDED_LOOKBACK_DAYS = 60


@router.get("/lingering")
def get_lingering_anomalies():
    """
    "Anomalies qui traînent" (stats section 4): vehicles whose exploitation
    reference is unclear in the standard 30-day window ("stale"/"unknown" -
    shown as "Depuis plus de 30 jours" or "Aucune donnée" in the vue
    globale), PLUS vehicles that have vanished entirely from that 30-day
    window but were still reporting 30-60 days ago (silent for over a
    month, invisible in the normal fleet listing).

    For each candidate, we re-check with a wider 60-day window and
    recompute its real status - a vehicle that turns out "fonctionnel"
    once we look further back is excluded (false-positive avoidance, per
    business decision).
    """
    # Step 1: candidates from the standard 30-day window whose exploitation
    # reference is ambiguous.
    metrics_30 = fetch_metrics()
    primary_overview = [v for v in get_vehicle_overview(metrics_30) if is_known_vehicle(v["num_parc"])]
    primary_num_parcs = {v["num_parc"] for v in primary_overview}

    exploitation_case_map = get_exploitation_case_per_vehicle(metrics_30)
    candidates = {
        num_parc for num_parc, (case, _) in exploitation_case_map.items()
        if case in ("stale", "unknown") and num_parc in primary_num_parcs
    }

    # Step 2: vehicles present in the 30-60 day window but absent from the
    # primary 30-day one - silent for over a month, invisible otherwise.
    metrics_60 = fetch_metrics(days_back=EXTENDED_LOOKBACK_DAYS)
    all_known_60 = {n for n in metrics_60["num_parc"].dropna().unique() if is_known_vehicle(n)}
    invisible = all_known_60 - primary_num_parcs

    all_candidates = candidates | invisible

    # Step 3: re-verify each candidate against the wider 60-day window.
    # num_parc values here come from pandas (.unique() / dict keys built
    # from a DataFrame) and are numpy.int64, not native Python int -
    # psycopg2 can't adapt that type directly as a SQL query parameter, so
    # every candidate is cast to a plain int before being used.
    lingering = []
    for num_parc in sorted(int(n) for n in all_candidates):
        metrics_v = fetch_metrics_for_vehicle(num_parc, max_days_back=EXTENDED_LOOKBACK_DAYS)
        door_v = fetch_door_counts_for_vehicle(num_parc, max_days_back=EXTENDED_LOOKBACK_DAYS)

        overview = get_vehicle_overview(metrics_v)
        if not overview:
            # Genuinely no data at all, even over 60 days - lingering for sure.
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
        # else: turned out fonctionnel with the wider window - excluded.

    return {"vehicles": lingering}
