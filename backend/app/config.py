## @file config.py
#  @brief Central configuration: database credentials loaded from the
#  environment, and all tunable thresholds used by the anomaly detection
#  logic.

import os
from dotenv import load_dotenv

load_dotenv()

## Hostname or IP address of the PostgreSQL server.
DB_HOST = os.getenv("DB_HOST")
## PostgreSQL port.
DB_PORT = os.getenv("DB_PORT", "5432")
## Name of the database to connect to.
DB_NAME = os.getenv("DB_NAME")
## Database user name.
DB_USER = os.getenv("DB_USER")
## Database user password.
DB_PASSWORD = os.getenv("DB_PASSWORD")

## If true, data access functions read from local sample CSV files instead
#  of opening a real database connection.
USE_SAMPLE_DATA = os.getenv("USE_SAMPLE_DATA", "true").lower() == "true"

## Number of days to look back when querying "metrics" and "door_counts",
#  used both to bound anomaly-detection windows and as the maximum
#  browsable range for the history feature.
HISTORY_LOOKBACK_DAYS = 30

## Set of operation_state values that represent a vehicle genuinely in
#  active service (1 = commercial service, 2 = deadhead run). Used to find
#  the last time a vehicle was actually in service, as opposed to a report
#  received while idle at the depot.
EXPLOITATION_STATES = {1, 2}

## Set of operation_state values that do not reliably prove the SAE trame
#  is being received (0 = SAEIV unavailable). Rows with one of these values
#  are ignored when checking whether a vehicle has ever reported a
#  meaningful operation_state.
UNRELIABLE_OPERATION_STATES = {0}

## Number of hours without any report before a vehicle is flagged as an
#  anomaly.
VEHICLE_ANOMALY_THRESHOLD_HOURS = 48

## Number of hours without a report from a specific door before that door
#  is flagged as an anomaly.
DOOR_ANOMALY_THRESHOLD_HOURS = 48

## Number of hours without an SAE trame before the SAE channel is flagged
#  as silent.
SAE_SILENCE_THRESHOLD_HOURS = 48
## Maximum acceptable share (0-1) of reports missing the SAE trame over
#  HISTORY_LOOKBACK_DAYS before it is flagged as degraded.
SAE_MISSING_RATIO_THRESHOLD = 0.10

## Number of hours without a GPS position before the GPS channel is
#  flagged as silent.
GPS_SILENCE_THRESHOLD_HOURS = 48
## Maximum acceptable share (0-1) of reports missing a GPS position over
#  HISTORY_LOOKBACK_DAYS before it is flagged as degraded.
GPS_MISSING_RATIO_THRESHOLD = 0.25
