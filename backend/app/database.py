## @file database.py
#  @brief Data access layer: opens the PostgreSQL connection, builds and runs
#  every SQL query against the "metrics" and "door_counts" tables, and
#  converts the results into pandas DataFrames with a unified "timestamp"
#  column. Falls back to local sample CSV files when USE_SAMPLE_DATA is set.

import re
import psycopg2
import psycopg2.extras
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, USE_SAMPLE_DATA,
    HISTORY_LOOKBACK_DAYS,
)

## Directory containing the sample CSV files used when USE_SAMPLE_DATA is true.
SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

## Regex matching a door_counts column name such as "P3_IN" or "P12_OUT".
DOOR_COLUMN_PATTERN = re.compile(r"^P(\d+)_(IN|OUT)$", re.IGNORECASE)

## Time zone used to convert UTC timestamps to local time for display.
PARIS_TZ = ZoneInfo("Europe/Paris")


def utc_now() -> datetime:
    """!
    @brief Return the current time in UTC as a naive datetime (no tzinfo).

    @return Current UTC time, matching the convention used by every
    "timestamp" column derived from date_wb/heure_wb.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local_iso(ts):
    """!
    @brief Convert a naive UTC timestamp into an Europe/Paris local-time ISO
    8601 string, for display in API responses.

    @param ts A naive UTC timestamp (datetime, pandas Timestamp, or None).
    @return ISO 8601 string in local time with no UTC offset, or None if
    ts is None or NaN.
    """
    if ts is None:
        return None
    try:
        if pd.isna(ts):
            return None
    except (TypeError, ValueError):
        pass
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC").tz_convert(PARIS_TZ).tz_localize(None).isoformat()


def _combine_date_time(df: pd.DataFrame, date_col: str, time_col: str) -> pd.Series:
    """!
    @brief Combine a DATE column and a TIME column into a single datetime
    Series, assuming both are already expressed in UTC.

    @param df DataFrame containing the two columns.
    @param date_col Name of the date column.
    @param time_col Name of the time column.
    @return A pandas Series of combined datetime values.
    """
    dates = pd.to_datetime(df[date_col], errors="coerce")
    times = pd.to_timedelta(df[time_col].astype(str), errors="coerce")
    return dates + times


def _combine_date_time_sae_to_utc(df: pd.DataFrame, date_col: str, time_col: str) -> pd.Series:
    """!
    @brief Combine a DATE column and a TIME column expressed in local French
    time (as used by date_sae/heure_sae) and convert the result to UTC.

    @param df DataFrame containing the two columns.
    @param date_col Name of the date column (local time).
    @param time_col Name of the time column (local time).
    @return A pandas Series of combined datetime values, converted to UTC.
    """
    naive_local = _combine_date_time(df, date_col, time_col)
    localized = naive_local.dt.tz_localize(PARIS_TZ, ambiguous="NaT", nonexistent="NaT")
    return localized.dt.tz_convert("UTC").dt.tz_localize(None)


def _lookback_start(since=None) -> datetime:
    """!
    @brief Resolve the start date to use in a query's date range.

    @param since Optional datetime; if provided and more recent than
    HISTORY_LOOKBACK_DAYS ago, it narrows the window.
    @return The datetime to use as the lower bound of the query.
    """
    floor = utc_now() - timedelta(days=HISTORY_LOOKBACK_DAYS)
    if since is not None and since > floor:
        return since
    return floor


def get_connection():
    """!
    @brief Open a new connection to the database.

    @return A psycopg2 connection object. The caller is responsible for
    closing it.
    """
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def _detect_door_columns(columns):
    """!
    @brief Extract the sorted list of door numbers present in a list of
    column names, based on the "PX_IN" / "PX_OUT" naming pattern.

    @param columns Iterable of column names.
    @return Sorted list of integer door numbers found.
    """
    doors = set()
    for col in columns:
        match = DOOR_COLUMN_PATTERN.match(col)
        if match:
            doors.add(int(match.group(1)))
    return sorted(doors)


def fetch_metrics(days_back: int = HISTORY_LOOKBACK_DAYS) -> pd.DataFrame:
    """!
    @brief Fetch recent rows from the "metrics" table for the whole fleet.

    Selects only fields needed for vehicle-level status and exploitation
    history (no passenger volume totals). Adds a combined "timestamp"
    column, preferring the WB source and falling back to SAE.

    @param days_back Number of days to look back.
    @return DataFrame with one row per metrics report, whole fleet.
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
    df["timestamp"] = ts_wb.combine_first(ts_sae)
    return df


def fetch_metrics_sae_gps(days_back: int = HISTORY_LOOKBACK_DAYS) -> pd.DataFrame:
    """!
    @brief Fetch recent "metrics" rows for the whole fleet, including
    num_parc_sae, latitude, longitude and operation_state.

    @param days_back Number of days to look back.
    @return DataFrame with one row per metrics report, whole fleet,
    including the fields needed for SAE/GPS anomaly detection.
    """
    if USE_SAMPLE_DATA:
        df = pd.read_csv(SAMPLE_DATA_DIR / "sample_metrics.csv")
        for col in ("date_sae", "heure_sae", "num_parc_sae", "latitude", "longitude", "operation_state"):
            if col not in df.columns:
                df[col] = None
    else:
        query = """
            SELECT
                COALESCE(num_parc_wb, num_parc_sae) AS num_parc,
                num_parc_sae,
                latitude,
                longitude,
                operation_state,
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
    df["timestamp"] = ts_wb.combine_first(ts_sae)
    return df


def fetch_metrics_sae_gps_for_vehicle(num_parc, since=None) -> pd.DataFrame:
    """!
    @brief Fetch "metrics" rows for a single vehicle, including num_parc_sae,
    latitude and longitude, filtered directly in SQL.

    @param num_parc Vehicle number to filter on.
    @param since Optional datetime narrowing the query window.
    @return DataFrame with one row per metrics report for that vehicle.
    """
    start_date = _lookback_start(since)

    if USE_SAMPLE_DATA:
        df = pd.read_csv(SAMPLE_DATA_DIR / "sample_metrics.csv")
        df = df[df["num_parc"] == int(num_parc)]
        for col in ("date_sae", "heure_sae", "num_parc_sae", "latitude", "longitude"):
            if col not in df.columns:
                df[col] = None
    else:
        query = """
            SELECT
                COALESCE(num_parc_wb, num_parc_sae) AS num_parc,
                num_parc_sae,
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
    ts_sae = _combine_date_time_sae_to_utc(df, "date_sae", "heure_sae")
    df["timestamp"] = ts_wb.combine_first(ts_sae)
    return df


def fetch_metrics_for_vehicle(num_parc, since=None) -> pd.DataFrame:
    """!
    @brief Fetch "metrics" rows for a single vehicle, filtered directly in
    SQL.

    @param num_parc Vehicle number to filter on.
    @param since Optional datetime narrowing the query window.
    @return DataFrame with one row per metrics report for that vehicle.
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
    ts_sae = _combine_date_time_sae_to_utc(df, "date_sae", "heure_sae")
    df["timestamp"] = ts_wb.combine_first(ts_sae)
    return df


## Explicit list of columns selected from door_counts (avoids SELECT *).
DOOR_COUNTS_COLUMNS = [
    "num_parc_wb", "date_wb", "heure_wb",
    *[f'"P{n}_{d}"' for n in range(1, 17) for d in ("IN", "OUT")],
]


def fetch_door_counts_for_vehicle(num_parc, since=None) -> pd.DataFrame:
    """!
    @brief Fetch door_counts rows for a single vehicle, filtered directly in
    SQL.

    @param num_parc Vehicle number to filter on.
    @param since Optional datetime narrowing the query window.
    @return DataFrame with one row per door_counts report for that vehicle,
    including a "timestamp" column and one pair of PX_IN/PX_OUT columns
    per door.
    """
    start_date = _lookback_start(since)

    if USE_SAMPLE_DATA:
        df = pd.read_csv(SAMPLE_DATA_DIR / "sample_door_counts.csv")
        df = df[df["num_parc"] == int(num_parc)]
    else:
        query = f"""
            SELECT {', '.join(DOOR_COUNTS_COLUMNS)}
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
    """!
    @brief Public wrapper exposing the door numbers present in a
    door_counts DataFrame.

    @param door_counts_df DataFrame with door_counts-style columns.
    @return Sorted list of integer door numbers found.
    """
    return _detect_door_columns(door_counts_df.columns)


def fetch_door_last_seen_aggregate(days_back: int = HISTORY_LOOKBACK_DAYS) -> pd.DataFrame:
    """!
    @brief Compute the last-seen timestamp of each door for the whole fleet
    in a single aggregate SQL query (GROUP BY vehicle).

    @param days_back Number of days to look back.
    @return DataFrame with one row per vehicle and columns num_parc,
    p1_last .. p16_last. A NULL column means either the door hasn't
    reported within days_back, or it doesn't exist on that vehicle.
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

    door_filter_exprs = [
        f'MAX(ts) FILTER (WHERE "P{n}_IN" IS NOT NULL OR "P{n}_OUT" IS NOT NULL) AS p{n}_last'
        for n in range(1, 17)
    ]
    door_cols = ", ".join(f'"P{n}_IN", "P{n}_OUT"' for n in range(1, 17))
    query = f"""
        WITH base AS (
            SELECT
                num_parc_wb,
                date_wb + heure_wb AS ts,
                {door_cols}
            FROM door_counts
            WHERE date_wb >= CURRENT_DATE - INTERVAL '{days_back} days'
        )
        SELECT num_parc_wb AS num_parc, {', '.join(door_filter_exprs)}
        FROM base
        GROUP BY num_parc_wb
    """
    with get_connection() as conn:
        df = pd.read_sql(query, conn)
    return df
