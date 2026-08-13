"""
Data access layer.

Reads from the real BDD3 PostgreSQL database (tables "metrics" and "door_counts")
when USE_SAMPLE_DATA is false, or from local sample CSV files otherwise.

IMPORTANT (per CDC section 2 - "Source de données"):
Passenger volume fields (PX_IN, PX_OUT, total_in, total_out) are read here
only when needed to compute a "last seen" timestamp per door, but must NEVER
be returned to the API / displayed in the tool. See anomaly.py and routes/.
"""

import re
import psycopg2
import psycopg2.extras
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, USE_SAMPLE_DATA,
    HISTORY_LOOKBACK_DAYS,
)

SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

DOOR_COLUMN_PATTERN = re.compile(r"^P(\d+)_(IN|OUT)$", re.IGNORECASE)

PARIS_TZ = ZoneInfo("Europe/Paris")


def _combine_date_time(df: pd.DataFrame, date_col: str, time_col: str) -> pd.Series:
    """
    Combine a DATE column and a TIME column into a single datetime Series
    (both already in UTC, e.g. date_wb/heure_wb).

    This is much faster than concatenating them into a string and parsing
    with pd.to_datetime(errors="coerce"), which falls back to a slow
    row-by-row dateutil parse whenever the format can't be inferred
    (e.g. because some rows have missing/empty values).
    """
    dates = pd.to_datetime(df[date_col], errors="coerce")
    times = pd.to_timedelta(df[time_col].astype(str), errors="coerce")
    return dates + times


def _combine_date_time_sae_to_utc(df: pd.DataFrame, date_col: str, time_col: str) -> pd.Series:
    """
    Same as _combine_date_time, but for date_sae/heure_sae specifically:
    per the BDD3 doc, heure_sae is LOCAL French time (UTC+1 in winter,
    UTC+2 in summer/DST), unlike heure_wb which is plain UTC. Without this
    conversion, timestamps derived from the SAE fallback would be off by
    1-2 hours compared to WB-based ones, silently corrupting duration
    calculations for any row relying on the SAE fallback.
    """
    naive_local = _combine_date_time(df, date_col, time_col)
    localized = naive_local.dt.tz_localize(PARIS_TZ, ambiguous="NaT", nonexistent="NaT")
    return localized.dt.tz_convert("UTC").dt.tz_localize(None)


def _lookback_start(since=None) -> datetime:
    """
    Resolve the actual start date to query from.
    "since" narrows the window when the caller already knows a more recent
    reference point (e.g. the vehicle's last known report from the global
    view) - but we never look back further than HISTORY_LOOKBACK_DAYS,
    which is the hard floor.
    """
    floor = datetime.now() - timedelta(days=HISTORY_LOOKBACK_DAYS)
    if since is not None and since > floor:
        return since
    return floor


def get_connection():
    """Open a new connection to BDD3. Caller is responsible for closing it."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def _detect_door_columns(columns):
    """
    Given a list of column names from door_counts, return the sorted list of
    door numbers found (e.g. [1, 2, 3, 4]) based on PX_IN / PX_OUT columns.
    This avoids hardcoding the number of doors, since it can differ between
    buses and trams.
    """
    doors = set()
    for col in columns:
        match = DOOR_COLUMN_PATTERN.match(col)
        if match:
            doors.add(int(match.group(1)))
    return sorted(doors)


def fetch_metrics(days_back: int = HISTORY_LOOKBACK_DAYS) -> pd.DataFrame:
    """
    Fetch recent rows from the "metrics" table, for the whole fleet.
    Only non-sensitive fields are selected (no total_in / total_out).
    Used by the global fleet view (CDC 3.1) only - for a single vehicle,
    use fetch_metrics_for_vehicle instead (much faster).
    """
    if USE_SAMPLE_DATA:
        df = pd.read_csv(SAMPLE_DATA_DIR / "sample_metrics.csv")
        for col in ("date_sae", "heure_sae"):
            if col not in df.columns:
                df[col] = None
    else:
        query = """
            SELECT
                COALESCE(num_parc_wb, num_parc_sae) AS num_parc,
                operation_state,
                latitude,
                longitude,
                date_wb,
                heure_wb,
                date_sae,
                heure_sae
            FROM metrics
            WHERE date_wb >= CURRENT_DATE - INTERVAL '%s days'
               OR date_sae >= CURRENT_DATE - INTERVAL '%s days'
        """
        with get_connection() as conn:
            df = pd.read_sql(query % (days_back, days_back), conn)

    ts_wb = _combine_date_time(df, "date_wb", "heure_wb")
    ts_sae = _combine_date_time_sae_to_utc(df, "date_sae", "heure_sae")
    # Use the WB timestamp when present, otherwise fall back to the SAE one.
    df["timestamp"] = ts_wb.combine_first(ts_sae)
    return df


def fetch_metrics_for_vehicle(num_parc, since=None) -> pd.DataFrame:
    """
    Fetch "metrics" rows for a single vehicle only - filtered directly in
    SQL. Used by the vehicle detail and live-check endpoints so they don't
    have to re-fetch the whole fleet's metrics just to read one vehicle's
    last report time.
    """
    start_date = _lookback_start(since)

    if USE_SAMPLE_DATA:
        df = pd.read_csv(SAMPLE_DATA_DIR / "sample_metrics.csv")
        df = df[df["num_parc"] == int(num_parc)]
        for col in ("date_sae", "heure_sae"):
            if col not in df.columns:
                df[col] = None
    else:
        query = """
            SELECT
                COALESCE(num_parc_wb, num_parc_sae) AS num_parc,
                operation_state,
                latitude,
                longitude,
                date_wb,
                heure_wb,
                date_sae,
                heure_sae
            FROM metrics
            WHERE (num_parc_wb = %s OR num_parc_sae = %s)
              AND (date_wb >= %s OR date_sae >= %s)
        """
        params = (num_parc, num_parc, start_date.date(), start_date.date())
        with get_connection() as conn:
            df = pd.read_sql(query, conn, params=params)

    ts_wb = _combine_date_time(df, "date_wb", "heure_wb")
    ts_sae = _combine_date_time(df, "date_sae", "heure_sae")
    df["timestamp"] = ts_wb.combine_first(ts_sae)
    return df


def fetch_door_counts(days_back: int = HISTORY_LOOKBACK_DAYS) -> pd.DataFrame:
    """
    Fetch recent rows from the "door_counts" table, for the whole fleet.
    Passenger volumes (PX_IN / PX_OUT) are only used internally to determine
    the last-seen timestamp per door - they are dropped before being returned
    by the API (see anomaly.get_door_status_for_vehicle).
    """
    if USE_SAMPLE_DATA:
        df = pd.read_csv(SAMPLE_DATA_DIR / "sample_door_counts.csv")
    else:
        query = """
            SELECT *
            FROM door_counts
            WHERE date_wb >= CURRENT_DATE - INTERVAL '%s days'
        """
        with get_connection() as conn:
            df = pd.read_sql(query % days_back, conn)
        df = df.rename(columns={"num_parc_wb": "num_parc"})

    df["timestamp"] = _combine_date_time(df, "date_wb", "heure_wb")
    return df


def fetch_door_counts_for_vehicle(num_parc, since=None) -> pd.DataFrame:
    """
    Fetch door_counts rows for a single vehicle only.

    Used by the vehicle detail, live-check and history endpoints. Filtering
    directly in SQL (instead of fetching the whole fleet and filtering with
    pandas) keeps these fast, since door_counts holds every vehicle's data
    with up to 16 doors x 2 columns each.

    "since" lets the caller narrow the window further when a more recent
    reference point is already known (see routes/vehicles.py) - bounded by
    HISTORY_LOOKBACK_DAYS as a hard floor either way.
    """
    start_date = _lookback_start(since)

    if USE_SAMPLE_DATA:
        df = pd.read_csv(SAMPLE_DATA_DIR / "sample_door_counts.csv")
        df = df[df["num_parc"] == int(num_parc)]
    else:
        query = """
            SELECT *
            FROM door_counts
            WHERE date_wb >= %s
              AND num_parc_wb = %s
        """
        with get_connection() as conn:
            df = pd.read_sql(query, conn, params=(start_date.date(), num_parc))
        df = df.rename(columns={"num_parc_wb": "num_parc"})

    df["timestamp"] = _combine_date_time(df, "date_wb", "heure_wb")
    return df


def get_door_columns(door_counts_df: pd.DataFrame):
    """Public helper so other modules can find which door numbers exist in the data."""
    return _detect_door_columns(door_counts_df.columns)


def fetch_door_last_seen_aggregate(days_back: int = HISTORY_LOOKBACK_DAYS) -> pd.DataFrame:
    """
    For the whole fleet, compute the last-seen timestamp of EACH door
    directly in SQL (one aggregate query, GROUP BY vehicle) instead of
    fetching every raw row like fetch_door_counts does.

    This lets the global view (CDC 3.1) also catch door-level anomalies
    (CDC 4.2) - e.g. one dead door out of sixteen - without paying the cost
    of a full door_counts fetch for the entire fleet.

    Returns one row per vehicle with columns num_parc, p1_last .. p16_last.
    """
    if USE_SAMPLE_DATA:
        df = pd.read_csv(SAMPLE_DATA_DIR / "sample_door_counts.csv")
        df["ts"] = _combine_date_time(df, "date_wb", "heure_wb")
        rows = []
        for num_parc, group in df.groupby("num_parc"):
            row = {"num_parc": num_parc}
            for n in range(1, 17):
                cols = [c for c in (f"P{n}_IN", f"P{n}_OUT") if c in group.columns]
                if cols:
                    mask = group[cols].notna().any(axis=1)
                    row[f"p{n}_last"] = group.loc[mask, "ts"].max() if mask.any() else pd.NaT
                else:
                    row[f"p{n}_last"] = pd.NaT
            rows.append(row)
        return pd.DataFrame(rows)

    door_case_exprs = [
        f'MAX(CASE WHEN "P{n}_IN" IS NOT NULL OR "P{n}_OUT" IS NOT NULL '
        f'THEN date_wb + heure_wb END) AS p{n}_last'
        for n in range(1, 17)
    ]
    query = f"""
        SELECT num_parc_wb AS num_parc, {', '.join(door_case_exprs)}
        FROM door_counts
        WHERE date_wb >= CURRENT_DATE - INTERVAL '{days_back} days'
        GROUP BY num_parc_wb
    """
    with get_connection() as conn:
        df = pd.read_sql(query, conn)
    return df
