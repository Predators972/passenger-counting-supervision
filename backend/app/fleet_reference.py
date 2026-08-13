"""
Rolling stock reference data.

Maps a vehicle number (num_parc) to its rolling stock type (tramway model,
bus type...) and its expected number of doors, based on number ranges
provided by the maintenance team.

Edit data/rolling_stock_ranges.json to add or correct ranges - no code
change needed. If a vehicle isn't found in any range, the door count is
guessed dynamically from the data instead (see anomaly.py) - less reliable,
since a door that has been silent for the whole lookback window then looks
identical to a door that simply doesn't exist on that vehicle.
"""

import json
from pathlib import Path

REFERENCE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "rolling_stock_ranges.json"

_ranges_cache = None

# Mapping from internal door index (P1_IN...P16_IN as they exist in door_counts)
# to the REAL physical door number, per BDD3 documentation (08/12/2025).
# P1-P6 are the same "porte 11-16" numbering for all tramways. P7-P16 differ
# between the CITADIS 302 (shorter, 12 doors total) and the CITADIS 401/402 /
# CAF URBOS (longer, 16 doors total). Buses just use their door index directly.
DOOR_SCHEMES = {
    "bus": {1: 1, 2: 2, 3: 3},
    "tram_302": {
        1: 11, 2: 12, 3: 13, 4: 14, 5: 15, 6: 16,
        7: 26, 8: 25, 9: 24, 10: 23, 11: 22, 12: 21,
    },
    "tram_401_402_urbos": {
        1: 11, 2: 12, 3: 13, 4: 14, 5: 15, 6: 16,
        7: 31, 8: 32, 9: 33, 10: 34, 11: 26, 12: 25, 13: 24, 14: 23, 15: 22, 16: 21,
    },
}


def get_physical_door_number(scheme_name, door_index):
    """Translate an internal door index (1-16) into the real physical door number."""
    scheme = DOOR_SCHEMES.get(scheme_name)
    if not scheme:
        return door_index
    return scheme.get(door_index, door_index)


def _load_ranges():
    if not REFERENCE_FILE.exists():
        return []
    with open(REFERENCE_FILE, encoding="utf-8") as f:
        return json.load(f)


def get_rolling_stock(num_parc):
    """
    Return {"type": str, "door_count": int | None, "door_scheme": str | None}
    for the given vehicle number, or None if no matching range is configured
    OR if num_parc is invalid/unreadable (e.g. NaN from a row with missing
    num_parc_wb/num_parc_sae in BDD3) - a bad value here must not crash the
    whole fleet listing, just exclude that one vehicle.
    door_count can be None (e.g. buses, whose door count varies by model even
    within the same energy type) - in that case callers should fall back to
    dynamic door-count detection instead of a fixed expected count.
    """
    global _ranges_cache
    if _ranges_cache is None:
        _ranges_cache = _load_ranges()

    try:
        num_parc = int(num_parc)
    except (ValueError, TypeError):
        return None

    for entry in _ranges_cache:
        if entry["range_start"] <= num_parc <= entry["range_end"]:
            return {
                "type": entry["type"],
                "door_count": entry.get("door_count"),
                "door_scheme": entry.get("door_scheme"),
            }
    return None


def is_known_vehicle(num_parc) -> bool:
    """
    True if num_parc falls within one of the configured rolling stock
    ranges. Vehicle numbers outside all known ranges are treated as bad
    data (a BDD3 data quality issue) and excluded from the tool entirely,
    per business decision.
    """
    return get_rolling_stock(num_parc) is not None
