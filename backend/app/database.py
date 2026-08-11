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
from pathlib import Path

from app.config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, USE_SAMPLE_DATA,
    HISTORY_LOOKBACK_DAYS,
)

SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

DOOR_COLUMN_PATTERN = re.compile(r"^P(\d+)_(IN|OUT)$", re.IGNORECASE)


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
    Fetch recent rows from the "metrics" table.
    Only non-sensitive fields are selected (no total_in / total_out).

    Per the CDC, a row's timestamp can come from either date_wb/heure_wb
    (direct Webreathe report) OR date_sae/heure_sae (SAE-side report) -
    the two are not always both populated. We read both and fall back to
    whichever is present, so a vehicle reporting only via SAE is not
    wrongly flagged as silent.
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

    ts_wb = pd.to_datetime(
        df["date_wb"].astype(str) + " " + df["heure_wb"].astype(str),
        errors="coerce",
    )
    ts_sae = pd.to_datetime(
        df["date_sae"].astype(str) + " " + df["heure_sae"].astype(str),
        errors="coerce",
    )
    # Use the WB timestamp when present, otherwise fall back to the SAE one.
    df["timestamp"] = ts_wb.combine_first(ts_sae)
    return df


def fetch_door_counts(days_back: int = HISTORY_LOOKBACK_DAYS) -> pd.DataFrame:
    """
    Fetch recent rows from the "door_counts" table.
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

    df["timestamp"] = pd.to_datetime(
        df["date_wb"].astype(str) + " " + df["heure_wb"].astype(str),
        errors="coerce",
    )
    return df


def get_door_columns(door_counts_df: pd.DataFrame):
    """Public helper so other modules can find which door numbers exist in the data."""
    return _detect_door_columns(door_counts_df.columns)
