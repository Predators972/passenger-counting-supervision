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

# How far back to look for data. Per business decision, anything older than
# 2 months is not useful for maintenance purposes (we care about the current
# state and how long a vehicle/door has been in it, not deep history).
HISTORY_LOOKBACK_DAYS = 60

# --- Anomaly detection thresholds ---
# Section 4.1 of the CDC: a vehicle is flagged when it hasn't reported any data
# for more than this many hours. Fixed at 2 days for now (per business decision).
VEHICLE_ANOMALY_THRESHOLD_HOURS = 48

# Section 4.2 of the CDC: a door is flagged when it hasn't reported data for
# more than this many hours, while other doors on the same vehicle are still
# reporting more recent data.
DOOR_ANOMALY_THRESHOLD_HOURS = 48
