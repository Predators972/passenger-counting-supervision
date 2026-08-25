"""
Application configuration.
Loads database credentials and settings from a local .env file.
Never hardcode credentials directly in the source code.
"""

import os
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# When true, the app reads from local sample CSV files instead of the real database.
# Useful for development before the DSI validates the application's DB access.
USE_SAMPLE_DATA = os.getenv("USE_SAMPLE_DATA", "true").lower() == "true"

# How far back to look for data. Unified to 30 days everywhere (business
# decision) - both for anomaly detection windows and for the history
# feature's maximum browsable range.
HISTORY_LOOKBACK_DAYS = 30

# operation_state values (per BDD3 doc) that count as the vehicle genuinely
# being in active service. Used to find the last time a vehicle was truly
# in exploitation, as opposed to noise picked up while idle at the depot
# (e.g. a maintainer walking past a door sensor triggers just that one door).
EXPLOITATION_STATES = {1, 2}  # 1 = En service commercial, 2 = En HLP

# operation_state = 0 means "SAEIV indisponible" - the SAE system itself was
# reporting as unavailable on that row, so its presence there doesn't prove
# the SAE trame is reliably received. A row with operation_state = 0 must
# NOT count as evidence of a genuine "stale" exploitation case (see
# anomaly.get_exploitation_case) - only other non-null values do.
UNRELIABLE_OPERATION_STATES = {0}

# --- Anomaly detection thresholds ---
# Section 4.1 of the CDC: a vehicle is flagged when it hasn't reported any data
# for more than this many hours. Fixed at 2 days for now (per business decision).
VEHICLE_ANOMALY_THRESHOLD_HOURS = 48

# Section 4.2 of the CDC: a door is flagged when it hasn't reported data for
# more than this many hours, while other doors on the same vehicle are still
# reporting more recent data.
DOOR_ANOMALY_THRESHOLD_HOURS = 48

# Section 4.3 (SAE) / 4.4 (GPS) of the CDC. Two combined triggers:
# - a silence duration (in hours), measured since the vehicle's own last
#   communication (not wall-clock "now" - see anomaly.get_sae_gps_status)
# - a "degraded mode" ratio: share of reports over HISTORY_LOOKBACK_DAYS
#   missing the field, even if it's not a total cutoff.
# SAE should almost always be present as soon as a vehicle communicates at
# all, so its tolerance is low. GPS can legitimately be blocked at some
# stops depending on surrounding buildings, so its tolerance is higher.
# These are business decisions with no confirmed real-world reference yet -
# easy to tune here once more data/feedback is available.
SAE_SILENCE_THRESHOLD_HOURS = 48
SAE_MISSING_RATIO_THRESHOLD = 0.10  # >10% of reports missing SAE = anomalie

GPS_SILENCE_THRESHOLD_HOURS = 48
GPS_MISSING_RATIO_THRESHOLD = 0.25  # >25% of reports missing GPS = anomalie
